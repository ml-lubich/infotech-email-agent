"""Bounded retry helper for LLM / OCR shots.

Why a private module: the multi-shot pipeline (see
``docs/ARCHITECTURE.md``) runs small models that occasionally hit
transient errors (rate limit, malformed parse, ONNX kernel hiccup).
Defense in depth says: try again a bounded number of times, then let
the existing ``state.fail(...)`` path record the FAIL shot for the AP
human. This module owns the retry policy in ONE place so every shot
gets the same behaviour and the same log format.

Design rules (matches the repo's architectural invariants):
  - Demeter: callers depend only on ``retry_call`` and pass plain
    callables / exception classes. No leaking of OpenAI internals.
  - Strict typing, no ``Any``.
  - No silent fallbacks: every retry attempt logs at INFO; the final
    failure re-raises the last exception verbatim.
  - Deterministic / injectable ``sleep`` so unit tests run instantly
    without monkeypatching ``time``.
"""

from __future__ import annotations

import logging
import time
from typing import Callable, Final, TypeVar

log = logging.getLogger(__name__)

T = TypeVar("T")

# Defaults are conservative on cost: small model retries are cheap, but
# we still cap attempts so a hard outage cannot blow the budget.
DEFAULT_ATTEMPTS: Final[int] = 3
DEFAULT_BASE_DELAY_S: Final[float] = 0.4


def retry_call(
    fn: Callable[[], T],
    *,
    label: str,
    attempts: int = DEFAULT_ATTEMPTS,
    base_delay: float = DEFAULT_BASE_DELAY_S,
    on: tuple[type[BaseException], ...] = (Exception,),
    on_attempt: Callable[[int, BaseException | None], None] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Invoke ``fn`` up to ``attempts`` times with exponential back-off.

    Args:
        fn: Zero-argument callable. Wrap your real call in a lambda so
            you control its arguments.
        label: Short identifier (e.g. ``"verify"``, ``"injection"``,
            ``"ocr"``) used in log lines.
        attempts: Maximum total attempts (>= 1). 1 means "no retry".
        base_delay: Seconds to wait before retry #2. Doubles each step
            (2 → ``2*base_delay``, 3 → ``4*base_delay``, …).
        on: Exception classes that count as transient. Any exception
            outside this tuple is re-raised immediately (no retry).
        on_attempt: Optional callback receiving ``(attempt_index_0,
            exception_or_None)``. ``None`` means the attempt succeeded.
            Used by tests and observability hooks; never used to drive
            retry policy.
        sleep: Injection seam for tests; defaults to ``time.sleep``.

    Returns:
        Whatever ``fn`` returns on its first successful attempt.

    Raises:
        ValueError: ``attempts < 1``.
        BaseException: the exception raised by the final failed attempt,
            verbatim, after all retries are exhausted (or immediately if
            the exception is not in ``on``).
    """
    if attempts < 1:
        raise ValueError(f"attempts must be >= 1, got {attempts}")

    last_exc: BaseException | None = None
    for i in range(attempts):
        try:
            result = fn()
        except BaseException as exc:  # noqa: BLE001 — re-raised below
            if not isinstance(exc, on):
                # Programmer error / non-transient; do not retry.
                log.warning(
                    "retry label=%s attempt=%d/%d non_transient=%s reraising",
                    label, i + 1, attempts, type(exc).__name__,
                )
                raise
            last_exc = exc
            if on_attempt is not None:
                on_attempt(i, exc)
            if i == attempts - 1:
                log.warning(
                    "retry label=%s attempt=%d/%d FINAL exc=%s: %s",
                    label, i + 1, attempts, type(exc).__name__, exc,
                )
                raise
            delay = base_delay * (2 ** i)
            log.info(
                "retry label=%s attempt=%d/%d transient=%s delay_s=%.2f",
                label, i + 1, attempts, type(exc).__name__, delay,
            )
            sleep(delay)
            continue
        if on_attempt is not None:
            on_attempt(i, None)
        if i > 0:
            log.info(
                "retry label=%s attempt=%d/%d RECOVERED",
                label, i + 1, attempts,
            )
        return result

    # Unreachable: the loop above either returns or re-raises.
    raise RuntimeError(  # pragma: no cover
        f"retry_call({label!r}) exited loop without result; last={last_exc!r}"
    )
