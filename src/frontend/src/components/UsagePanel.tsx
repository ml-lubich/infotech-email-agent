import type { UsageEnvelope } from "../types";

interface Props {
    usage?: UsageEnvelope;
}

function fmt(n: number): string {
    return n.toLocaleString("en-US");
}

/**
 * Stakeholder-friendly token-spend panel.
 *
 * Surfaces what the multi-shot pipeline actually cost in tokens, broken
 * down per phase so business reviewers can see where spend went and how
 * effectively the prompt cache served repeat content.
 *
 * Tolerates `usage === undefined` (older runs re-hydrated from the runs
 * dir before this field existed).
 */
export function UsagePanel({ usage }: Props) {
    if (!usage) {
        return (
            <div className="card">
                <h2>Token usage</h2>
                <p style={{ color: "var(--text-3)", fontSize: 12, margin: 0 }}>
                    No usage data recorded for this run.
                </p>
            </div>
        );
    }

    const { totals, shots, cache_hit_ratio } = usage;
    const cachePct = (cache_hit_ratio * 100).toFixed(1);

    return (
        <div className="card">
            <h2>Token usage</h2>
            <div
                style={{
                    display: "grid",
                    gridTemplateColumns: "repeat(4, minmax(0, 1fr))",
                    gap: 12,
                    marginBottom: 12,
                }}
            >
                <Stat label="Total tokens" value={fmt(totals.total_tokens)} />
                <Stat label="Input" value={fmt(totals.input_tokens)} />
                <Stat label="Output" value={fmt(totals.output_tokens)} />
                <Stat label="Cache hit" value={`${cachePct}%`} />
            </div>

            <div style={{ overflowX: "auto" }}>
                <table className="usage-table">
                    <thead>
                        <tr>
                            <th>Phase</th>
                            <th>Model</th>
                            <th style={{ textAlign: "right" }}>Input</th>
                            <th style={{ textAlign: "right" }}>Cached</th>
                            <th style={{ textAlign: "right" }}>Output</th>
                            <th style={{ textAlign: "right" }}>Total</th>
                        </tr>
                    </thead>
                    <tbody>
                        {shots.length === 0 ? (
                            <tr>
                                <td
                                    colSpan={6}
                                    style={{ color: "var(--text-3)", fontSize: 12 }}
                                >
                                    No LLM shots ran.
                                </td>
                            </tr>
                        ) : (
                            shots.map((s) => (
                                <tr key={s.shot}>
                                    <td>{s.shot}</td>
                                    <td>{s.model || "—"}</td>
                                    <td style={{ textAlign: "right" }}>
                                        {fmt(s.input_tokens)}
                                    </td>
                                    <td style={{ textAlign: "right" }}>
                                        {fmt(s.cached_input_tokens)}
                                    </td>
                                    <td style={{ textAlign: "right" }}>
                                        {fmt(s.output_tokens)}
                                    </td>
                                    <td style={{ textAlign: "right" }}>
                                        {fmt(s.total_tokens)}
                                    </td>
                                </tr>
                            ))
                        )}
                    </tbody>
                </table>
            </div>
            <p
                style={{
                    color: "var(--text-3)",
                    fontSize: 11,
                    marginTop: 10,
                    marginBottom: 0,
                }}
            >
                Prompt-cache reduces input cost. Reasoning tokens (counted in
                Output) are billed but never returned to the user.
            </p>
        </div>
    );
}

function Stat({ label, value }: { label: string; value: string }) {
    return (
        <div
            style={{
                background: "var(--surface-2, rgba(255,255,255,0.04))",
                borderRadius: 8,
                padding: "10px 12px",
            }}
        >
            <div
                style={{
                    color: "var(--text-3)",
                    fontSize: 10,
                    textTransform: "uppercase",
                    letterSpacing: 0.5,
                }}
            >
                {label}
            </div>
            <div style={{ fontSize: 20, fontWeight: 600, marginTop: 2 }}>
                {value}
            </div>
        </div>
    );
}
