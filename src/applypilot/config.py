"""ApplyPilot configuration: paths, platform detection, user data.

By default, ApplyPilot stores user data under the user's home directory.

Directory selection order:
1) APPLYPILOT_DIR environment variable (explicit override)
2) ~/.applypilot (backwards compatible if it already contains data)
3) ~/.applypilot-data (preferred new default if it already contains data)
4) ~/.applypilot (fresh install default)
"""

import os
import platform
import shutil
from pathlib import Path


def _dir_has_user_data(p: Path) -> bool:
    """Heuristic: does this dir look like an ApplyPilot workspace?"""
    try:
        return any(
            (
                (p / ".env").exists(),
                (p / "applypilot.db").exists(),
                (p / "profile.json").exists(),
                (p / "searches.yaml").exists(),
                (p / "resume.txt").exists(),
            )
        )
    except Exception:
        return False


def _dir_completeness(p: Path) -> int:
    """Prefer the directory with more actual user config present."""
    score = 0
    try:
        if (p / ".env").exists():
            score += 3
        if (p / "profile.json").exists():
            score += 3
        if (p / "resume.txt").exists():
            score += 2
        if (p / "searches.yaml").exists():
            score += 2
        if (p / "applypilot.db").exists():
            score += 1
    except Exception:
        return 0
    return score


def _select_app_dir() -> Path:
    # Explicit override
    env = (os.environ.get("APPLYPILOT_DIR", "") or "").strip()
    if env:
        return Path(env).expanduser()

    home = Path.home()
    legacy = home / ".applypilot"
    preferred = home / ".applypilot-data"

    # Backwards compatible: if legacy already holds data, keep using it.
    legacy_has = _dir_has_user_data(legacy)
    preferred_has = _dir_has_user_data(preferred)

    if legacy_has and preferred_has:
        # If both exist, pick the more complete workspace (common when the DB
        # was created under ~/.applypilot but the config lives under
        # ~/.applypilot-data).
        return preferred if _dir_completeness(preferred) > _dir_completeness(legacy) else legacy

    if legacy_has:
        return legacy

    # If preferred holds data (common on Windows setups), use it.
    if preferred_has:
        return preferred

    # Fresh install: prefer the new workspace location.
    if not legacy.exists() and not preferred.exists():
        return preferred

    # If someone created the preferred dir (but it's empty), prefer it
    # when legacy doesn't exist yet.
    if preferred.exists() and not legacy.exists():
        return preferred

    return legacy


# User data directory — all user-specific files live here
APP_DIR = _select_app_dir()

# Core paths
DB_PATH = APP_DIR / "applypilot.db"
PROFILE_PATH = APP_DIR / "profile.json"
RESUME_PATH = APP_DIR / "resume.txt"
RESUME_PDF_PATH = APP_DIR / "resume.pdf"
SEARCH_CONFIG_PATH = APP_DIR / "searches.yaml"
ENV_PATH = APP_DIR / ".env"

# Generated output
TAILORED_DIR = APP_DIR / "tailored_resumes"
COVER_LETTER_DIR = APP_DIR / "cover_letters"
LOG_DIR = APP_DIR / "logs"

# Chrome worker isolation
CHROME_WORKER_DIR = APP_DIR / "chrome-workers"
APPLY_WORKER_DIR = APP_DIR / "apply-workers"

# Package-shipped config (YAML registries)
PACKAGE_DIR = Path(__file__).parent
CONFIG_DIR = PACKAGE_DIR / "config"


def get_chrome_path() -> str:
    """Auto-detect Chrome/Chromium executable path, cross-platform.

    Override with CHROME_PATH environment variable.
    """
    env_path = os.environ.get("CHROME_PATH")
    if env_path and Path(env_path).exists():
        return env_path

    system = platform.system()

    if system == "Windows":
        candidates = [
            Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Google/Chrome/Application/chrome.exe",
            Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))
            / "Google/Chrome/Application/chrome.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
        ]
    elif system == "Darwin":
        candidates = [
            Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            Path("/Applications/Chromium.app/Contents/MacOS/Chromium"),
        ]
    else:  # Linux
        candidates = []
        for name in ("google-chrome", "google-chrome-stable", "chromium-browser", "chromium"):
            found = shutil.which(name)
            if found:
                candidates.append(Path(found))

    for c in candidates:
        if c and c.exists():
            return str(c)

    # Fall back to PATH search
    for name in ("google-chrome", "google-chrome-stable", "chromium-browser", "chromium", "chrome"):
        found = shutil.which(name)
        if found:
            return found

    raise FileNotFoundError("Chrome/Chromium not found. Install Chrome or set CHROME_PATH environment variable.")


def get_chrome_user_data() -> Path:
    """Default Chrome user data directory, cross-platform."""
    system = platform.system()
    if system == "Windows":
        return Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "User Data"
    elif system == "Darwin":
        return Path.home() / "Library" / "Application Support" / "Google" / "Chrome"
    else:
        return Path.home() / ".config" / "google-chrome"


def ensure_dirs():
    """Create all required directories."""
    for d in [APP_DIR, TAILORED_DIR, COVER_LETTER_DIR, LOG_DIR, CHROME_WORKER_DIR, APPLY_WORKER_DIR]:
        d.mkdir(parents=True, exist_ok=True)


def load_profile() -> dict:
    """Load user profile from ~/.applypilot/profile.json."""
    import json

    if not PROFILE_PATH.exists():
        raise FileNotFoundError(f"Profile not found at {PROFILE_PATH}. Run `applypilot init` first.")
    return json.loads(PROFILE_PATH.read_text(encoding="utf-8"))


def load_search_config() -> dict:
    """Load search configuration from ~/.applypilot/searches.yaml."""
    import yaml

    # Optional override: allow per-run / per-session search config.
    override = (os.environ.get("APPLYPILOT_SEARCHES_PATH", "") or "").strip()
    if override:
        p = Path(override).expanduser()
        if p.exists():
            return yaml.safe_load(p.read_text(encoding="utf-8")) or {}

    if not SEARCH_CONFIG_PATH.exists():
        # Fall back to package-shipped example
        example = CONFIG_DIR / "searches.example.yaml"
        if example.exists():
            return yaml.safe_load(example.read_text(encoding="utf-8"))
        return {}
    return yaml.safe_load(SEARCH_CONFIG_PATH.read_text(encoding="utf-8")) or {}


def load_sites_config() -> dict:
    """Load sites.yaml configuration (sites list, manual_ats, blocked, etc.)."""
    import yaml

    path = CONFIG_DIR / "sites.yaml"
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def is_manual_ats(url: str | None) -> bool:
    """Check if a URL routes through an ATS that requires manual application."""
    if not url:
        return False
    sites_cfg = load_sites_config()
    domains = sites_cfg.get("manual_ats", [])
    url_lower = url.lower()
    return any(domain in url_lower for domain in domains)


def load_blocked_sites() -> tuple[set[str], list[str]]:
    """Load blocked sites and URL patterns from sites.yaml.

    Returns:
        (blocked_site_names, blocked_url_patterns)
    """
    cfg = load_sites_config()
    blocked = cfg.get("blocked", {})
    sites = set(blocked.get("sites", []))
    patterns = blocked.get("url_patterns", [])
    return sites, patterns


def load_blocked_sso() -> list[str]:
    """Load blocked SSO domains from sites.yaml."""
    cfg = load_sites_config()
    return cfg.get("blocked_sso", [])


def load_base_urls() -> dict[str, str | None]:
    """Load site base URLs for URL resolution from sites.yaml."""
    cfg = load_sites_config()
    return cfg.get("base_urls", {})


# ---------------------------------------------------------------------------
# Default values — referenced across modules instead of magic numbers
# ---------------------------------------------------------------------------

DEFAULTS = {
    "min_score": 7,
    "max_apply_attempts": 3,
    "apply_lock_ttl_minutes": 20,
    "max_tailor_attempts": 5,
    "poll_interval": 60,
    "apply_timeout": 300,
    "viewport": "1280x900",
}


def load_env():
    """Load environment variables from ~/.applypilot/.env if it exists."""
    from dotenv import load_dotenv

    if ENV_PATH.exists():
        load_dotenv(ENV_PATH)
    # Also try CWD .env as fallback
    load_dotenv()


def get_site_login_password(profile: dict | None = None) -> str:
    """Return default job-site login password from environment.

    Source is `.env` via `APPLYPILOT_SITE_PASSWORD` (or legacy
    `JOB_SITE_PASSWORD`). This deliberately does not read profile.json.
    """
    load_env()

    env_pw = (os.environ.get("APPLYPILOT_SITE_PASSWORD") or os.environ.get("JOB_SITE_PASSWORD") or "").strip()
    if env_pw:
        return env_pw
    return ""


# ---------------------------------------------------------------------------
# Tier system — feature gating by installed dependencies
# ---------------------------------------------------------------------------

TIER_LABELS = {
    1: "Discovery",
    2: "AI Scoring & Tailoring",
    3: "Full Auto-Apply",
}

TIER_COMMANDS: dict[int, list[str]] = {
    1: ["init", "run discover", "run enrich", "status", "dashboard"],
    2: ["run score", "run tailor", "run cover", "run pdf", "run"],
    3: ["apply"],
}


def get_tier() -> int:
    """Detect the current tier based on available dependencies.

    Tier 1 (Discovery):            Python + pip
    Tier 2 (AI Scoring & Tailoring): + LLM API key
    Tier 3 (Full Auto-Apply):       + Claude Code CLI + Chrome
    """
    load_env()

    has_llm = any(os.environ.get(k) for k in ("GEMINI_API_KEY", "OPENAI_API_KEY", "LLM_URL"))
    if not has_llm:
        return 1

    has_claude = shutil.which("claude") is not None
    try:
        get_chrome_path()
        has_chrome = True
    except FileNotFoundError:
        has_chrome = False

    if has_claude and has_chrome:
        return 3

    return 2


def check_tier(required: int, feature: str) -> None:
    """Raise SystemExit with a clear message if the current tier is too low.

    Args:
        required: Minimum tier needed (1, 2, or 3).
        feature: Human-readable description of the feature being gated.
    """
    current = get_tier()
    if current >= required:
        return

    from rich.console import Console

    _console = Console(stderr=True)

    missing: list[str] = []
    if required >= 2 and not any(os.environ.get(k) for k in ("GEMINI_API_KEY", "OPENAI_API_KEY", "LLM_URL")):
        missing.append("LLM API key - run [bold]applypilot init[/bold] or set GEMINI_API_KEY")
    if required >= 3:
        if not shutil.which("claude"):
            missing.append("Claude Code CLI - install from [bold]https://claude.ai/code[/bold]")
        try:
            get_chrome_path()
        except FileNotFoundError:
            missing.append("Chrome/Chromium - install or set CHROME_PATH")

    _console.print(
        f"\n[red]'{feature}' requires {TIER_LABELS.get(required, f'Tier {required}')} (Tier {required}).[/red]\n"
        f"Current tier: {TIER_LABELS.get(current, f'Tier {current}')} (Tier {current})."
    )
    if missing:
        _console.print("\n[yellow]Missing:[/yellow]")
        for m in missing:
            _console.print(f"  - {m}")
    _console.print()
    raise SystemExit(1)
