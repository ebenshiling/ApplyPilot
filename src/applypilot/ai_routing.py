"""Validated, context-local task routing. Credentials never appear in diagnostics."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import wraps

TASKS = {
    "score": "Job scoring and fit explanations",
    "summary": "Summaries, copilot and failure explanations",
    "tailor": "CV tailoring",
    "statement": "Supporting statements",
    "cover": "Cover letters and follow-up drafts",
    "review": "LLM factual review / judge",
    "assistant": "Interview preparation and general assistance",
    "forms": "Application form mapping",
    "extract": "Discovery and description extraction",
    "email": "Email classification, matching and date extraction",
}
ASSISTANT_ROUTES = {"explain": "score", "diagnose": "summary", "copilot": "summary", "interview": "assistant", "followup": "cover"}
WRITING_TASKS = frozenset({"tailor", "statement", "cover"})
REASONING_HEADROOM = {"low": 2048, "medium": 4096, "high": 8192, "xhigh": 16384, "max": 16384}
_ALIASES = {"scoring": "score", "summaries": "summary", "cv": "tailor", "validation": "review", "judge": "review", "enrich": "extract", "discover": "extract"}
_DEFAULT_MODELS = {"gemini": "gemini-2.5-flash", "openai": "gpt-4o-mini", "anthropic": "claude-sonnet-4-6", "local": "local-model"}
_KEYS = {"gemini": "GEMINI_API_KEY", "openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY", "local": "LLM_API_KEY"}
_URLS = {"gemini": "https://generativelanguage.googleapis.com", "openai": "https://api.openai.com/v1", "anthropic": "https://api.anthropic.com/v1"}
_current_task = ContextVar("applypilot_llm_task", default=None)
_model_overrides = ContextVar("applypilot_llm_model_overrides", default={})


def normalize_task(task: str) -> str:
    name = str(task).strip().lower()
    name = _ALIASES.get(name, name)
    if name not in TASKS:
        raise ValueError("Unknown AI routing task")
    return name


def current_task() -> str | None:
    return _current_task.get()


@contextmanager
def task_context(task: str):
    token = _current_task.set(normalize_task(task))
    try:
        yield
    finally:
        _current_task.reset(token)


def routed_task(task: str):
    name = normalize_task(task)
    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            with task_context(name):
                return function(*args, **kwargs)
        wrapped.__llm_task__ = name
        return wrapped
    return decorate


@contextmanager
def model_context(model: str | None):
    if not model:
        yield
        return
    overrides = dict(_model_overrides.get())
    overrides[current_task() or "default"] = str(model).strip()
    token = _model_overrides.set(overrides)
    try:
        yield
    finally:
        _model_overrides.reset(token)


def _setting(task: str, suffix: str) -> str:
    from applypilot.ai_settings import environment
    return (environment().get(f"LLM_{task.upper()}_{suffix}", "") or "").strip()


@dataclass(frozen=True)
class Route:
    task: str
    provider: str
    base_url: str = field(repr=False)
    model: str
    api_key: str = field(repr=False)
    reasoning_effort: str | None = None
    thinking_budget: int | None = None
    explicit: bool = False
    reasoning_token_allowance: int = 0

    @property
    def identity(self) -> str:
        return f"{self.provider}:{self.model}:{self.reasoning_effort or ''}:{self.thinking_budget}:{self.reasoning_token_allowance}"

    def description(self) -> dict:
        return {"task": self.task, "label": TASKS.get(self.task, "Default provider"),
                "provider": self.provider, "model": self.model,
                "reasoning_effort": self.reasoning_effort, "thinking_budget": self.thinking_budget,
                "reasoning_token_allowance": self.reasoning_token_allowance,
                "explicit": self.explicit, "configured": True}


def resolve_route(task: str | None = None) -> Route:
    from applypilot.llm import _detect_provider, _pick_gemini_model, _LUNA_MODELS, _LUNA_EFFORTS

    name = normalize_task(task) if task else current_task()
    from applypilot.ai_settings import environment
    env = environment()
    provider_override = _setting(name, "PROVIDER").lower() if name else ""
    model_override = _setting(name, "MODEL") if name else ""
    effort_override = _setting(name, "REASONING_EFFORT").lower() if name else ""
    budget_override = _setting(name, "THINKING_BUDGET") if name else ""
    allowance_override = _setting(name, "REASONING_TOKEN_ALLOWANCE") if name else ""
    explicit = bool(provider_override or model_override or effort_override or budget_override or allowance_override)
    if provider_override:
        provider = "anthropic" if provider_override == "claude" else provider_override
        if provider not in _DEFAULT_MODELS:
            raise ValueError(f"LLM_{name.upper()}_PROVIDER must be gemini, openai, anthropic, or local")
        try:
            default = _detect_provider()
        except RuntimeError:
            default = None
        model = model_override or (default[2] if default and default[0] == provider else _DEFAULT_MODELS[provider])
        url = (env.get("LLM_URL", "") or "").strip().rstrip("/") if provider == "local" else _URLS[provider]
        key = (env.get(_KEYS[provider], "") or "").strip()
        if provider == "local" and not url:
            raise RuntimeError(f"AI task {name}: LLM_URL is not configured")
        if provider != "local" and not key:
            raise RuntimeError(f"AI task {name}: {_KEYS[provider]} is not configured")
    else:
        provider, url, model, key = _detect_provider()
        model = model_override or model
    model = _model_overrides.get().get(name or "default", model)
    if not model or model.startswith(("sk-", "AIza", "GOCSPX-")):
        raise ValueError("AI route requires a model identifier")
    if provider == "gemini":
        model = _pick_gemini_model(model)
    effort = None
    if provider == "openai" and model in _LUNA_MODELS:
        effort = effort_override or (env.get("OPENAI_REASONING_EFFORT", "none") or "none").strip().lower()
        if effort not in _LUNA_EFFORTS:
            raise ValueError("Luna task reasoning must be none, low, medium, high, xhigh, or max")
    elif effort_override and effort_override != "none":
        raise ValueError("Task-specific reasoning effort currently requires an OpenAI Luna model")
    budget = None
    if budget_override:
        if provider != "gemini":
            raise ValueError("Task THINKING_BUDGET applies only to Gemini")
        try:
            budget = int(budget_override)
        except ValueError as exc:
            raise ValueError("Task THINKING_BUDGET must be an integer") from exc
        if not -1 <= budget <= 32768:
            raise ValueError("Task THINKING_BUDGET must be between -1 and 32768")
    allowance = REASONING_HEADROOM.get(effort, 0) if name in WRITING_TASKS else 0
    if allowance_override:
        if name not in WRITING_TASKS or provider != "openai" or model not in _LUNA_MODELS:
            raise ValueError("REASONING_TOKEN_ALLOWANCE applies only to OpenAI Luna writing tasks")
        try:
            configured_allowance = int(allowance_override)
        except ValueError as exc:
            raise ValueError("REASONING_TOKEN_ALLOWANCE must be an integer") from exc
        if not 0 <= configured_allowance <= 16384:
            raise ValueError("REASONING_TOKEN_ALLOWANCE must be between 0 and 16384")
        allowance = configured_allowance if effort != "none" else 0
    return Route(name or "default", provider, url, model, key, effort, budget, explicit, allowance)


def route_info(task: str | None = None) -> dict:
    name = normalize_task(task) if task else (current_task() or "default")
    try:
        return resolve_route(task).description()
    except (ValueError, RuntimeError) as exc:
        return {"task": name, "label": TASKS.get(name, "Default provider"), "configured": False, "error": str(exc)}


def route_table() -> list[dict]:
    return [route_info(task) for task in TASKS]
