import { useState } from "react";
import { downloadRunUrl } from "../api";
import type { IntakeResponse } from "../types";

interface Props {
    result: IntakeResponse;
}

type Tab = "summary" | "json" | "log";

export function OutboundPanel({ result }: Props) {
    const [tab, setTab] = useState<Tab>("summary");

    return (
        <div className="card">
            <div className="outbound-header">
                <h2>Outbound packet</h2>
                <a
                    className="btn"
                    href={downloadRunUrl(result.case_id)}
                    download={`${result.case_id}.zip`}
                    title="Download all artefacts (Email.json, Invoice.pdf, outbound_email.{txt,json}, run.log) as a single .zip"
                >
                    ⇣ Download .zip
                </a>
            </div>
            <div className="tabs">
                <button
                    className={tab === "summary" ? "active" : ""}
                    onClick={() => setTab("summary")}
                >
                    AP summary
                </button>
                <button
                    className={tab === "json" ? "active" : ""}
                    onClick={() => setTab("json")}
                >
                    Full JSON
                </button>
                <button
                    className={tab === "log" ? "active" : ""}
                    onClick={() => setTab("log")}
                >
                    Run log
                </button>
            </div>
            {tab === "summary" && (
                <pre className="payload">{result.outbound_text || "(empty)"}</pre>
            )}
            {tab === "json" && (
                <pre className="payload">
                    {JSON.stringify(result.outbound_json, null, 2)}
                </pre>
            )}
            {tab === "log" && (
                <pre className="payload">{result.log_tail || "(no log)"}</pre>
            )}
        </div>
    );
}
