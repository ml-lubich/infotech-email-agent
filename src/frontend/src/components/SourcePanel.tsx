import { useEffect, useState } from "react";
import { runFileUrl } from "../api";
import type { IntakeResponse } from "../types";

interface Props {
    result: IntakeResponse;
}

type Tab = "email" | "pdf";

/**
 * Renders the inbound source files for the current case:
 *
 * * the original Email.json (pretty-printed), and
 * * the invoice PDF (in an iframe so the browser's native viewer
 *   handles paging, zoom, and text selection).
 *
 * The files are streamed from the backend by case_id + filename via
 * /api/runs/{case_id}/file/{filename}, never trusted from the client.
 */
export function SourcePanel({ result }: Props) {
    const emailName = result.email_filename ?? null;
    const pdfName = result.pdf_filename ?? null;

    const initial: Tab = emailName ? "email" : pdfName ? "pdf" : "email";
    const [tab, setTab] = useState<Tab>(initial);

    if (!emailName && !pdfName) {
        return null;
    }

    return (
        <div className="card">
            <div className="outbound-header">
                <h2>Source packet</h2>
                <div className="source-hint">
                    The exact email + PDF the agent worked from.
                </div>
            </div>
            <div className="tabs">
                <button
                    className={tab === "email" ? "active" : ""}
                    onClick={() => setTab("email")}
                    disabled={!emailName}
                    title={emailName ?? "no Email.json on disk"}
                >
                    Email.json
                </button>
                <button
                    className={tab === "pdf" ? "active" : ""}
                    onClick={() => setTab("pdf")}
                    disabled={!pdfName}
                    title={pdfName ?? "no PDF attached"}
                >
                    Invoice PDF
                </button>
            </div>
            {tab === "email" && emailName && (
                <EmailJsonView caseId={result.case_id} filename={emailName} />
            )}
            {tab === "pdf" && pdfName && (
                <PdfView caseId={result.case_id} filename={pdfName} />
            )}
        </div>
    );
}

function EmailJsonView({ caseId, filename }: { caseId: string; filename: string }) {
    const [text, setText] = useState<string>("(loading…)");
    const [err, setErr] = useState<string | null>(null);
    const url = runFileUrl(caseId, filename);

    useEffect(() => {
        let cancelled = false;
        setErr(null);
        setText("(loading…)");
        fetch(url)
            .then(async (r) => {
                if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
                const raw = await r.text();
                try {
                    return JSON.stringify(JSON.parse(raw), null, 2);
                } catch {
                    // Server validates JSON on intake, but if a hand-edited
                    // case dir contains malformed JSON, surface it raw
                    // rather than blanking the panel.
                    return raw;
                }
            })
            .then((pretty) => {
                if (!cancelled) setText(pretty);
            })
            .catch((e: Error) => {
                if (!cancelled) setErr(e.message);
            });
        return () => {
            cancelled = true;
        };
    }, [url]);

    if (err) {
        return <div className="banner error">⚠ could not load {filename}: {err}</div>;
    }
    return (
        <>
            <div className="source-link">
                <a href={url} target="_blank" rel="noreferrer">
                    open {filename} in new tab ↗
                </a>
            </div>
            <pre className="payload">{text}</pre>
        </>
    );
}

function PdfView({ caseId, filename }: { caseId: string; filename: string }) {
    const url = runFileUrl(caseId, filename);
    return (
        <>
            <div className="source-link">
                <a href={url} target="_blank" rel="noreferrer">
                    open {filename} in new tab ↗
                </a>
            </div>
            <iframe
                title={`PDF: ${filename}`}
                src={url}
                className="source-pdf-frame"
            />
        </>
    );
}
