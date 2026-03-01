"""Workspace transfer helpers for moving ApplyPilot between computers."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
import zipfile


BASE_FILES = (
    "profile.json",
    "resume.txt",
    "resume.pdf",
    "searches.yaml",
    "applypilot.db",
    ".env",
)

GENERATED_DIRS = (
    "tailored_resumes",
    "cover_letters",
)

OPTIONAL_DIRS = ("logs",)

MANIFEST_NAME = "manifest.json"


def _iter_files_under(root: Path) -> list[Path]:
    files: list[Path] = []
    if not root.exists() or not root.is_dir():
        return files
    for p in sorted(root.rglob("*")):
        if p.is_file():
            files.append(p)
    return files


def _safe_archive_name(rel_path: Path) -> str:
    return rel_path.as_posix().lstrip("/")


def export_workspace(
    workspace: Path,
    archive_path: Path,
    *,
    include_env: bool = False,
    include_db: bool = True,
    include_generated: bool = False,
    include_logs: bool = False,
) -> dict[str, object]:
    """Export workspace files into a zip archive.

    Returns a summary payload with counts and included paths.
    """
    workspace = Path(workspace).expanduser()
    archive_path = Path(archive_path).expanduser()

    if not workspace.exists() or not workspace.is_dir():
        raise FileNotFoundError(f"Workspace does not exist: {workspace}")

    archive_path.parent.mkdir(parents=True, exist_ok=True)

    include_names: set[str] = {"profile.json", "resume.txt", "resume.pdf", "searches.yaml"}
    if include_db:
        include_names.add("applypilot.db")
    if include_env:
        include_names.add(".env")

    included: list[str] = []
    missing: list[str] = []

    with zipfile.ZipFile(archive_path, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name in BASE_FILES:
            if name not in include_names:
                continue
            p = workspace / name
            if not p.exists() or not p.is_file():
                missing.append(name)
                continue
            zf.write(p, arcname=name)
            included.append(name)

        if include_generated:
            for d in GENERATED_DIRS:
                root = workspace / d
                for p in _iter_files_under(root):
                    rel = p.relative_to(workspace)
                    arc = _safe_archive_name(rel)
                    zf.write(p, arcname=arc)
                    included.append(arc)

        if include_logs:
            for d in OPTIONAL_DIRS:
                root = workspace / d
                for p in _iter_files_under(root):
                    rel = p.relative_to(workspace)
                    arc = _safe_archive_name(rel)
                    zf.write(p, arcname=arc)
                    included.append(arc)

        manifest = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "workspace": str(workspace),
            "included": included,
            "missing": missing,
            "options": {
                "include_env": include_env,
                "include_db": include_db,
                "include_generated": include_generated,
                "include_logs": include_logs,
            },
        }
        zf.writestr(MANIFEST_NAME, json.dumps(manifest, indent=2))

    return {
        "archive": str(archive_path),
        "included": included,
        "missing": missing,
        "count": len(included),
    }


def import_workspace(archive_path: Path, workspace: Path, *, overwrite: bool = False) -> dict[str, object]:
    """Import selected workspace files from a transfer zip archive."""
    archive_path = Path(archive_path).expanduser()
    workspace = Path(workspace).expanduser()

    if not archive_path.exists() or not archive_path.is_file():
        raise FileNotFoundError(f"Archive not found: {archive_path}")

    workspace.mkdir(parents=True, exist_ok=True)
    ws_resolved = workspace.resolve()

    allowed_roots = set(BASE_FILES) | set(GENERATED_DIRS) | set(OPTIONAL_DIRS) | {MANIFEST_NAME}

    restored: list[str] = []
    skipped_exists: list[str] = []
    skipped_unsafe: list[str] = []
    skipped_unknown: list[str] = []

    with zipfile.ZipFile(archive_path, mode="r") as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue

            name = str(info.filename or "").replace("\\", "/").strip()
            if not name:
                continue

            parts = [p for p in name.split("/") if p]
            if not parts:
                continue

            if name == MANIFEST_NAME:
                continue

            # Prevent path traversal before allowlist checks.
            if any(p in ("..", ".") for p in parts):
                skipped_unsafe.append(name)
                continue

            root = parts[0]
            if root not in allowed_roots:
                skipped_unknown.append(name)
                continue

            rel_path = Path(*parts)
            dest = workspace / rel_path
            try:
                dest_parent = dest.parent.resolve()
                if ws_resolved != dest_parent and ws_resolved not in dest_parent.parents:
                    skipped_unsafe.append(name)
                    continue
            except Exception:
                skipped_unsafe.append(name)
                continue

            if dest.exists() and not overwrite:
                skipped_exists.append(name)
                continue

            dest.parent.mkdir(parents=True, exist_ok=True)
            data = zf.read(info)
            dest.write_bytes(data)
            restored.append(name)

    return {
        "archive": str(archive_path),
        "workspace": str(workspace),
        "restored": restored,
        "skipped_exists": skipped_exists,
        "skipped_unsafe": skipped_unsafe,
        "skipped_unknown": skipped_unknown,
        "count": len(restored),
    }
