"""Per-run token-usage observability.

Every LLM shot records its OpenAI ``response.usage`` block into a single
``UsageMeter`` instance owned by ``_IntakeRun``. The meter:

  - emits one structured ``usage shot=...`` log line per recorded shot,
  - emits a single ``usage_total ...`` summary line at the end of the
    pipeline,
  - serialises totals + per-shot breakdown into the outbound JSON under
    ``payload["usage"]`` (sibling of ``payload["pipeline"]``).

Why a side-channel file for the extract tool: the extractor runs INSIDE
the Agents SDK ``Runner.run_sync`` loop, so we cannot pass a callback
directly. We mirror the ``OUT_DIR_ENV`` pattern: the tool writes
``usage_extract.json`` to the run's out-dir; ``_IntakeRun`` reads it
back after the agent loop finishes.

Architecture notes:
  - DIP: callers depend on ``UsageMeter.record_response`` (and its
    callable form) only.
  - Demeter: the OpenAI Response/Usage shape is read entirely inside
    ``extract_usage``; nothing else pokes at SDK internals.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Final

log = logging.getLogger(__name__)

# Sibling of OUT_DIR_ENV: this filename inside the run's out-dir is the
# side-channel the extract tool uses to publish its usage block.
USAGE_EXTRACT_FILENAME: Final[str] = "usage_extract.json"


@dataclass(frozen=True)
class ShotUsage:
    """One immutable record of token usage for one LLM shot."""

    shot: str
    model: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cached_input_tokens: int = 0
    reasoning_tokens: int = 0


def _safe_int(obj: object, attr: str) -> int:
    """Return ``int(obj.attr)`` if it's numeric, else 0. Pure defensive."""
    if obj is None:
        return 0
    val = getattr(obj, attr, None)
    if isinstance(val, bool):
        return 0
    if isinstance(val, (int, float)):
        return int(val)
    return 0


def extract_usage(response: object) -> dict[str, int]:
    """Pull token counts from an OpenAI Responses-API response.

    Returns ``{}`` when the response carries no ``usage`` block (e.g.
    a stub response in tests). Tolerates the two SDK shapes we have
    seen in the wild (``input_tokens_details.cached_tokens`` and
    ``output_tokens_details.reasoning_tokens``).
    """
    usage = getattr(response, "usage", None)
    if usage is None:
        return {}
    out: dict[str, int] = {
        "input_tokens": _safe_int(usage, "input_tokens"),
        "output_tokens": _safe_int(usage, "output_tokens"),
        "total_tokens": _safe_int(usage, "total_tokens"),
    }
    in_details = getattr(usage, "input_tokens_details", None)
    cached = _safe_int(in_details, "cached_tokens")
    if cached:
        out["cached_input_tokens"] = cached
    out_details = getattr(usage, "output_tokens_details", None)
    reasoning = _safe_int(out_details, "reasoning_tokens")
    if reasoning:
        out["reasoning_tokens"] = reasoning
    return out


@dataclass
class UsageMeter:
    """Mutable per-run usage ledger; emit ``as_envelope`` at the end."""

    shots: list[ShotUsage] = field(default_factory=list)

    # ---- recording --------------------------------------------------

    def record_response(self, shot: str, model: str, response: object) -> None:
        """Extract + record usage from a raw Responses-API response."""
        u = extract_usage(response)
        if not u:
            log.info("usage shot=%s model=%s usage=unavailable", shot, model)
            return
        self._push(
            ShotUsage(
                shot=shot,
                model=model,
                input_tokens=u.get("input_tokens", 0),
                output_tokens=u.get("output_tokens", 0),
                total_tokens=u.get("total_tokens", 0),
                cached_input_tokens=u.get("cached_input_tokens", 0),
                reasoning_tokens=u.get("reasoning_tokens", 0),
            )
        )

    def record_dict(self, shot: str, model: str, usage: dict[str, int]) -> None:
        """Record a previously-extracted usage dict (e.g. from the
        side-channel file written by the extract tool)."""
        if not usage:
            log.info("usage shot=%s model=%s usage=unavailable", shot, model)
            return
        self._push(
            ShotUsage(
                shot=shot,
                model=model,
                input_tokens=int(usage.get("input_tokens", 0) or 0),
                output_tokens=int(usage.get("output_tokens", 0) or 0),
                total_tokens=int(usage.get("total_tokens", 0) or 0),
                cached_input_tokens=int(usage.get("cached_input_tokens", 0) or 0),
                reasoning_tokens=int(usage.get("reasoning_tokens", 0) or 0),
            )
        )

    def sink_for(self, shot: str, model: str) -> Callable[[object], None]:
        """Return a one-arg callback that records into this meter.

        Convenient for handing to ``verify_extraction(usage_sink=...)``
        without leaking the meter object across module boundaries.
        """
        def _sink(response: object) -> None:
            self.record_response(shot, model, response)
        return _sink

    def _push(self, rec: ShotUsage) -> None:
        self.shots.append(rec)
        log.info(
            "usage shot=%s model=%s input=%d output=%d total=%d "
            "cached_in=%d reasoning_out=%d",
            rec.shot, rec.model,
            rec.input_tokens, rec.output_tokens, rec.total_tokens,
            rec.cached_input_tokens, rec.reasoning_tokens,
        )

    # ---- aggregation ------------------------------------------------

    def totals(self) -> dict[str, int]:
        return {
            "input_tokens": sum(s.input_tokens for s in self.shots),
            "output_tokens": sum(s.output_tokens for s in self.shots),
            "total_tokens": sum(s.total_tokens for s in self.shots),
            "cached_input_tokens": sum(s.cached_input_tokens for s in self.shots),
            "reasoning_tokens": sum(s.reasoning_tokens for s in self.shots),
        }

    def cache_hit_ratio(self) -> float:
        """Fraction of input tokens served from prompt cache (0.0–1.0)."""
        t = self.totals()
        denom = t["input_tokens"]
        return (t["cached_input_tokens"] / denom) if denom else 0.0

    def as_envelope(self) -> dict[str, object]:
        """Serialise for embedding under ``payload["usage"]``."""
        return {
            "totals": self.totals(),
            "cache_hit_ratio": round(self.cache_hit_ratio(), 4),
            "shots": [asdict(s) for s in self.shots],
        }

    # ---- summary log line ------------------------------------------

    def log_summary(self, log_: logging.Logger) -> None:
        t = self.totals()
        log_.info(
            "usage_total shots=%d input=%d output=%d total=%d "
            "cached_in=%d reasoning_out=%d cache_hit_ratio=%.2f",
            len(self.shots),
            t["input_tokens"], t["output_tokens"], t["total_tokens"],
            t["cached_input_tokens"], t["reasoning_tokens"],
            self.cache_hit_ratio(),
        )


# --- side-channel helpers (extract tool ↔ _IntakeRun) -----------------


def write_extract_usage(out_dir: Path, model: str, usage: dict[str, int]) -> None:
    """Persist the extract shot's usage block for the orchestrator to pick up.

    Failures are logged (not silent) but never raised — observability is
    best-effort and must not break the pipeline.
    """
    try:
        path = out_dir / USAGE_EXTRACT_FILENAME
        path.write_text(
            json.dumps({"model": model, "usage": usage}, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError as exc:
        log.warning("usage: could not write %s: %s", USAGE_EXTRACT_FILENAME, exc)


def read_extract_usage(out_dir: Path) -> tuple[str, dict[str, int]] | None:
    """Read the side-channel file. Returns ``None`` if absent or unreadable."""
    path = out_dir / USAGE_EXTRACT_FILENAME
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("usage: could not read %s: %s", USAGE_EXTRACT_FILENAME, exc)
        return None
    if not isinstance(data, dict):
        return None
    model = str(data.get("model") or "")
    usage = data.get("usage") or {}
    if not isinstance(usage, dict):
        usage = {}
    return model, {k: int(v) for k, v in usage.items() if isinstance(v, (int, float))}
