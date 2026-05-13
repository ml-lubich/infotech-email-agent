interface Props {
    flags: string[];
    warnings: string[];
}

const HIGH_RISK = new Set([
    "bank_account_change_requested",
    "prompt_injection_attempt_in_document",
    "vendor_domain_mismatch",
    "duplicate_invoice_number_suspected",
]);

function flagClass(flag: string): string {
    if (HIGH_RISK.has(flag)) return "chip bad";
    return "chip warn";
}

export function RiskFlags({ flags, warnings }: Props) {
    if (flags.length === 0 && warnings.length === 0) {
        return (
            <div className="card">
                <h2>Risk flags</h2>
                <span className="chip good">no risk flags raised</span>
            </div>
        );
    }
    return (
        <div className="card">
            <h2>Risk flags</h2>
            {flags.length > 0 && (
                <>
                    <div className="flags-row">
                        {flags.map((f) => (
                            <span key={f} className={flagClass(f)}>
                                {f}
                            </span>
                        ))}
                    </div>
                </>
            )}
            {warnings.length > 0 && (
                <>
                    <div
                        className="section-title"
                        style={{ margin: "10px 0 6px", fontSize: 11 }}
                    >
                        Source warnings
                    </div>
                    <div className="flags-row">
                        {warnings.map((w, i) => (
                            <span key={`${w}-${i}`} className="chip">
                                {w}
                            </span>
                        ))}
                    </div>
                </>
            )}
        </div>
    );
}
