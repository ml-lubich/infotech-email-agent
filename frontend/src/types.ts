// Mirrors src/invoice_agent_web/main.py response models and the
// pipeline envelope written by src/invoice_agent/pipeline.py.

export interface Evidence {
    finding: string;
    source: "email" | "pdf_text" | "extracted_payload" | "verifier" | "summary" | string;
    quote: string;
    location?: string | null;
}

export interface PipelineShot {
    name: string;
    kind: "deterministic" | "llm" | string;
    model: string;
    decision: "PASS" | "FLAG" | "FAIL" | "SKIPPED" | string;
    confidence_before: number;
    delta: number;
    confidence_after: number;
    findings: string[];
    evidence?: Evidence[];
}

export interface PipelineEnvelope {
    confidence: number;
    flag_count: number;
    shots: PipelineShot[];
}

export interface TaxLine {
    label?: string | null;
    amount?: number | null;
    rate?: string | null;
}

export interface InvoiceLineItem {
    sku?: string | null;
    description?: string | null;
    quantity?: number | null;
    unit_price?: number | null;
    line_total?: number | null;
}

export interface OutboundInvoice {
    vendor_name?: string | null;
    invoice_number?: string | null;
    invoice_date?: string | null;
    due_date?: string | null;
    payment_terms?: string | null;
    currency?: string | null;
    customer_po_number?: string | null;
    subtotal?: number | null;
    taxes?: TaxLine[] | null;
    total_due?: number | null;
    line_items?: InvoiceLineItem[] | null;
    ship_to?: unknown;
    notes?: string | null;
    source_warnings?: string[] | null;
    risk_flags?: string[] | null;
    email_context?: Record<string, unknown> | null;
    pipeline?: PipelineEnvelope;
    [key: string]: unknown;
}

export interface IntakeResponse {
    case_id: string;
    agent_reply: string;
    outbound_text: string;
    outbound_json: OutboundInvoice;
    artifacts: Record<string, string>;
    log_tail: string;
}

export interface ExampleCase {
    name: string;
    has_pdf: boolean;
    subject: string | null;
}

export interface HealthResponse {
    status: string;
    llm_enabled: boolean;
    has_openai_key: boolean;
    runs_dir: string;
}

export interface ApiError {
    status: number;
    message: string;
}
