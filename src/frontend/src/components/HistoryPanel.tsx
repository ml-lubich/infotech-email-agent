import { useEffect, useState } from "react";
import { downloadRunUrl, listRuns } from "../api";
import type { StoredRun } from "../types";

interface Props {
    /** Bumped after every successful run so the list refreshes. */
    refreshKey: number;
    /** Click handler — load this run into the main result panel. */
    onPick: (caseId: string) => void;
    busy: boolean;
}

function fmtBytes(n: number): string {
    if (n < 1024) return `${n} B`;
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
    return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

function fmtTime(epochSeconds: number): string {
    const d = new Date(epochSeconds * 1000);
    return d.toLocaleString();
}

export function HistoryPanel({ refreshKey, onPick, busy }: Props) {
    const [runs, setRuns] = useState<StoredRun[]>([]);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        let cancelled = false;
        listRuns()
            .then((rs) => {
                if (!cancelled) setRuns(rs);
            })
            .catch((e: Error) => {
                if (!cancelled) setError(e.message);
            });
        return () => {
            cancelled = true;
        };
    }, [refreshKey]);

    if (error) {
        return (
            <div className="card">
                <h2>History</h2>
                <p className="muted-empty">
                    Could not load history: {error}
                </p>
            </div>
        );
    }

    return (
        <div className="card">
            <h2>History ({runs.length})</h2>
            {runs.length === 0 && (
                <p className="muted-empty">
                    No runs yet. Submit an upload or example to populate.
                </p>
            )}
            <div className="examples-list">
                {runs.map((r) => (
                    <div
                        key={r.case_id}
                        className="example history-row"
                    >
                        <button
                            type="button"
                            className="history-pick"
                            disabled={busy}
                            onClick={() => onPick(r.case_id)}
                            title={r.case_id}
                        >
                            <span className="ex-name">{r.label}</span>
                            <span className="ex-sub">
                                {fmtTime(r.created_at)} · {r.file_count} files ·{" "}
                                {fmtBytes(r.size_bytes)}
                                {r.has_outbound ? "" : " · (no outbound)"}
                            </span>
                        </button>
                        <a
                            className="btn"
                            href={downloadRunUrl(r.case_id)}
                            download={`${r.case_id}.zip`}
                            title="Download .zip of this run"
                            onClick={(e) => e.stopPropagation()}
                        >
                            ⇣
                        </a>
                    </div>
                ))}
            </div>
        </div>
    );
}
