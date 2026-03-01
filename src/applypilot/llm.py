"""Unified LLM client for ApplyPilot.

Auto-detects provider from environment:
  GEMINI_API_KEY  -> Google Gemini (AI Studio Generative Language API)
  OPENAI_API_KEY  -> OpenAI
  LLM_URL         -> Local llama.cpp / Ollama compatible endpoint

Optional:
  LLM_PROVIDER    -> Force provider: gemini|openai|local

LLM_MODEL env var overrides the model name for any provider.
"""

import logging
import os
import random
import threading
import time

import httpx

log = logging.getLogger(__name__)

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
    model_override = (os.environ.get("LLM_MODEL", "") or "").strip()
    provider_override = (os.environ.get("LLM_PROVIDER", "") or "").strip().lower()

    gemini_key = (os.environ.get("GEMINI_API_KEY", "") or "").strip()
    openai_key = (os.environ.get("OPENAI_API_KEY", "") or "").strip()
    local_url = (os.environ.get("LLM_URL", "") or "").strip()

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
            return ("local", local_url.rstrip("/"), model_override or "local-model", os.environ.get("LLM_API_KEY", ""))

        raise RuntimeError("Invalid LLM_PROVIDER. Use: gemini, openai, local")

    # Default ordering:
    # 1) explicit local endpoint
    if local_url:
        return (
            "local",
            local_url.rstrip("/"),
            model_override or "local-model",
            os.environ.get("LLM_API_KEY", ""),
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

    raise RuntimeError(
        "No LLM provider configured. Set GEMINI_API_KEY, OPENAI_API_KEY, or LLM_URL in your environment."
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

    def __init__(self, provider: str, base_url: str, model: str, api_key: str) -> None:
        self.provider = provider
        self.base_url = base_url
        self.model = model
        self.api_key = api_key
        self._client = httpx.Client(timeout=_TIMEOUT)

    # -- public API ---------------------------------------------------------

    def chat(
        self,
        messages: list[dict],
        temperature: float = 0.0,
        max_tokens: int = 4096,
        response_mime_type: str | None = None,
        thinking_budget: int | None = None,
    ) -> str:
        """Send a chat completion request and return the assistant message text."""
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
                thinking_budget=thinking_budget,
            )

        return self._chat_openai_compat(messages, temperature=temperature, max_tokens=max_tokens)

    def _chat_openai_compat(self, messages: list[dict], temperature: float, max_tokens: int) -> str:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        for attempt in range(_MAX_RETRIES):
            try:
                _throttle()
                resp = self._client.post(
                    f"{self.base_url}/chat/completions",
                    json=payload,
                    headers=headers,
                )
                if resp.status_code in (429, 503) and attempt < _MAX_RETRIES - 1:
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
                return data["choices"][0]["message"]["content"]
            except httpx.TimeoutException:
                if attempt < _MAX_RETRIES - 1:
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

        # AI Studio Gemini API key is passed as query parameter.
        params = {"key": self.api_key}
        url = f"{self.base_url}/v1beta/models/{_pick_gemini_model(self.model)}:generateContent"

        for attempt in range(_MAX_RETRIES):
            try:
                _throttle()
                resp = self._client.post(
                    url,
                    params=params,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                )

                # Model not available for new users: fall back to a newer model.
                if resp.status_code == 404 and attempt == 0:
                    try:
                        msg = (resp.json() or {}).get("error", {}).get("message", "")
                    except Exception:
                        msg = ""
                    if "no longer available" in (msg or "").lower() or "not available" in (msg or "").lower():
                        fallback = os.environ.get("GEMINI_FALLBACK_MODEL", "gemini-2.5-flash")
                        fallback = _pick_gemini_model(fallback)
                        if fallback != _pick_gemini_model(self.model):
                            log.warning("Gemini model %s unavailable; falling back to %s", self.model, fallback)
                            self.model = fallback
                            url = f"{self.base_url}/v1beta/models/{_pick_gemini_model(self.model)}:generateContent"
                            # retry immediately on the new model
                            continue

                if resp.status_code in (429, 503) and attempt < _MAX_RETRIES - 1:
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
                texts = [p.get("text", "") for p in parts if isinstance(p, dict)]
                return "".join(texts).strip()
            except httpx.TimeoutException:
                if attempt < _MAX_RETRIES - 1:
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

    def ask(self, prompt: str, **kwargs) -> str:
        """Convenience: single user prompt -> assistant response."""
        return self.chat([{"role": "user", "content": prompt}], **kwargs)


def chat_json(messages: list[dict], temperature: float = 0.0, max_tokens: int = 2048) -> str:
    """Request JSON output when supported by the provider."""
    client = get_client()
    # Gemini supports responseMimeType.
    if (client.provider or "").lower() == "gemini":
        # Default: disable thinking to avoid MAX_TOKENS truncation in JSON mode.
        tb_raw = (os.environ.get("GEMINI_JSON_THINKING_BUDGET", "0") or "0").strip()
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
    return client.chat(messages, temperature=temperature, max_tokens=max_tokens)


def close_client() -> None:
    """Close the shared HTTP client (optional)."""
    global _instance
    if _instance is not None:
        _instance._client.close()
        _instance = None


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_instance: LLMClient | None = None


def get_client() -> LLMClient:
    """Return (or create) the module-level LLMClient singleton."""
    global _instance
    if _instance is None:
        provider, base_url, model, api_key = _detect_provider()
        log.info("LLM provider: %s  base_url: %s  model: %s", provider, base_url, model)
        _instance = LLMClient(provider, base_url, model, api_key)
    return _instance
