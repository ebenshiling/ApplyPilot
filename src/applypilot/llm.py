"""Unified LLM client for ApplyPilot.

Auto-detects provider from environment:
  GEMINI_API_KEY  -> Google Gemini (AI Studio Generative Language API)
  OPENAI_API_KEY  -> OpenAI
  LLM_URL         -> Local llama.cpp / Ollama compatible endpoint

Optional:
  ANTHROPIC_API_KEY -> Anthropic Claude Messages API
  LLM_PROVIDER    -> Force provider: gemini|openai|anthropic|local

LLM_MODEL env var overrides the model name for any provider.
OPENAI_REASONING_EFFORT configures Luna reasoning (default: none).
"""

import json
import logging
import os
import random
import re
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

import httpx
from applypilot.ai_routing import current_task, model_context, resolve_route, task_context, WRITING_TASKS, REASONING_HEADROOM

log = logging.getLogger(__name__)
_LUNA_MODELS = frozenset({"gpt-6-luna", "gpt-5.6-luna"})
_LUNA_EFFORTS = frozenset({"none", "low", "medium", "high", "xhigh", "max"})

# ---------------------------------------------------------------------------
# Provider detection
# ---------------------------------------------------------------------------


def _pick_gemini_model(model: str) -> str:
    """Normalize/upgrade Gemini model names.

    AI Studio may return 404 for models that are "not available to new users".
    When that happens, we try a safe default.
    """
    m = (model or "").strip()
    if not m:
        return "gemini-2.5-flash"
    if m.startswith("models/"):
        m = m.split("models/", 1)[1]
    return m


def _detect_provider() -> tuple[str, str, str, str]:
    """Return (provider, base_url, model, api_key) based on environment variables."""
    from applypilot.ai_settings import environment
    env = environment()
    model_override = (env.get("LLM_MODEL", "") or "").strip()
    provider_override = (env.get("LLM_PROVIDER", "") or "").strip().lower()

    gemini_key = (env.get("GEMINI_API_KEY", "") or "").strip()
    openai_key = (env.get("OPENAI_API_KEY", "") or "").strip()
    anthropic_key = (env.get("ANTHROPIC_API_KEY", "") or "").strip()
    local_url = (env.get("LLM_URL", "") or "").strip()

    if provider_override:
        if provider_override == "gemini":
            if not gemini_key:
                raise RuntimeError("LLM_PROVIDER=gemini but GEMINI_API_KEY is not set")
            return (
                "gemini",
                "https://generativelanguage.googleapis.com",
                _pick_gemini_model(model_override or "gemini-2.5-flash"),
                gemini_key,
            )
        if provider_override == "openai":
            if not openai_key:
                raise RuntimeError("LLM_PROVIDER=openai but OPENAI_API_KEY is not set")
            return ("openai", "https://api.openai.com/v1", model_override or "gpt-4o-mini", openai_key)
        if provider_override == "local":
            if not local_url:
                raise RuntimeError("LLM_PROVIDER=local but LLM_URL is not set")
            return ("local", local_url.rstrip("/"), model_override or "local-model", env.get("LLM_API_KEY", ""))
        if provider_override in {"anthropic", "claude"}:
            if not anthropic_key:
                raise RuntimeError("LLM_PROVIDER=anthropic but ANTHROPIC_API_KEY is not set")
            return ("anthropic", "https://api.anthropic.com/v1", model_override or "claude-sonnet-4-6", anthropic_key)

        raise RuntimeError("Invalid LLM_PROVIDER. Use: gemini, openai, anthropic, local")

    # Default ordering:
    # 1) explicit local endpoint
    if local_url:
        return (
            "local",
            local_url.rstrip("/"),
            model_override or "local-model",
            env.get("LLM_API_KEY", ""),
        )

    # 2) Gemini key
    if gemini_key:
        # Use Gemini native API (generateContent). The OpenAI-compat endpoint has been unreliable.
        return (
            "gemini",
            "https://generativelanguage.googleapis.com",
            _pick_gemini_model(model_override or "gemini-2.5-flash"),
            gemini_key,
        )

    # 3) OpenAI
    if openai_key:
        return (
            "openai",
            "https://api.openai.com/v1",
            model_override or "gpt-4o-mini",
            openai_key,
        )

    if anthropic_key:
        return ("anthropic", "https://api.anthropic.com/v1", model_override or "claude-sonnet-4-6", anthropic_key)
    raise RuntimeError(
        "No LLM provider configured. Set GEMINI_API_KEY, OPENAI_API_KEY, ANTHROPIC_API_KEY, or LLM_URL."
    )


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

_MAX_RETRIES = int(os.environ.get("LLM_MAX_RETRIES", "5") or "5")
_TIMEOUT = int(os.environ.get("LLM_TIMEOUT", "120") or "120")  # seconds

# Optional client-side throttling (helps avoid 429s on low-rate plans)
_MIN_INTERVAL = float(os.environ.get("LLM_MIN_INTERVAL", "0") or "0")

# Backoff tuning
_BACKOFF_BASE = float(os.environ.get("LLM_BACKOFF_BASE", "2") or "2")
_BACKOFF_MAX = float(os.environ.get("LLM_BACKOFF_MAX", "60") or "60")

_rate_lock = threading.Lock()
_last_request_ts = 0.0
_request_budget = ContextVar("llm_request_budget", default=None)


@contextmanager
def request_budget(timeout_seconds: float = 45, max_attempts: int = 1):
    """Context-local interactive limits; never change a pooled client's defaults."""
    token = _request_budget.set((max(1, min(120, timeout_seconds)), max(1, min(_MAX_RETRIES, max_attempts))))
    try:
        yield
    finally:
        _request_budget.reset(token)


def _attempt_limit() -> int:
    budget = _request_budget.get()
    return budget[1] if budget else _MAX_RETRIES


def _request_options() -> dict:
    budget = _request_budget.get()
    return {"timeout": httpx.Timeout(budget[0], connect=min(10, budget[0]))} if budget else {}


class ProviderAccessError(RuntimeError):
    """Sanitized account-level failure; do not retry it for every job in a batch."""
    def __init__(self, provider: str, category: str, status_code: int, message: str):
        self.provider = provider
        self.category = category
        self.status_code = status_code
        self.batch_blocking = True
        super().__init__(message)


def provider_access_error(provider: str, exc: httpx.HTTPStatusError) -> ProviderAccessError | None:
    status = exc.response.status_code
    code = ""
    try:
        error = exc.response.json().get("error", {})
        if isinstance(error, dict):
            code = str(error.get("code") or error.get("status") or "").lower()
    except (ValueError, AttributeError):
        pass
    category = "payment" if status == 402 or code == "insufficient_quota" else "authentication" if status == 401 else "permission" if status == 403 else "configuration" if status == 404 else None
    if not category:
        return None
    actions = {"payment": "Check billing/credits and the API project linked to this key, or explicitly choose another configured provider in Settings.",
               "authentication": "Replace the provider API key in Settings.", "permission": "Check API/project permissions, billing availability and model access for this key.",
               "configuration": "Check that this model is available to the selected provider/project in Settings."}
    return ProviderAccessError(provider, category, status, f"{provider.title()} blocked this request (HTTP {status}; {category}). {actions[category]}")


def _throttle() -> None:
    """Simple global min-interval throttle across threads."""
    global _last_request_ts
    if _MIN_INTERVAL <= 0:
        return
    with _rate_lock:
        now = time.time()
        wait = _MIN_INTERVAL - (now - _last_request_ts)
        if wait > 0:
            time.sleep(wait)
        _last_request_ts = time.time()


def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        # Usually seconds (e.g. "10")
        return float(value.strip())
    except Exception:
        return None


def _compute_backoff_seconds(attempt: int, status_code: int | None, retry_after: float | None) -> float:
    """Compute a wait time with jitter.

    attempt is 0-based.
    """
    if retry_after is not None:
        base = retry_after
    else:
        base = _BACKOFF_BASE
        # 429s often need a longer cooldown than 1/2/4s.
        if status_code == 429:
            base = max(base, 5.0)
        base = min(_BACKOFF_MAX, base * (2**attempt))

    # small jitter to avoid synchronized retries
    return min(_BACKOFF_MAX, base + random.uniform(0.0, 0.5))


class LLMClient:
    """Unified LLM facade.

    - OpenAI/local: OpenAI-compatible /chat/completions
    - Gemini: Generative Language API models/*:generateContent
    """

    def __init__(self, provider: str, base_url: str, model: str, api_key: str, *, reasoning_effort: str | None = None, thinking_budget: int | None = None, reasoning_token_allowance: int | None = None) -> None:
        self.provider = provider
        self.base_url = base_url
        self.model = model
        self.api_key = api_key
        self._reasoning_effort = reasoning_effort
        self.thinking_budget = thinking_budget
        if reasoning_token_allowance is not None and (isinstance(reasoning_token_allowance, bool) or not isinstance(reasoning_token_allowance, int) or not 0 <= reasoning_token_allowance <= 16384):
            raise ValueError("Reasoning token allowance must be an integer between 0 and 16384")
        self.reasoning_token_allowance = reasoning_token_allowance
        self._client = httpx.Client(timeout=_TIMEOUT)
        self._usage_local = threading.local()

    @property
    def last_usage(self) -> dict:
        return getattr(self._usage_local, "value", {})

    @property
    def last_model(self) -> str:
        return getattr(self._usage_local, "model", self.model)

    @property
    def openai_reasoning_effort(self) -> str | None:
        if self.provider != "openai" or self.model not in _LUNA_MODELS:
            return None
        effort = self._reasoning_effort if self._reasoning_effort is not None else (os.environ.get("OPENAI_REASONING_EFFORT", "none") or "none").strip().lower() or "none"
        if effort not in _LUNA_EFFORTS:
            raise ValueError("OPENAI_REASONING_EFFORT for Luna must be none, low, medium, high, xhigh, or max")
        return effort

    @property
    def cache_identity(self) -> str:
        return f"{self.provider}:{self.model}:{self.openai_reasoning_effort or ''}:{self.thinking_budget}:{self.reasoning_token_allowance}"

    def completion_token_limit(self, requested: int) -> int:
        """Bound total Luna completion tokens; headroom is not a separate reserve."""
        effort = self.openai_reasoning_effort
        if effort is None:
            return requested
        if isinstance(requested, bool) or not isinstance(requested, int) or not 1 <= requested <= 128000:
            raise ValueError("Luna completion limit must be an integer between 1 and 128000")
        allowance = 0
        if effort != "none" and current_task() in WRITING_TASKS:
            allowance = self.reasoning_token_allowance if self.reasoning_token_allowance is not None else REASONING_HEADROOM.get(effort, 0)
        return min(128000, requested + allowance)

    # -- public API ---------------------------------------------------------

    def _chat(
        self,
        messages: list[dict],
        temperature: float = 0.0,
        max_tokens: int = 4096,
        response_mime_type: str | None = None,
        thinking_budget: int | None = None,
    ) -> str:
        """Send a chat completion request and return the assistant message text."""
        self._usage_local.value = {}
        self._usage_local.model = self.model
        self._usage_local.operation_id = None
        self._usage_local.operation_request_key = None
        self._usage_local.operation_country = None
        log.info("LLM request task=%s provider=%s model=%s", current_task() or "default", self.provider, self.model)
        # Qwen3 optimization: prepend /no_think to skip chain-of-thought
        # reasoning, saving tokens on structured extraction tasks.
        if "qwen" in self.model.lower() and messages:
            first = messages[0]
            if first.get("role") == "user" and not first["content"].startswith("/no_think"):
                messages = [{"role": first["role"], "content": f"/no_think\n{first['content']}"}] + messages[1:]

        if (self.provider or "").lower() == "gemini":
            return self._chat_gemini(
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
                response_mime_type=response_mime_type,
                thinking_budget=thinking_budget if thinking_budget is not None else self.thinking_budget,
            )

        if self.provider == "anthropic":
            return self._chat_anthropic(messages, temperature, max_tokens, response_mime_type)

        return self._chat_openai_compat(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_mime_type=response_mime_type,
        )

    def _chat_anthropic(self, messages: list[dict], temperature: float, max_tokens: int, response_mime_type: str | None) -> str:
        system = "\n\n".join(str(m.get("content", "")) for m in messages if m.get("role") == "system")
        conversation = [{"role": m["role"], "content": m["content"]} for m in messages if m.get("role") in {"user", "assistant"}]
        if response_mime_type == "application/json":
            system += "\nReturn valid JSON only, with no markdown fences or commentary."
        payload = {"model": self.model, "system": system, "messages": conversation,
                   "temperature": temperature, "max_tokens": max_tokens}
        for attempt in range(_attempt_limit()):
            try:
                _throttle()
                from applypilot.operations import before_provider
                before_provider(payload,self)
                response = self._client.post(self.base_url + "/messages", json=payload,
                    headers={"x-api-key": self.api_key, "anthropic-version": "2023-06-01", "Content-Type": "application/json"}, **_request_options())
                if response.status_code in {429, 500, 502, 503, 529} and attempt < _attempt_limit() - 1:
                    time.sleep(_compute_backoff_seconds(attempt, response.status_code, _parse_retry_after(response.headers.get("retry-after"))))
                    continue
                response.raise_for_status()
                data = response.json()
                self._usage_local.value = data.get("usage", {})
                if data.get("stop_reason") == "max_tokens":
                    raise RuntimeError("Claude output exceeded the configured token limit")
                return "".join(block.get("text", "") for block in data.get("content", []) if block.get("type") == "text").strip()
            except httpx.TimeoutException:
                if attempt == _attempt_limit() - 1:
                    raise
                time.sleep(_compute_backoff_seconds(attempt, None, None))
        raise RuntimeError("Claude request failed after bounded retries")

    def _chat_openai_compat(
        self,
        messages: list[dict],
        temperature: float,
        max_tokens: int,
        response_mime_type: str | None = None,
    ) -> str:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        effort = self.openai_reasoning_effort
        if effort is not None:
            payload.pop("max_tokens")
            payload["max_completion_tokens"] = self.completion_token_limit(max_tokens)
            log.info("Luna completion budget task=%s effort=%s requested=%s total=%s", current_task() or "default", effort, max_tokens, payload["max_completion_tokens"])
            payload["reasoning_effort"] = effort
            payload["messages"] = [{**m, "role": "developer"} if m.get("role") == "system" else dict(m) for m in messages]
            if effort != "none":
                payload.pop("temperature")
        if response_mime_type == "application/json" and self.provider == "openai":
            payload["response_format"] = {"type": "json_object"}

        for attempt in range(_attempt_limit()):
            try:
                _throttle()
                from applypilot.operations import before_provider
                before_provider(payload,self)
                resp = self._client.post(
                    f"{self.base_url}/chat/completions",
                    json=payload,
                    headers=headers,
                    **_request_options(),
                )
                if resp.status_code in (429, 503) and attempt < _attempt_limit() - 1:
                    retry_after = _parse_retry_after(resp.headers.get("retry-after") or resp.headers.get("Retry-After"))
                    wait = _compute_backoff_seconds(attempt, resp.status_code, retry_after)
                    log.warning(
                        "LLM returned %s, retrying in %ds (attempt %d/%d)",
                        resp.status_code,
                        int(round(wait)),
                        attempt + 1,
                        _MAX_RETRIES,
                    )
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                data = resp.json()
                self._usage_local.value = data.get("usage", {})
                if effort is not None:
                    choices = data.get("choices") or []
                    if not choices:
                        raise RuntimeError("OpenAI returned no completion choices")
                    if choices[0].get("finish_reason") == "length":
                        raise RuntimeError("Luna reached its output limit, including reasoning tokens. Reduce reasoning effort or adjust the output limit.")
                    content = (choices[0].get("message") or {}).get("content")
                    if not isinstance(content, str) or not content.strip():
                        raise RuntimeError("Luna returned no usable text response")
                return data["choices"][0]["message"]["content"]
            except httpx.TimeoutException:
                if attempt < _attempt_limit() - 1:
                    wait = _compute_backoff_seconds(attempt, None, None)
                    log.warning(
                        "LLM request timed out, retrying in %ds (attempt %d/%d)",
                        int(round(wait)),
                        attempt + 1,
                        _MAX_RETRIES,
                    )
                    time.sleep(wait)
                    continue
                raise

        raise RuntimeError("LLM request failed after all retries")

    def _chat_gemini(
        self,
        messages: list[dict],
        temperature: float,
        max_tokens: int,
        response_mime_type: str | None = None,
        thinking_budget: int | None = None,
    ) -> str:
        # Map OpenAI-style messages -> Gemini contents.
        system_texts: list[str] = []
        contents: list[dict] = []

        for m in messages:
            role = (m.get("role") or "").strip().lower()
            text = m.get("content") or ""
            if role == "system":
                if text:
                    system_texts.append(str(text))
                continue

            # Gemini uses roles: user | model
            g_role = "user"
            if role == "assistant":
                g_role = "model"
            contents.append({"role": g_role, "parts": [{"text": str(text)}]})

        payload: dict = {
            "contents": contents or [{"role": "user", "parts": [{"text": ""}]}],
            "generationConfig": {
                "temperature": float(temperature),
                "maxOutputTokens": int(max_tokens),
            },
        }
        if response_mime_type:
            payload["generationConfig"]["responseMimeType"] = str(response_mime_type)
        if thinking_budget is not None:
            # Gemini 2.5+ can spend the output budget on hidden "thoughts".
            # For structured JSON tasks we want thinking disabled (0).
            payload["generationConfig"]["thinkingConfig"] = {"thinkingBudget": int(thinking_budget)}
        if system_texts:
            payload["systemInstruction"] = {"parts": [{"text": "\n\n".join(system_texts)}]}

        # Keep credentials out of URLs and HTTP exception/log messages.
        url = f"{self.base_url}/v1beta/models/{_pick_gemini_model(self.model)}:generateContent"

        for attempt in range(_attempt_limit()):
            try:
                _throttle()
                from applypilot.operations import before_provider
                before_provider(payload,self)
                resp = self._client.post(
                    url,
                    json=payload,
                    headers={"Content-Type": "application/json", "x-goog-api-key": self.api_key},
                    **_request_options(),
                )

                # Model not available for new users: fall back to a newer model.
                if resp.status_code == 404 and attempt == 0 and attempt < _attempt_limit() - 1:
                    try:
                        msg = (resp.json() or {}).get("error", {}).get("message", "")
                    except Exception:
                        msg = ""
                    if "no longer available" in (msg or "").lower() or "not available" in (msg or "").lower():
                        fallback = os.environ.get("GEMINI_FALLBACK_MODEL", "gemini-2.5-flash")
                        fallback = _pick_gemini_model(fallback)
                        if fallback != _pick_gemini_model(self.model):
                            log.warning("Gemini model %s unavailable; falling back to %s", self.model, fallback)
                            self._usage_local.model = fallback
                            url = f"{self.base_url}/v1beta/models/{fallback}:generateContent"
                            # retry immediately on the new model
                            continue

                if resp.status_code in (429, 503) and attempt < _attempt_limit() - 1:
                    retry_after = _parse_retry_after(resp.headers.get("retry-after") or resp.headers.get("Retry-After"))
                    wait = _compute_backoff_seconds(attempt, resp.status_code, retry_after)
                    log.warning(
                        "LLM returned %s, retrying in %ds (attempt %d/%d)",
                        resp.status_code,
                        int(round(wait)),
                        attempt + 1,
                        _MAX_RETRIES,
                    )
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                data = resp.json()
                candidates = data.get("candidates") or []
                if not candidates:
                    return ""
                parts = (candidates[0].get("content") or {}).get("parts") or []
                usage = data.get("usageMetadata", {})
                self._usage_local.value = {"input_tokens": usage.get("promptTokenCount"), "output_tokens": usage.get("candidatesTokenCount")}
                texts = [p.get("text", "") for p in parts if isinstance(p, dict)]
                return "".join(texts).strip()
            except httpx.TimeoutException:
                if attempt < _attempt_limit() - 1:
                    wait = _compute_backoff_seconds(attempt, None, None)
                    log.warning(
                        "LLM request timed out, retrying in %ds (attempt %d/%d)",
                        int(round(wait)),
                        attempt + 1,
                        _MAX_RETRIES,
                    )
                    time.sleep(wait)
                    continue
                raise

        raise RuntimeError("LLM request failed after all retries")

    def chat(self, messages: list[dict], temperature: float = 0.0, max_tokens: int = 4096,
             response_mime_type: str | None = None, thinking_budget: int | None = None) -> str:
        self._usage_local.value={}
        self._usage_local.operation_id=None
        self._usage_local.operation_request_key=None
        self._usage_local.operation_country=None
        from applypilot.operations import transport
        return transport(self,messages,temperature,max_tokens,response_mime_type,thinking_budget,
                         lambda:self._guarded_chat(messages,temperature,max_tokens,response_mime_type,thinking_budget))

    def _guarded_chat(self, messages: list[dict], temperature: float = 0.0, max_tokens: int = 4096,
             response_mime_type: str | None = None, thinking_budget: int | None = None) -> str:
        cached_error = getattr(self._usage_local, "blocked_error", None)
        if cached_error and getattr(self._usage_local, "blocked_until", 0) > time.monotonic():
            self._usage_local.value = {}
            raise cached_error
        try:
            return self._chat(messages, temperature, max_tokens, response_mime_type, thinking_budget)
        except httpx.HTTPStatusError as exc:
            classified = provider_access_error(self.provider, exc)
            if classified:
                self._usage_local.blocked_error = classified
                self._usage_local.blocked_until = time.monotonic() + 900
                raise classified from None
            raise
        finally:
            try:
                from applypilot.pipeline_store import record_usage
                record_usage(self)
            except Exception:
                log.debug("Pipeline usage ledger unavailable", exc_info=True)

    def ask(self, prompt: str, **kwargs) -> str:
        """Convenience: single user prompt -> assistant response."""
        return self.chat([{"role": "user", "content": prompt}], **kwargs)


def chat_json(messages: list[dict], temperature: float = 0.0, max_tokens: int = 2048) -> str:
    """Request JSON output when supported by the provider."""
    client = get_client()
    # Gemini supports responseMimeType; OpenAI supports response_format.
    if (client.provider or "").lower() == "gemini":
        # Default: disable thinking to avoid MAX_TOKENS truncation in JSON mode.
        route_budget = getattr(client, "thinking_budget", None)
        tb_raw = str(route_budget) if route_budget is not None else (os.environ.get("GEMINI_JSON_THINKING_BUDGET", "0") or "0").strip()
        try:
            thinking_budget = int(tb_raw)
        except Exception:
            thinking_budget = 0
        return client.chat(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_mime_type="application/json",
            thinking_budget=thinking_budget,
        )
    if (client.provider or "").lower() == "openai":
        return client.chat(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_mime_type="application/json",
        )
    return client.chat(messages, temperature=temperature, max_tokens=max_tokens)


class StructuredOutputError(ValueError):
    """Raised when an LLM response cannot satisfy a structured JSON contract."""


def parse_json_response(text: str) -> Any:
    """Parse JSON from a model response with conservative formatting tolerance.

    Models sometimes add markdown fences, a short preamble, or a hidden-thinking
    block despite being asked for JSON. We accept those wrappers but never invent
    or repair field values here.
    """
    raw = str(text or "").strip()
    if not raw:
        raise StructuredOutputError("LLM returned an empty response")

    if "</think>" in raw:
        raw = raw.rsplit("</think>", 1)[1].strip()

    fenced = re.search(r"```(?:json)?\s*(.*?)```", raw, flags=re.IGNORECASE | re.DOTALL)
    if fenced:
        raw = fenced.group(1).strip()

    decoder = json.JSONDecoder()
    for index, char in enumerate(raw):
        if char not in "[{":
            continue
        try:
            value, _ = decoder.raw_decode(raw[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, (dict, list)):
            return value

    raise StructuredOutputError(f"LLM returned invalid JSON ({len(raw)} response chars)")


def structured_json(
    messages: list[dict],
    *,
    temperature: float = 0.0,
    max_tokens: int = 2048,
    required_keys: tuple[str, ...] = (),
    task_name: str = "structured output",
    repair_attempts: int = 1,
    routing_task: str | None = None,
) -> dict[str, Any]:
    """Request a JSON object, retrying once when the response shape is invalid.

    Transport retries remain inside the provider client. This wrapper handles the
    separate failure mode where the provider responds successfully but the model
    emits empty, malformed, or incorrectly shaped JSON.
    """
    base_messages = [dict(message) for message in messages]
    total_attempts = max(0, int(repair_attempts)) + 1
    last_error = "invalid response"

    for attempt in range(total_attempts):
        request_messages = base_messages
        if attempt:
            keys = ", ".join(required_keys) or "the requested fields"
            request_messages = base_messages + [
                {
                    "role": "user",
                    "content": (
                        "Your previous response did not satisfy the JSON contract. "
                        f"Return only one valid JSON object with these top-level keys: {keys}. "
                        "Do not use markdown, commentary, or a code fence."
                    ),
                }
            ]

        if routing_task:
            with task_context(routing_task):
                raw = chat_json(request_messages, temperature=temperature, max_tokens=max_tokens)
        else:
            raw = chat_json(request_messages, temperature=temperature, max_tokens=max_tokens)
        try:
            parsed = parse_json_response(raw)
            if not isinstance(parsed, dict):
                raise StructuredOutputError("LLM JSON response was not an object")
            missing = [key for key in required_keys if key not in parsed]
            if missing:
                raise StructuredOutputError("LLM JSON response missing required fields: " + ", ".join(missing))
            return parsed
        except StructuredOutputError as exc:
            last_error = str(exc)
            if attempt < total_attempts - 1:
                log.warning(
                    "LLM structured output rejected task=%s attempt=%d/%d: %s",
                    task_name,
                    attempt + 1,
                    total_attempts,
                    last_error,
                )

    raise StructuredOutputError(f"LLM structured output failed for {task_name}: {last_error}")


def close_client() -> None:
    """Close the shared HTTP client (optional)."""
    global _instance
    with _clients_lock:
        clients = list(_clients.values()) + ([_instance] if _instance is not None else [])
        _clients.clear()
        _instance = None
    for client in {id(c): c for c in clients}.values():
        client._client.close()


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_instance: LLMClient | None = None
_clients: dict[tuple, LLMClient] = {}
_clients_lock = threading.RLock()


def get_client(task: str | None = None) -> LLMClient:
    """Return an immutable configured client for the current/explicit task."""
    global _instance
    route = resolve_route(task)
    key = (route.provider, route.base_url, route.model, route.api_key, route.reasoning_effort, route.thinking_budget, route.reasoning_token_allowance)
    with _clients_lock:
        client = _clients.get(key)
        if client is None:
            client = LLMClient(route.provider, route.base_url, route.model, route.api_key,
                               reasoning_effort=route.reasoning_effort, thinking_budget=route.thinking_budget, reasoning_token_allowance=route.reasoning_token_allowance)
            _clients[key] = client
            log.info("LLM route task=%s provider=%s model=%s", route.task, route.provider, route.model)
        if route.task == "default":
            _instance = client
        return client


@contextmanager
def temporary_model(model: str | None):
    """Temporarily override the active client model for the duration of a block."""

    with model_context(model):
        yield get_client()
