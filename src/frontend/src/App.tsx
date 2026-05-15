import { useEffect, useState } from "react";
import { getHealth, getRun, listExamples, runExample, runUpload } from "./api";
import { ConfidenceGauge } from "./components/ConfidenceGauge";
import { HistoryPanel } from "./components/HistoryPanel";
import { InvoiceCard } from "./components/InvoiceCard";
import { OutboundPanel } from "./components/OutboundPanel";
import { PipelineTimeline } from "./components/PipelineTimeline";
import { RiskFlags } from "./components/RiskFlags";
import { SourcePanel } from "./components/SourcePanel";
import { ThemeToggle } from "./components/ThemeToggle";
import { UploadZone } from "./components/UploadZone";
import { UsagePanel } from "./components/UsagePanel";
import type { ExampleCase, HealthResponse, IntakeResponse } from "./types";

export default function App() {
    const [health, setHealth] = useState<HealthResponse | null>(null);
    const [examples, setExamples] = useState<ExampleCase[]>([]);
    const [email, setEmail] = useState<File | null>(null);
    const [pdf, setPdf] = useState<File | null>(null);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [result, setResult] = useState<IntakeResponse | null>(null);
    // Bumped after every successful run so the HistoryPanel re-fetches.
    const [historyKey, setHistoryKey] = useState(0);

    useEffect(() => {
        getHealth()
            .then(setHealth)
            .catch((e: Error) => setError(`backend unreachable: ${e.message}`));
        listExamples()
            .then(setExamples)
            .catch(() => {
                /* listing failure is non-fatal — leave list empty */
            });
    }, []);

    async function submitUpload() {
        if (!email) {
            setError("Email.json is required.");
            return;
        }
        setBusy(true);
        setError(null);
        try {
            const r = await runUpload(email, pdf, email.name.replace(/\.json$/i, ""));
            setResult(r);
            setHistoryKey((k) => k + 1);
        } catch (e) {
            setError((e as Error).message);
        } finally {
            setBusy(false);
        }
    }

    async function submitExample(name: string) {
        setBusy(true);
        setError(null);
        try {
            const r = await runExample(name);
            setResult(r);
            setHistoryKey((k) => k + 1);
        } catch (e) {
            setError((e as Error).message);
        } finally {
            setBusy(false);
        }
    }

    async function loadHistory(caseId: string) {
        setBusy(true);
        setError(null);
        try {
            const r = await getRun(caseId);
            setResult(r);
        } catch (e) {
            setError((e as Error).message);
        } finally {
            setBusy(false);
        }
    }

    const envelope = result?.outbound_json.pipeline;
    const usage = result?.outbound_json.usage;
    const flags = (result?.outbound_json.risk_flags ?? []) as string[];
    const warnings = (result?.outbound_json.source_warnings ?? []) as string[];

    return (
        <div className="app">
            <header className="app-header">
                <div>
                    <h1>Invoice Intake — Pipeline Dashboard</h1>
                    <div className="sub">
                        Drop an inbox <code>Email.json</code> + invoice PDF, watch the
                        multi-shot pipeline grade vendor risk in real time.
                    </div>
                </div>
                <div className="header-controls">
                    <div className="health-pill">
                        <span
                            className={
                                "dot " +
                                (health?.has_openai_key ? "good" : health ? "bad" : "")
                            }
                        />
                        {health
                            ? `${health.llm_enabled ? "LLM active" : "deterministic only"} · ${health.has_openai_key ? "key OK" : "no OPENAI_API_KEY"
                            }`
                            : "checking backend…"}
                    </div>
                    <ThemeToggle />
                </div>
            </header>

            <div className="layout">
                <aside>
                    <div className="card">
                        <h2>Upload</h2>
                        <UploadZone
                            email={email}
                            pdf={pdf}
                            onPick={(e, p) => {
                                setEmail(e);
                                setPdf(p);
                            }}
                        />
                        <button
                            className="btn primary"
                            disabled={busy || !email}
                            onClick={submitUpload}
                        >
                            {busy ? <span className="spinner" /> : null}
                            {busy ? "Running pipeline…" : "Run intake"}
                        </button>
                    </div>

                    <div className="card">
                        <h2>Or pick a shipped example</h2>
                        <div className="examples-list">
                            {examples.length === 0 && (
                                <p style={{ color: "var(--text-3)", fontSize: 12, margin: 0 }}>
                                    No examples available.
                                </p>
                            )}
                            {examples.map((ex) => (
                                <button
                                    key={ex.name}
                                    className={ex.has_pdf ? "example" : "example no-pdf"}
                                    disabled={busy}
                                    onClick={() => submitExample(ex.name)}
                                    title={ex.subject ?? ""}
                                >
                                    <span className="ex-name">{ex.name}</span>
                                    <span className="ex-sub">
                                        {ex.subject ?? "(no subject)"}
                                    </span>
                                </button>
                            ))}
                        </div>
                    </div>

                    <HistoryPanel
                        refreshKey={historyKey}
                        onPick={loadHistory}
                        busy={busy}
                    />
                </aside>

                <main>
                    {error && <div className="banner error">⚠ {error}</div>}

                    {!result && !error && (
                        <div className="results-empty">
                            Upload an email + PDF or run a shipped example to see the
                            pipeline trace, extracted invoice, and outbound packet.
                        </div>
                    )}

                    {result && (
                        <>
                            <div className="banner info">
                                Run <span className="case-id">{result.case_id}</span> ·{" "}
                                {result.agent_reply}
                            </div>
                            <ConfidenceGauge envelope={envelope} />
                            <RiskFlags flags={flags} warnings={warnings} />
                            <UsagePanel usage={usage} />
                            <PipelineTimeline shots={envelope?.shots ?? []} />
                            <InvoiceCard invoice={result.outbound_json} />
                            <SourcePanel result={result} />
                            <OutboundPanel result={result} />
                        </>
                    )}
                </main>
            </div>
        </div>
    );
}
