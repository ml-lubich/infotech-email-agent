import type {
    ExampleCase,
    HealthResponse,
    IntakeResponse,
    StoredRun,
} from "./types";

const BASE = "/api";

async function unwrap<T>(res: Response): Promise<T> {
    if (!res.ok) {
        let detail: string;
        try {
            const body = (await res.json()) as { error?: string; detail?: string };
            detail = body.error ?? body.detail ?? res.statusText;
        } catch {
            detail = res.statusText;
        }
        const err = new Error(detail);
        (err as Error & { status?: number }).status = res.status;
        throw err;
    }
    return (await res.json()) as T;
}

export async function getHealth(): Promise<HealthResponse> {
    return unwrap<HealthResponse>(await fetch(`${BASE}/health`));
}

export async function listExamples(): Promise<ExampleCase[]> {
    const data = await unwrap<{ cases: ExampleCase[] }>(
        await fetch(`${BASE}/examples`),
    );
    return data.cases;
}

export async function runUpload(
    email: File,
    pdf: File | null,
    label: string,
): Promise<IntakeResponse> {
    const fd = new FormData();
    fd.append("email", email);
    if (pdf) fd.append("pdf", pdf);
    fd.append("label", label);
    return unwrap<IntakeResponse>(
        await fetch(`${BASE}/intake`, { method: "POST", body: fd }),
    );
}

export async function runExample(name: string): Promise<IntakeResponse> {
    const fd = new FormData();
    fd.append("name", name);
    return unwrap<IntakeResponse>(
        await fetch(`${BASE}/intake/example`, { method: "POST", body: fd }),
    );
}

export async function listRuns(): Promise<StoredRun[]> {
    const data = await unwrap<{ runs: StoredRun[] }>(
        await fetch(`${BASE}/runs`),
    );
    return data.runs;
}

export async function getRun(caseId: string): Promise<IntakeResponse> {
    return unwrap<IntakeResponse>(
        await fetch(`${BASE}/runs/${encodeURIComponent(caseId)}`),
    );
}

export function downloadRunUrl(caseId: string): string {
    return `${BASE}/runs/${encodeURIComponent(caseId)}/download`;
}

/**
 * Build the URL for a single source file inside a case dir (the
 * original Email.json or invoice PDF). Used by SourcePanel to render
 * the inbound packet via <iframe> / fetch().
 */
export function runFileUrl(caseId: string, filename: string): string {
    return `${BASE}/runs/${encodeURIComponent(caseId)}/file/${encodeURIComponent(filename)}`;
}
