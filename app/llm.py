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

from openai import APIConnectionError, APIStatusError, APITimeoutError, InternalServerError, RateLimitError

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

    def acquire(self, tokens: int, on_wait=None):
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
            if on_wait and wait > 2:
                on_wait(f"Waiting {round(wait)}s for the API token budget (LLM_TPM)…")
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


class RequestTooLarge(RuntimeError):
    """HTTP 413: this single request is bigger than the model/plan allows (retrying cannot help)."""

    def __init__(self, message, limit=None, requested=None):
        super().__init__(message)
        self.limit, self.requested = limit, requested


# Largest request (tokens) the provider has told us it accepts; learned from a 413 and shared by
# every run in this process so later runs start small instead of failing again.
learned_limit: int | None = None


def _parse_too_large(exc) -> RequestTooLarge:
    text = str(exc)
    m = re.search(r"Limit (\d+)[^0-9]+Requested (\d+)", text)
    limit, requested = (int(m.group(1)), int(m.group(2))) if m else (None, None)
    return RequestTooLarge(text[:400], limit, requested)


class Usage:
    """Token totals for one agent run (thread-safe enough: one run == one thread)."""

    def __init__(self):
        self.calls = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.retries = 0
        self.key_switches = 0
        self.waited_seconds = 0.0
        self.models: set[str] = set()

    def as_dict(self):
        return {
            "llm_calls": self.calls,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "rate_limit_retries": self.retries,
            "key_switches": self.key_switches,
            "waited_seconds": round(self.waited_seconds, 1),
            "models": sorted(self.models),
        }


_limiter = TokenLimiter(config.LLM_TPM, config.LLM_RPM)
_slots = threading.BoundedSemaphore(max(1, config.LLM_CONCURRENCY))
# Models other than the core one (small model, fallback) are limited by the provider separately, so
# each gets its own window and its own concurrency cap: a burst of narration requests to the small
# model must never eat the core model's tokens-per-minute.
# Models the provider said do not exist for this key (404): never tried again in this process.
_dead_models: set[str] = set()
_side_lanes: dict[str, tuple[TokenLimiter, threading.BoundedSemaphore]] = {}
_lanes_lock = threading.Lock()
_TRANSIENT = (APIConnectionError, APITimeoutError, InternalServerError)


def get_limiter() -> TokenLimiter:
    return _limiter


def _new_lane(model: str):
    if model == config.GROQ_MODEL:
        return TokenLimiter(config.LLM_TPM, config.LLM_RPM), threading.BoundedSemaphore(max(1, config.LLM_CONCURRENCY))
    return (TokenLimiter(config.LLM_TPM_SMALL, config.LLM_RPM_SMALL),
            threading.BoundedSemaphore(max(1, config.LLM_CONCURRENCY_SMALL)))


def _lane(model: str, primary: str, key: int = 0):
    """(limiter, semaphore) for `model` on API key number `key`.

    The first key's core model uses the shared core lane. Every other (key, model) pair gets its own
    window and concurrency cap, because the provider meters each key and each model separately.
    """
    if key == 0 and (model == config.GROQ_MODEL or (model == primary and model != config.GROQ_SMALL_MODEL)):
        return _limiter, _slots
    name = f"{key}:{model}"
    with _lanes_lock:
        if name not in _side_lanes:
            _side_lanes[name] = _new_lane(model)
        return _side_lanes[name]


class ClientPool:
    """Several API keys behind one client: spread load over them and fail over when one is rate limited.

    Note: Groq meters limits per organization, so the keys must come from different accounts/orgs
    to add capacity. Cooldowns are tracked per (key, model) because limits are per model too.
    """

    def __init__(self, keys, factory=None, clock=time.monotonic):
        self.keys = list(dict.fromkeys(k for k in keys if k)) or [None]
        self._factory = factory
        self._clients: dict[int, object] = {}
        self._cool: dict[tuple[int, str], float] = {}
        self._busy = [0] * len(self.keys)
        self._used = [0.0] * len(self.keys)
        self._lock = threading.Lock()
        self._clock = clock

    @classmethod
    def single(cls, client):
        pool = cls([None])
        pool._clients[0] = client
        return pool

    @property
    def size(self) -> int:
        return len(self.keys)

    def _client(self, i: int):
        if i not in self._clients:
            self._clients[i] = self._factory(self.keys[i])
        return self._clients[i]

    def acquire(self, model: str):
        """(index, client, seconds_to_wait): the least busy key that is not cooling down for `model`;
        when every key is cooling, the one that frees up first plus how long to wait for it."""
        with self._lock:
            now = self._clock()
            ready = [i for i in range(self.size) if self._cool.get((i, model), 0) <= now]
            if ready:
                i, wait = min(ready, key=lambda k: (self._busy[k], self._used[k])), 0.0
            else:
                i = min(range(self.size), key=lambda k: self._cool[(k, model)])
                wait = self._cool[(i, model)] - now
            self._busy[i] += 1
            self._used[i] = now
        return i, self._client(i), max(0.0, wait)

    def release(self, i: int):
        with self._lock:
            self._busy[i] = max(0, self._busy[i] - 1)

    def cool(self, i: int, model: str, seconds: float):
        with self._lock:
            self._cool[(i, model)] = self._clock() + seconds

    def has_alternative(self, i: int, model: str) -> bool:
        now = self._clock()
        with self._lock:
            return any(k != i and self._cool.get((k, model), 0) <= now for k in range(self.size))

    def status(self) -> list[dict]:
        now = self._clock()
        with self._lock:
            return [{"key": f"#{i + 1}", "busy": self._busy[i],
                     "cooling": sorted({m for (k, m), t in self._cool.items() if k == i and t > now})}
                    for i in range(self.size)]


_pools: dict[tuple, "ClientPool"] = {}


def get_pool(keys, factory) -> "ClientPool":
    """One shared pool per key set, so concurrent jobs see each other's cooldowns and load."""
    ident = tuple(dict.fromkeys(k for k in keys if k))
    with _lanes_lock:
        pool = _pools.get(ident)
        if pool is None:
            pool = _pools[ident] = ClientPool(ident)
        pool._factory = factory
        pool._clients.clear()  # clients are cheap; state (cooldowns, load) is what must be shared
    return pool


def complete(client, usage: Usage | None = None, sleep=time.sleep, on_wait=None, **kwargs):
    """chat.completions.create with budgeting, key rotation, retry and model fallback."""
    pool = client if isinstance(client, ClientPool) else ClientPool.single(client)
    models = [kwargs.pop("model")]
    primary = models[0]
    if config.GROQ_FALLBACK_MODEL and config.GROQ_FALLBACK_MODEL not in models:
        models.append(config.GROQ_FALLBACK_MODEL)
    estimate = estimate_tokens(kwargs.get("messages")) + estimate_tokens(kwargs.get("tools")) + 1500
    last_exc = None
    for model in models:
        if model in _dead_models and len(models) > 1:
            continue
        attempts = MAX_RETRIES * pool.size
        for attempt in range(attempts):
            idx, cl, cooling = pool.acquire(model)
            if cooling > 0:  # every key is rate limited for this model: wait for the first to recover
                pool.release(idx)
                if on_wait:
                    on_wait(f"All {pool.size} API key(s) are rate limited; waiting {round(cooling + 0.5)}s…")
                if usage:
                    usage.waited_seconds += cooling + 0.5
                sleep(cooling + 0.5)
                idx, cl, _ = pool.acquire(model)
            limiter, slots = _lane(model, primary, idx)
            try:
                entry = limiter.acquire(estimate, on_wait)
                try:
                    with slots:
                        response = cl.chat.completions.create(model=model, **kwargs)
                except RateLimitError as exc:
                    limiter.settle(entry, 0)
                    last_exc = exc
                    delay = retry_after_seconds(exc)
                    if delay is None:
                        delay = min(2 ** (attempt // pool.size) * 5, 60)
                    limiter.penalize(delay)
                    pool.cool(idx, model, delay + 0.5)
                    if pool.has_alternative(idx, model):
                        # Another key is free: switch immediately instead of waiting.
                        if usage:
                            usage.key_switches += 1
                        if on_wait:
                            on_wait(f"API key #{idx + 1} is rate limited; switching to another key.")
                        continue
                    if usage:
                        usage.retries += 1
                        usage.waited_seconds += delay + 0.5
                    if attempt < attempts - 1:
                        if on_wait:
                            on_wait(f"Rate limited by the API; waiting {round(delay + 0.5)}s before retrying…")
                        sleep(delay + 0.5)
                    pool.cool(idx, model, 0)  # we waited it out here; do not wait again in acquire()
                    continue
                except _TRANSIENT as exc:
                    limiter.settle(entry, 0)
                    last_exc = exc
                    delay = min(2 ** (attempt // pool.size), 20)
                    if usage:
                        usage.retries += 1
                        usage.waited_seconds += delay
                    if attempt < attempts - 1:
                        sleep(delay)
                    continue
                except APIStatusError as exc:
                    limiter.settle(entry, 0)
                    if getattr(exc, "status_code", None) == 404:
                        _dead_models.add(model)  # unknown model / no access: use the next one, now and later
                        last_exc = exc
                        break
                    if getattr(exc, "status_code", None) == 413:
                        err = _parse_too_large(exc)
                        global learned_limit
                        if err.limit:
                            learned_limit = err.limit if learned_limit is None else min(learned_limit, err.limit)
                        last_exc = err
                        break  # a retry cannot help; try the fallback model if there is one
                    raise
            finally:
                pool.release(idx)
            u = getattr(response, "usage", None)
            total = getattr(u, "total_tokens", None) if u else None
            limiter.settle(entry, total)
            if usage:
                usage.calls += 1
                usage.models.add(model)
                if u:
                    usage.prompt_tokens += getattr(u, "prompt_tokens", 0) or 0
                    usage.completion_tokens += getattr(u, "completion_tokens", 0) or 0
            return response
    raise last_exc


_THINK = re.compile(r"<think>.*?</think>", re.S)


def small_model() -> str:
    return config.GROQ_SMALL_MODEL or config.GROQ_MODEL


def ask_small(client, system: str, user: str, usage: Usage | None = None, on_wait=None, max_tokens: int = 1500) -> str:
    """One plain-text completion on the small model, falling back to the core model if it fails.

    For work a small model does well and that would otherwise burn core tokens: writing narration,
    rephrasing, short repairs. Returns the text with any <think> block removed.
    """
    errors = []
    for model in dict.fromkeys([small_model(), config.GROQ_MODEL]):
        if model in _dead_models and model != config.GROQ_MODEL:
            continue
        try:
            response = complete(
                client, usage=usage, on_wait=on_wait, model=model, temperature=0.5, max_tokens=max_tokens,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            )
            text = _THINK.sub("", response.choices[0].message.content or "").strip()
            if text:
                return text
            errors.append(f"{model}: empty reply")
        except Exception as exc:  # noqa: BLE001 - any failure of the small model falls through to the next
            errors.append(f"{model}: {str(exc)[:120]}")
    raise RuntimeError("No model produced text. " + " | ".join(errors))
