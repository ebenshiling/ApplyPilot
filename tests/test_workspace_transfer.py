from __future__ import annotations

from pathlib import Path
import tempfile
import zipfile

from applypilot.workspace_transfer import export_workspace, import_workspace


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_workspace_export_and_import_roundtrip() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        src = root / "src"
        dst = root / "dst"
        archive = root / "transfer.zip"

        _write(src / "profile.json", '{"personal": {"full_name": "Jane Doe"}}')
        _write(src / "resume.txt", "Jane Doe\nSUMMARY\n...")
        _write(src / "searches.yaml", "queries:\n  - query: Data Analyst\n")
        _write(src / ".env", "GEMINI_API_KEY=secret\n")
        _write(src / "tailored_resumes" / "one.txt", "tailored")

        result = export_workspace(
            src,
            archive,
            include_env=True,
            include_db=False,
            include_generated=True,
            include_logs=False,
        )
        assert archive.exists()
        assert int(result["count"]) >= 4

        imported = import_workspace(archive, dst, overwrite=False)
        assert int(imported["count"]) >= 4
        assert (dst / "profile.json").exists()
        assert (dst / "resume.txt").exists()
        assert (dst / "searches.yaml").exists()
        assert (dst / ".env").exists()
        assert (dst / "tailored_resumes" / "one.txt").exists()


def test_workspace_import_skips_unsafe_and_unknown_entries() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        archive = root / "unsafe.zip"
        dst = root / "dst"

        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("profile.json", "{}")
            zf.writestr("../evil.txt", "bad")
            zf.writestr("random.txt", "ignored")

        out = import_workspace(archive, dst, overwrite=False)
        assert (dst / "profile.json").exists()
        assert "../evil.txt" in list(out["skipped_unsafe"])
        assert "random.txt" in list(out["skipped_unknown"])
        assert not (root / "evil.txt").exists()
