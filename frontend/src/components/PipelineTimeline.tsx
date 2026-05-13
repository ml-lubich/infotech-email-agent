import type { PipelineShot } from "../types";

interface Props {
    shots: PipelineShot[];
}

function rowClass(decision: string): string {
    switch (decision) {
        case "PASS":
            return "shot-row pass";
        case "FLAG":
            return "shot-row flag";
        case "FAIL":
            return "shot-row fail";
        default:
            return "shot-row skipped";
    }
}

function fmtDelta(delta: number): string {
    if (delta === 0) return "±0";
    const sign = delta > 0 ? "+" : "";
    return `${sign}${delta.toFixed(2)}`;
}

export function PipelineTimeline({ shots }: Props) {
    if (shots.length === 0) {
        return (
            <div className="card">
                <h2>Pipeline shots</h2>
                <p style={{ color: "var(--text-2)", fontSize: 13, margin: 0 }}>
                    No pipeline trace embedded in this run.
                </p>
            </div>
        );
    }
    return (
        <div className="card">
            <h2>Pipeline shots</h2>
            <div className="timeline">
                {shots.map((shot) => (
                    <div key={shot.name} className={rowClass(shot.decision)}>
                        <div className="badge" />
                        <div>
                            <div className="name">{shot.name}</div>
                            <div className="meta">
                                {shot.kind}
                                {shot.model ? ` · ${shot.model}` : ""} · {shot.decision}
                            </div>
                            {shot.findings.length > 0 && (
                                <div className="findings">
                                    {shot.findings.map((f) => (
                                        <span key={f} className="chip warn">
                                            {f}
                                        </span>
                                    ))}
                                </div>
                            )}
                            {shot.evidence && shot.evidence.length > 0 && (
                                <div className="evidence">
                                    {shot.evidence.map((e, i) => (
                                        <div key={`${e.finding}-${i}`} className="evidence-row">
                                            <div className="evidence-meta">
                                                <span className="evidence-finding">{e.finding}</span>
                                                <span className="evidence-source">
                                                    {e.source}
                                                    {e.location ? ` · ${e.location}` : ""}
                                                </span>
                                            </div>
                                            <blockquote className="evidence-quote">{e.quote}</blockquote>
                                        </div>
                                    ))}
                                </div>
                            )}
                        </div>
                        <div className="delta">
                            {fmtDelta(shot.delta)}
                            <span className="conf">
                                {(shot.confidence_before * 100).toFixed(0)}% →{" "}
                                {(shot.confidence_after * 100).toFixed(0)}%
                            </span>
                        </div>
                    </div>
                ))}
            </div>
        </div>
    );
}
