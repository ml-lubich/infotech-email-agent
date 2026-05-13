// Hermetic mock fixtures for the dashboard e2e suite.
//
// The frontend talks to FastAPI through a Vite proxy at `/api/*`. In e2e we
// install `page.route('**/api/**', …)` handlers that return these payloads,
// so the suite NEVER reaches the real backend, and never burns OpenAI credit.

import type { Page, Route } from "@playwright/test";
import type {
    ExampleCase,
    HealthResponse,
    IntakeResponse,
} from "../../../src/types";

export const HEALTHY: HealthResponse = {
    status: "ok",
    llm_enabled: true,
    has_openai_key: true,
    runs_dir: "/tmp/e2e/out",
};

export const DEGRADED: HealthResponse = {
    status: "ok",
    llm_enabled: false,
    has_openai_key: false,
    runs_dir: "/tmp/e2e/out",
};

export const EXAMPLES: ExampleCase[] = [
    { name: "case_1", has_pdf: true, subject: "Invoice INV-001 attached" },
    { name: "case_12_fraud_bank_change", has_pdf: true, subject: "Updated bank details" },
    { name: "case_3_no_attachment", has_pdf: false, subject: "Please pay" },
];

export function buildIntakeResponse(
    overrides: Partial<IntakeResponse> = {},
): IntakeResponse {
    const base: IntakeResponse = {
        case_id: "case_1",
        agent_reply: "Pipeline complete — 1 risk flag raised.",
        outbound_text:
            "Vendor: ACME Robotics LLC\nInvoice #: INV-2026-001\nTotal due: USD 1,234.56\n",
        outbound_json: {
            vendor_name: "ACME Robotics LLC",
            invoice_number: "INV-2026-001",
            invoice_date: "2026-05-01",
            due_date: "2026-05-31",
            payment_terms: "Net 30",
            currency: "USD",
            customer_po_number: "PO-9988",
            subtotal: 1100,
            taxes: [{ label: "Sales tax", amount: 134.56, rate: "8.875%" }],
            total_due: 1234.56,
            line_items: [
                {
                    sku: "RBT-100",
                    description: "Robotics widget",
                    quantity: 2,
                    unit_price: 550,
                    line_total: 1100,
                },
            ],
            notes: null,
            source_warnings: ["pdf_image_text_overrides_body"],
            risk_flags: ["bank_account_change_requested"],
            email_context: { po_number: "PO-9988" },
            pipeline: {
                confidence: 0.78,
                flag_count: 1,
                shots: [
                    {
                        name: "extract",
                        kind: "llm",
                        model: "gpt-5-mini",
                        decision: "PASS",
                        confidence_before: 0.5,
                        delta: 0.2,
                        confidence_after: 0.7,
                        findings: [],
                    },
                    {
                        name: "verify_extraction",
                        kind: "llm",
                        model: "gpt-5-mini",
                        decision: "FLAG",
                        confidence_before: 0.7,
                        delta: -0.05,
                        confidence_after: 0.65,
                        findings: ["bank_account_change_requested"],
                    },
                    {
                        name: "injection_screen",
                        kind: "llm",
                        model: "gpt-5-nano",
                        decision: "PASS",
                        confidence_before: 0.65,
                        delta: 0.13,
                        confidence_after: 0.78,
                        findings: [],
                    },
                ],
            },
        },
        artifacts: {
            "outbound_email.txt": "/tmp/e2e/out/case_1/outbound_email.txt",
            "outbound_email.json": "/tmp/e2e/out/case_1/outbound_email.json",
        },
        log_tail:
            "extract_invoice_from_pdf -> ok\nsend_customer_service_notification -> ok\n",
    };
    return { ...base, ...overrides };
}

interface InstallOptions {
    health?: HealthResponse;
    examples?: ExampleCase[];
    intake?: IntakeResponse;
    intakeStatus?: number;
    intakeError?: { error: string };
}

/**
 * Install a hermetic /api router. Call BEFORE `page.goto()`.
 * Any unmatched /api/* request fails the test loudly so we never
 * accidentally pass through to a real backend.
 */
export async function installApiMocks(
    page: Page,
    opts: InstallOptions = {},
): Promise<void> {
    const health = opts.health ?? HEALTHY;
    const examples = opts.examples ?? EXAMPLES;
    const intake = opts.intake ?? buildIntakeResponse();
    const intakeStatus = opts.intakeStatus ?? 200;

    const intakeBody = opts.intakeError
        ? JSON.stringify(opts.intakeError)
        : JSON.stringify(intake);

    // Playwright runs route handlers in REVERSE registration order
    // (last registered wins). Register the catch-all FIRST so the
    // specific handlers below take precedence.
    await page.route("**/api/**", async (route: Route) => {
        await route.fulfill({
            status: 599,
            contentType: "application/json",
            body: JSON.stringify({
                error: `unmocked api route: ${route.request().url()}`,
            }),
        });
    });

    await page.route("**/api/health", async (route: Route) => {
        await route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify(health),
        });
    });

    await page.route("**/api/examples", async (route: Route) => {
        await route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify({ cases: examples }),
        });
    });

    await page.route("**/api/intake", async (route: Route) => {
        await route.fulfill({
            status: intakeStatus,
            contentType: "application/json",
            body: intakeBody,
        });
    });

    await page.route("**/api/intake/example", async (route: Route) => {
        await route.fulfill({
            status: intakeStatus,
            contentType: "application/json",
            body: intakeBody,
        });
    });
}
