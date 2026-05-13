import type {
    ExampleCase,
    HealthResponse,
    IntakeResponse,
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
