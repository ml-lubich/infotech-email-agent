"""Multi-shot orchestration with progressive confidence scoring.

This module owns the **pipeline** abstraction: a fixed ordered sequence of
"shots" that each contribute to a running confidence score in
``[0.0, 1.0]``. Every shot emits one structured log line and produces zero
or more snake_case findings that are merged into the final artefact's
``risk_flags``.

Shots in order (driven by ``invoice_agent.agent.run_intake``):

  0. ``pre_flight``        — deterministic email scan (regex injection,
                              attachment validation).
  1. ``extract``           — LLM (vision) observation, recorded from the
                              agent's emitted payload (no extra LLM call).
  2. ``arithmetic_check``  — deterministic math + format checks.
  3. ``critic_review``     — LLM (gpt-5-nano) cross-checks JSON vs raw
                              PDF text. SKIPPED when no client is given.
  4. ``injection_screen``  — LLM (gpt-5-nano) dedicated injection scan.
                              SKIPPED when no client is given.
  5. ``synthesis_finalise`` — deterministic rewrite of outbound files
                              with the confidence banner + envelope.

Decision math (deterministic, easy to test):

    start                           = 0.50
    PASS  (deterministic shot)      = +0.10
    PASS  (LLM shot)                = +0.05
    FLAG  (deterministic, per find) = -0.10, capped at -0.20 per shot
    FLAG  (LLM, per find)           = -0.05, capped at -0.15 per shot
    FAIL  (any shot)                = -0.30
    SKIPPED                         =  0.00

Architectural notes:
  - Demeter: callers depend only on ``PipelineState.record`` /
    ``PipelineState.skip`` and the dataclasses; they do not poke at
    private fields.
  - Append-only: adding a new shot is a minor change; reordering /
    removing is breaking and must update ``docs/ARCHITECTURE.md``.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from typing import Final, Literal

log = logging.getLogger(__name__)

START_CONFIDENCE: Final[float] = 0.50

_PASS_DELTA_DET: Final[float] = 0.10
_PASS_DELTA_LLM: Final[float] = 0.05
_FLAG_DELTA_PER_DET: Final[float] = -0.10
_FLAG_DELTA_PER_LLM: Final[float] = -0.05
_FLAG_DELTA_CAP_DET: Final[float] = -0.20
_FLAG_DELTA_CAP_LLM: Final[float] = -0.15
_FAIL_DELTA: Final[float] = -0.30

ShotKind = Literal["deterministic", "llm"]
ShotDecision = Literal["PASS", "FLAG", "FAIL", "SKIPPED"]


@dataclass(frozen=True)
class Shot:
    """One immutable record of a pipeline decision point."""

    name: str
    kind: ShotKind
    model: str  # empty string for deterministic shots
    decision: ShotDecision
    confidence_before: float
    delta: float
    confidence_after: float
    findings: list[str]


@dataclass
class PipelineState:
    """Mutable confidence ledger; emit a frozen ``to_envelope`` at the end."""

    confidence: float = START_CONFIDENCE
    shots: list[Shot] = field(default_factory=list)

    # ---- internal helpers ------------------------------------------------

    @staticmethod
    def _clamp(x: float) -> float:
        return max(0.0, min(1.0, x))

    def _push(self, shot: Shot) -> Shot:
        self.shots.append(shot)
        log.info(
            "shot=%d name=%s kind=%s model=%s decision=%s "
            "confidence_before=%.2f delta=%+.2f confidence_after=%.2f findings=%s",
            len(self.shots) - 1,
            shot.name,
            shot.kind,
            shot.model or "-",
            shot.decision,
            shot.confidence_before,
            shot.delta,
            shot.confidence_after,
            shot.findings,
        )
        return shot

    # ---- public API ------------------------------------------------------

    def record(
        self,
        name: str,
        kind: ShotKind,
        model: str,
        findings: list[str],
    ) -> Shot:
        """Record a normal PASS/FLAG outcome and update confidence.

        Empty ``findings`` → PASS. Non-empty → FLAG with capped delta.
        """
        before = self.confidence
        if findings:
            per = _FLAG_DELTA_PER_DET if kind == "deterministic" else _FLAG_DELTA_PER_LLM
            cap = _FLAG_DELTA_CAP_DET if kind == "deterministic" else _FLAG_DELTA_CAP_LLM
            delta = max(cap, per * len(findings))
            decision: ShotDecision = "FLAG"
        else:
            delta = _PASS_DELTA_DET if kind == "deterministic" else _PASS_DELTA_LLM
            decision = "PASS"
        after = self._clamp(before + delta)
        self.confidence = after
        return self._push(
            Shot(
                name=name,
                kind=kind,
                model=model,
                decision=decision,
                confidence_before=round(before, 2),
                delta=round(delta, 2),
                confidence_after=round(after, 2),
                findings=list(findings),
            )
        )

    def skip(self, name: str, kind: ShotKind, model: str, reason: str) -> Shot:
        """Record a SKIPPED shot. Confidence does not move."""
        before = self.confidence
        return self._push(
            Shot(
                name=name,
                kind=kind,
                model=model,
                decision="SKIPPED",
                confidence_before=round(before, 2),
                delta=0.0,
                confidence_after=round(before, 2),
                findings=[f"skipped:{reason}"],
            )
        )

    def fail(self, name: str, kind: ShotKind, model: str, error: str) -> Shot:
        """Record a FAIL outcome. Confidence drops by a fixed amount but the
        pipeline keeps going so the AP human still sees partial evidence.
        """
        before = self.confidence
        delta = _FAIL_DELTA
        after = self._clamp(before + delta)
        self.confidence = after
        return self._push(
            Shot(
                name=name,
                kind=kind,
                model=model,
                decision="FAIL",
                confidence_before=round(before, 2),
                delta=round(delta, 2),
                confidence_after=round(after, 2),
                findings=[f"error:{error}"],
            )
        )

    # ---- serialisation ---------------------------------------------------

    def all_findings(self) -> list[str]:
        """Flat list of every finding tag from every shot, in order, deduped."""
        seen: dict[str, None] = {}
        for s in self.shots:
            for tag in s.findings:
                # Skip housekeeping tags that should not become risk_flags.
                if tag.startswith("skipped:") or tag.startswith("error:"):
                    continue
                seen.setdefault(tag, None)
        return list(seen.keys())

    def flag_count(self) -> int:
        return sum(1 for s in self.shots if s.decision in ("FLAG", "FAIL"))

    def to_envelope(self) -> dict[str, object]:
        """Serialise for embedding in ``outbound_email.json``."""
        return {
            "confidence": round(self.confidence, 2),
            "flag_count": self.flag_count(),
            "shots": [asdict(s) for s in self.shots],
        }

    def banner(self) -> str:
        """One-line human banner for the top of ``outbound_email.txt``."""
        return (
            f"Confidence: {self.confidence:.2f} — "
            f"{len(self.shots)} shots, {self.flag_count()} flag(s)"
        )
