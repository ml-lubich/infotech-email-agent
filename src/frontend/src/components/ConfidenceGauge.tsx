import type { PipelineEnvelope, PipelineShot } from "../types";

interface Props {
    envelope: PipelineEnvelope | undefined;
}

function gaugeColor(pct: number): string {
    if (pct >= 75) return "var(--good)";
    if (pct >= 50) return "var(--warn)";
    return "var(--bad)";
}

function shotClass(shot: PipelineShot): string {
    const d = (shot.decision ?? "").toUpperCase();
    if (d === "PASS") return "pass";
    if (d === "FLAG") return "flag";
    if (d === "FAIL") return "fail";
    return "skipped";
}

function shotConfidence(shot: PipelineShot): number {
    // Per-shot confidence is optional in the envelope; default to a small
    // visible bar so SKIPPED rows still render as a track.
    const c = (shot as unknown as { confidence?: number }).confidence;
    if (typeof c === "number" && Number.isFinite(c)) return Math.max(0, Math.min(1, c));
    return shotClass(shot) === "skipped" ? 0.12 : 0.65;
}

export function ConfidenceGauge({ envelope }: Props) {
    const confidence = envelope?.confidence ?? 0;
    const pct = Math.round(confidence * 100);
    const flagCount = envelope?.flag_count ?? 0;
    const shots = envelope?.shots ?? [];
    const shotCount = shots.length;
    const passes = shots.filter((s) => s.decision === "PASS").length;

    return (
        <div className="card">
            <h2>Pipeline confidence</h2>
            <div className="confidence-card">
                <div
                    className="gauge"
                    style={
                        {
                            ["--pct" as string]: String(pct),
                            ["--gauge-color" as string]: gaugeColor(pct),
                        } as React.CSSProperties
                    }
                >
                    <div className="gauge-inner">
                        <div className="gauge-value">{pct}%</div>
                        <div className="gauge-label">confidence</div>
                    </div>
                </div>
                <div className="confidence-meta">
                    <div className="meta-tile">
                        <div className="k">Shots</div>
                        <div className="v">{shotCount}</div>
                    </div>
                    <div className="meta-tile">
                        <div className="k">Passes</div>
                        <div className="v" style={{ color: "var(--good)" }}>
                            {passes}
                        </div>
                    </div>
                    <div className="meta-tile">
                        <div className="k">Flags</div>
                        <div className="v" style={{ color: flagCount > 0 ? "var(--warn)" : "var(--text-1)" }}>
                            {flagCount}
                        </div>
                    </div>
                </div>
            </div>

            {shots.length > 0 && (
                <div className="sparkline" aria-label="Per-shot confidence">
                    <div className="head">
                        <span className="label">Per-shot trace</span>
                        <span className="legend">
                            <span className="lg-pass">pass</span>
                            <span className="lg-flag">flag</span>
                            <span className="lg-fail">fail</span>
                            <span className="lg-skip">skip</span>
                        </span>
                    </div>
                    <p
                        style={{
                            color: "var(--text-2)",
                            fontSize: 12,
                            margin: "2px 0 8px",
                            lineHeight: 1.4,
                        }}
                    >
                        Bar height = running confidence after that shot. Hover any bar for the
                        decision. Confidence starts at 50%; deterministic PASS adds 10pp,
                        LLM PASS adds 5pp, FAIL drops 30pp.
                    </p>
                    <div className="bars">
                        {shots.map((s, i) => {
                            const c = shotConfidence(s);
                            const heightPct = Math.max(8, Math.round(c * 100));
                            const decision = (s.decision ?? "—").toString();
                            return (
                                <div
                                    key={`${s.name}-${i}`}
                                    className={`bar ${shotClass(s)}`}
                                    style={{ height: `${heightPct}%` }}
                                    title={`${s.name}: ${decision} (${Math.round(c * 100)}%)`}
                                >
                                    <span className="tip">
                                        {s.name} · {decision} · {Math.round(c * 100)}%
                                    </span>
                                </div>
                            );
                        })}
                    </div>
                    <div className="axis">
                        {shots.map((s, i) => (
                            <span className="tick" key={`tick-${i}`} title={s.name}>
                                {s.name.replace(/^pass[_\s]?/i, "")}
                            </span>
                        ))}
                    </div>
                </div>
            )}
        </div>
    );
}
