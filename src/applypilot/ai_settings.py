"""Encrypted UI-managed workspace credentials and context-local task settings."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import json
import os
import math
from pathlib import Path
from applypilot.read_model import reuse_read

_workspace = ContextVar("ai_settings_workspace", default=None)


def current_workspace():
    return _workspace.get()
PROVIDER_KEYS = ("GEMINI_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY")


@contextmanager
def workspace_context(app_dir: Path):
    from applypilot.country_context import current,CountryError
    active=current()
    if active and Path(app_dir).resolve()!=Path(active.workspace):
        raise CountryError("AI workspace is outside the captured owner")
    token = _workspace.set(app_dir)
    try:
        yield
    finally:
        _workspace.reset(token)


@reuse_read
def environment(app_dir: Path | None = None) -> dict:
    from applypilot.config import APP_DIR
    root = app_dir or _workspace.get() or APP_DIR
    from applypilot.country_context import current,CountryError
    active=current()
    if active and Path(root).resolve()!=Path(active.workspace):
        raise CountryError("AI settings are outside the captured owner")
    output = dict(os.environ)
    if (app_dir is not None or _workspace.get() is not None) and (root/".env").is_file():
        from dotenv import dotenv_values
        output.update({k: v for k, v in dotenv_values(root/".env").items() if v is not None})
    path = root/".ai-settings.json"
    if path.exists():
        stored = json.loads(path.read_text(encoding="utf-8"))
        for key in stored.get("service_credentials", []):
            if key in PROVIDER_KEYS:
                output[key] = os.environ.get(key, "")
        output.update(stored.get("settings", {}))
        if stored.get("credentials"):
            from applypilot.mail_connectors import vault
            for key, value in stored["credentials"].items():
                try:
                    output[key] = vault(root).decrypt(value.encode()).decode()
                except Exception:
                    output[key] = ""
    return output


def public(app_dir: Path) -> dict:
    from applypilot.ai_routing import route_table
    from applypilot.decision_observer import configuration
    env = environment(app_dir)
    with workspace_context(app_dir):
        routes = route_table()
    from applypilot.setup_workspace import read_searches_dict
    cfg = read_searches_dict(app_dir)
    from dotenv import dotenv_values
    workspace_env = dotenv_values(app_dir/".env") if (app_dir/".env").is_file() else {}
    path = app_dir/".ai-settings.json"
    stored = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    origins = {}
    for key in PROVIDER_KEYS:
        encrypted = key in stored.get("credentials", {})
        service_only = key in stored.get("service_credentials", [])
        workspace_override = workspace_env.get(key) is not None and not service_only
        origins[key] = {"source": "encrypted workspace key" if encrypted else "workspace .env" if workspace_override else "service environment" if os.environ.get(key) else "not configured",
                        "workspace_env_present": bool(workspace_env.get(key)),
                        "workspace_env_differs_from_service": bool(workspace_env.get(key) and os.environ.get(key) and workspace_env[key] != os.environ[key]),
                        "using_service_key": service_only and not encrypted}
    return {"routes": routes, "credentials": {key: bool(env.get(key)) for key in ("GEMINI_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY")},
            "credential_origins": origins,
            "studio_daily_budget": int(env.get("APPLYPILOT_STUDIO_DAILY_BUDGET", cfg.get("studio_daily_budget", 20))),
            "prices": json.loads(env["APPLYPILOT_AI_PRICES"]) if env.get("APPLYPILOT_AI_PRICES") else cfg.get("ai_prices", {}),
            "decisions_observer": configuration(app_dir, env)}


def save(app_dir: Path, data: dict) -> dict:
    from applypilot.setup_workspace import _atomic_write_text
    from applypilot.ai_routing import TASKS
    path = app_dir/".ai-settings.json"
    stored = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"settings": {}, "credentials": {}}
    if "decisions_observer" in data:
        options = data["decisions_observer"]
        if not isinstance(options, dict) or set(options) - {"enabled", "daily_budget"}:
            raise ValueError("Provide Decisions observer enabled and daily_budget settings")
        if "enabled" in options:
            if not isinstance(options["enabled"], bool):
                raise ValueError("Decisions observer enabled must be a boolean")
            stored["settings"]["APPLYPILOT_DECISIONS_OBSERVER"] = "1" if options["enabled"] else "0"
        if "daily_budget" in options:
            budget = options["daily_budget"]
            if isinstance(budget, bool) or not isinstance(budget, int) or not 1 <= budget <= 200:
                raise ValueError("Decisions daily budget must be an integer between 1 and 200")
            stored["settings"]["APPLYPILOT_DECISIONS_DAILY_BUDGET"] = str(budget)
    if "studio_daily_budget" in data:
        number = int(data["studio_daily_budget"])
        if not 1 <= number <= 200:
            raise ValueError("Studio generation budget must be between 1 and 200")
        stored["settings"]["APPLYPILOT_STUDIO_DAILY_BUDGET"] = str(number)
    if "prices" in data:
        prices = data["prices"]
        if not isinstance(prices, dict) or len(prices) > 50:
            raise ValueError("Configure prices for up to 50 provider/model pairs")
        for name, rates in prices.items():
            if not isinstance(rates, dict) or any(not isinstance(rates.get(key), (int, float)) or not math.isfinite(rates[key]) or rates[key] < 0 for key in ("input_per_million", "output_per_million")):
                raise ValueError("Prices must be nonnegative per-million token rates")
        stored["settings"]["APPLYPILOT_AI_PRICES"] = json.dumps(prices)
    for key, value in (data.get("credentials") or {}).items():
        if key not in {"GEMINI_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"}:
            raise ValueError("Unknown provider credential")
        if value is None:
            stored["credentials"].pop(key, None)
            service_keys = set(stored.get("service_credentials", []))
            service_keys.add(key)
            stored["service_credentials"] = sorted(service_keys)
        elif str(value).strip():
            if len(str(value)) > 1000:
                raise ValueError("Provider credential is too long")
            from applypilot.mail_connectors import vault
            stored["credentials"][key] = vault(app_dir).encrypt(str(value).strip().encode()).decode()
            stored["service_credentials"] = [item for item in stored.get("service_credentials", []) if item != key]
    for route in data.get("routes", []):
        task = route.get("task")
        if task not in TASKS:
            raise ValueError("Unknown AI task")
        for field in ("provider", "model", "reasoning_effort", "thinking_budget", "reasoning_token_allowance"):
            if field in route:
                value = str(route[field] if route[field] is not None else "").strip()
                if len(value) > 200 or (field == "provider" and value not in {"", "gemini", "openai", "anthropic", "local"}):
                    raise ValueError("Invalid provider or model setting")
                if field == "model" and value.startswith(("sk-", "AIza", "GOCSPX-")):
                    raise ValueError("Use the API key field for credentials, not the model field")
                key = f"LLM_{task.upper()}_{field.upper()}"
                if value or field in {"reasoning_effort", "thinking_budget", "reasoning_token_allowance"}:
                    stored["settings"][key] = value
                else:
                    stored["settings"].pop(key, None)
    _atomic_write_text(path, json.dumps(stored, indent=2)+"\n")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return public(app_dir)
