"""The confidence ledger.

One run = six steps ("shots"), in order. Each shot writes one row
into a ``PipelineState`` and moves a single number called
``confidence`` (clamped to ``[0.0, 1.0]``, starts at 0.50). Every
row becomes one structured log line and is also embedded in the
final ``outbound_email.json`` so the dashboard can render the full
timeline.

The six shots, in order (driven by ``invoice_agent.agent.run_intake``):

  0. ``pre_flight``         — regex scan of the email body.
  1. ``extract``            — observation of what the agent emitted.
  2. ``arithmetic_check``   — totals, dates, currency format.
  3. ``critic_review``      — small-LLM critic (skipped if no client).
  4. ``injection_screen``   — small-LLM injection scan (skipped if no client).
  5. ``synthesis_finalise`` — rewrite outbound files with the banner.

Confidence math (kept boring on purpose so tests can pin it down):

    start                            = 0.50
    PASS  (deterministic shot)       = +0.10
    PASS  (LLM shot)                 = +0.10
    FLAG  (deterministic, per find)  = -0.10, capped at -0.20 per shot
    FLAG  (LLM, per find)            = -0.05, capped at -0.15 per shot
    FAIL  (any shot)                 = -0.30
    SKIPPED                          =  0.00

Note on PASS symmetry: deterministic and LLM PASS rewards are equal
(+0.10) so a fully clean run hits 1.00 by shot 6. FLAG penalties are
still asymmetric — LLM flags get a smaller per-finding bite (-0.05)
and a tighter per-shot cap (-0.15) because weak models are noisier
than deterministic guardrails. Upstream callers (see
``agent._do_critic_review`` / ``agent._do_injection_screen``) drop
LLM findings that lack a citable evidence quote BEFORE recording, so
the ledger only sees flags that point to real document text.

Callers only touch ``PipelineState.record`` / ``.skip`` / ``.fail``
and the dataclasses. Adding a new shot is safe; reordering or
removing one is a breaking change and must update
``docs/ARCHITECTURE.md`` and ``docs/WALKTHROUGH.md``.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from typing import Final, Literal

from invoice_agent.schema import Evidence

log = logging.getLogger(__name__)

START_CONFIDENCE: Final[float] = 0.50

_PASS_DELTA_DET: Final[float] = 0.10
_PASS_DELTA_LLM: Final[float] = 0.10
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
    # Optional, additive: per-shot AP-facing pointers back to the exact
    # substring that triggered each finding. Default empty list keeps
    # every previous serialisation byte-identical when no evidence is
    # supplied (see `docs/API.md`, `docs/ARCHITECTURE.md`).
    evidence: list[Evidence] = field(default_factory=list)


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
        evidence: list[Evidence] | None = None,
    ) -> Shot:
        """Record a normal PASS/FLAG outcome and update confidence.

        Empty ``findings`` → PASS. Non-empty → FLAG with capped delta.
        ``evidence`` is optional and defaults to ``[]``; pass per-finding
        ``Evidence`` entries to surface a quote in the dashboard.
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
                evidence=list(evidence or []),
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
            "shots": [self._shot_to_dict(s) for s in self.shots],
        }

    @staticmethod
    def _shot_to_dict(shot: Shot) -> dict[str, object]:
        """Serialise a Shot to JSON-safe primitives.

        ``asdict`` does not recurse into Pydantic ``Evidence`` instances,
        so we hand-convert that field via ``model_dump`` to keep the
        envelope JSON-serialisable.
        """
        d = asdict(shot)
        d["evidence"] = [e.model_dump() for e in shot.evidence]
        return d

    def banner(self) -> str:
        """One-line human banner for the top of ``outbound_email.txt``."""
        return (
            f"Confidence: {self.confidence:.2f} — "
            f"{len(self.shots)} shots, {self.flag_count()} flag(s)"
        )
