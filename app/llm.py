"""Shared LLM access layer: one place that decides when and how a request may be sent.

The Groq API is the bottleneck of the whole pipeline (token-per-minute limits, 429s), and
every agent run makes many calls. This module gives all runs, in all threads, one budget:

* TokenLimiter: rolling 60-second window of requests/tokens, so calls wait *before* being
  sent instead of being rejected and retried (a rejected call still costs a round trip).
* a concurrency cap, so parallel jobs queue instead of racing each other into 429s;
* retry that honours the server's own "try again in 1m5.2s" hint, plus transient-error backoff;
* an optional fallback model once the primary one keeps failing;
* usage accounting (prompt/completion tokens) that is reported back to the job.
"""
import json
import re
import threading
import time
from collections import deque

from openai import APIConnectionError, APITimeoutError, InternalServerError, RateLimitError

from . import config

WINDOW_SECONDS = 60.0
MAX_RETRIES = 6


def estimate_tokens(obj) -> int:
    """Cheap upper-ish estimate (~3.5 chars/token for code-heavy JSON); settled with real usage."""
    try:
        text = obj if isinstance(obj, str) else json.dumps(obj, default=str)
    except (TypeError, ValueError):
        text = str(obj)
    return int(len(text) / 3.5) + 1


def retry_after_seconds(exc: Exception) -> float | None:
    """Parse 'Please try again in 1m5.2s' / '5.895s' / '350ms' from a 429 body."""
    text = str(exc)
    m = re.search(r"try again in (?:(\d+)m(?!s))?(?:([\d.]+)s)?(?:([\d.]+)ms)?", text, re.IGNORECASE)
    if not m or not any(m.groups()):
        return None
    minutes, seconds, millis = m.groups()
    return float(minutes or 0) * 60 + float(seconds or 0) + float(millis or 0) / 1000


class TokenLimiter:
    """Blocks callers so that at most `tpm` tokens and `rpm` requests start per rolling minute.

    A limit of 0 disables that dimension. A single request larger than `tpm` is let through
    once the window is empty (otherwise it could never run).
    """

    def __init__(self, tpm: int = 0, rpm: int = 0, clock=time.monotonic, sleep=time.sleep):
        self.tpm, self.rpm = tpm, rpm
        self._clock, self._sleep = clock, sleep
        self._lock = threading.Lock()
        self._window: deque[list] = deque()  # [timestamp, tokens]

    def _purge(self, now):
        while self._window and now - self._window[0][0] >= WINDOW_SECONDS:
            self._window.popleft()

    def acquire(self, tokens: int):
        """Reserve `tokens`; returns a handle for settle(). Sleeps (outside the lock) as needed."""
        while True:
            with self._lock:
                now = self._clock()
                self._purge(now)
                used = sum(t for _, t in self._window)
                over_tokens = self.tpm and self._window and used + tokens > self.tpm
                over_requests = self.rpm and len(self._window) >= self.rpm
                if not over_tokens and not over_requests:
                    entry = [now, tokens]
                    self._window.append(entry)
                    return entry
                wait = WINDOW_SECONDS - (now - self._window[0][0])
            self._sleep(max(wait, 0.05) + 0.05)

    def settle(self, entry, actual_tokens: int | None):
        """Replace the estimate with the real token count once the response is known."""
        if actual_tokens is not None:
            with self._lock:
                entry[1] = actual_tokens

    def penalize(self, seconds: float):
        """After a 429, nothing else should start until the server's window has passed."""
        with self._lock:
            now = self._clock()
            self._window.append([now - WINDOW_SECONDS + seconds, self.tpm or 1])


class Usage:
    """Token totals for one agent run (thread-safe enough: one run == one thread)."""

    def __init__(self):
        self.calls = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.retries = 0
        self.waited_seconds = 0.0
        self.models: set[str] = set()

    def as_dict(self):
        return {
            "llm_calls": self.calls,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "rate_limit_retries": self.retries,
            "waited_seconds": round(self.waited_seconds, 1),
            "models": sorted(self.models),
        }


_limiter = TokenLimiter(config.LLM_TPM, config.LLM_RPM)
_slots = threading.BoundedSemaphore(max(1, config.LLM_CONCURRENCY))
_TRANSIENT = (APIConnectionError, APITimeoutError, InternalServerError)


def get_limiter() -> TokenLimiter:
    return _limiter


def complete(client, usage: Usage | None = None, sleep=time.sleep, **kwargs):
    """chat.completions.create with budgeting, retry and model fallback."""
    models = [kwargs.pop("model")]
    if config.GROQ_FALLBACK_MODEL and config.GROQ_FALLBACK_MODEL not in models:
        models.append(config.GROQ_FALLBACK_MODEL)
    estimate = estimate_tokens(kwargs.get("messages")) + estimate_tokens(kwargs.get("tools")) + 1500
    last_exc = None
    for model in models:
        for attempt in range(MAX_RETRIES):
            entry = _limiter.acquire(estimate)
            try:
                with _slots:
                    response = client.chat.completions.create(model=model, **kwargs)
            except RateLimitError as exc:
                _limiter.settle(entry, 0)
                last_exc = exc
                delay = retry_after_seconds(exc)
                if delay is None:
                    delay = min(2 ** attempt * 5, 60)
                _limiter.penalize(delay)
                if usage:
                    usage.retries += 1
                    usage.waited_seconds += delay + 0.5
                if attempt < MAX_RETRIES - 1:
                    sleep(delay + 0.5)
                continue
            except _TRANSIENT as exc:
                _limiter.settle(entry, 0)
                last_exc = exc
                delay = min(2 ** attempt, 20)
                if usage:
                    usage.retries += 1
                    usage.waited_seconds += delay
                if attempt < MAX_RETRIES - 1:
                    sleep(delay)
                continue
            u = getattr(response, "usage", None)
            total = getattr(u, "total_tokens", None) if u else None
            _limiter.settle(entry, total)
            if usage:
                usage.calls += 1
                usage.models.add(model)
                if u:
                    usage.prompt_tokens += getattr(u, "prompt_tokens", 0) or 0
                    usage.completion_tokens += getattr(u, "completion_tokens", 0) or 0
            return response
    raise last_exc
