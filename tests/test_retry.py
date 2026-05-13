"""Tests for the bounded-retry helper used by LLM and OCR shots.

Pinned behavior:
- success on attempt 1 → 1 call, no sleep.
- success on attempt 2 → 2 calls, 1 sleep at base_delay.
- failure on all 3 attempts → raises the last exception, 2 sleeps.
- non-matching exception class → re-raises immediately, no retry.
- attempts < 1 raises ValueError up front.
- on_attempt callback fires for each attempt with the exception (or None
  on success), so observability hooks can record retries.
"""

from __future__ import annotations

from typing import Any

import pytest

from invoice_agent._retry import retry_call


class _Counter:
    """Test double that tracks how many times it was called."""

    def __init__(self, *, succeed_on: int, exc: BaseException | None = None) -> None:
        self.calls = 0
        self.succeed_on = succeed_on
        self.exc = exc or RuntimeError("boom")

    def __call__(self) -> str:
        self.calls += 1
        if self.calls < self.succeed_on:
            raise self.exc
        return "ok"


def test_success_on_first_attempt_no_sleep() -> None:
    sleeps: list[float] = []
    fn = _Counter(succeed_on=1)
    out = retry_call(fn, label="t1", sleep=sleeps.append)
    assert out == "ok"
    assert fn.calls == 1
    assert sleeps == []


def test_success_on_second_attempt_sleeps_once() -> None:
    sleeps: list[float] = []
    fn = _Counter(succeed_on=2)
    out = retry_call(fn, label="t2", base_delay=0.5, sleep=sleeps.append)
    assert out == "ok"
    assert fn.calls == 2
    # base_delay * 2**0 = 0.5
    assert sleeps == [0.5]


def test_exhausted_attempts_reraises_last_exception() -> None:
    sleeps: list[float] = []
    final = ValueError("final boom")
    fn = _Counter(succeed_on=99, exc=final)
    with pytest.raises(ValueError, match="final boom"):
        retry_call(fn, label="t3", attempts=3, base_delay=0.5, sleep=sleeps.append)
    assert fn.calls == 3
    # Two backoff sleeps between three attempts: 0.5, then 1.0.
    assert sleeps == [0.5, 1.0]


def test_non_transient_exception_is_not_retried() -> None:
    sleeps: list[float] = []
    fn = _Counter(succeed_on=99, exc=KeyError("schema bug"))
    with pytest.raises(KeyError):
        retry_call(
            fn,
            label="t4",
            attempts=5,
            on=(RuntimeError,),
            sleep=sleeps.append,
        )
    assert fn.calls == 1
    assert sleeps == []


def test_attempts_less_than_one_rejected() -> None:
    with pytest.raises(ValueError, match="attempts must be >= 1"):
        retry_call(lambda: "x", label="t5", attempts=0)


def test_on_attempt_callback_records_each_attempt() -> None:
    events: list[tuple[int, str | None]] = []

    def hook(i: int, exc: BaseException | None) -> None:
        events.append((i, type(exc).__name__ if exc else None))

    sleeps: list[float] = []
    fn = _Counter(succeed_on=2)
    retry_call(fn, label="t6", on_attempt=hook, sleep=sleeps.append)
    assert events == [(0, "RuntimeError"), (1, None)]


def test_attempts_one_means_no_retry() -> None:
    sleeps: list[float] = []
    fn = _Counter(succeed_on=99)
    with pytest.raises(RuntimeError):
        retry_call(fn, label="t7", attempts=1, sleep=sleeps.append)
    assert fn.calls == 1
    assert sleeps == []
