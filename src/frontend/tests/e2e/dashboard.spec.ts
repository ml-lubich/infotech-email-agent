import { expect, test } from "@playwright/test";
import {
    DEGRADED,
    EXAMPLES,
    buildIntakeResponse,
    installApiMocks,
} from "./fixtures/mocks";

// Pin LA timezone so any date-only formatting in the UI stays deterministic
// regardless of where the test runs (CI vs laptop).
test.use({ timezoneId: "America/Los_Angeles" });

test.describe("Invoice Intake Dashboard", () => {
    test.beforeEach(async ({ page }) => {
        // Capture browser console errors — fail the test if the app logs any.
        page.on("pageerror", (err) => {
            throw err;
        });
    });

    test("renders header, healthy backend pill, and shipped examples", async ({
        page,
    }) => {
        await installApiMocks(page);
        await page.goto("/");

        await expect(
            page.getByRole("heading", {
                name: /Invoice Intake — Pipeline Dashboard/i,
            }),
        ).toBeVisible();

        // Health pill resolves to LLM active + key OK.
        await expect(page.getByText(/LLM active/)).toBeVisible();
        await expect(page.getByText(/key OK/)).toBeVisible();

        // Example list is populated from the mocked /api/examples response.
        // Match each example button by its full visible text ("<name> <subject>")
        // so /case_1/ does not collide with case_12_*.
        for (const ex of EXAMPLES) {
            const subject = ex.subject ?? "(no subject)";
            await expect(
                page.getByRole("button", {
                    name: `${ex.name} ${subject}`,
                }),
            ).toBeVisible();
        }

        // Empty results state shown until a run is dispatched.
        await expect(
            page.getByText(/Upload an email \+ PDF or run a shipped example/i),
        ).toBeVisible();
    });

    test("degraded backend (no key, LLM disabled) surfaces in the pill", async ({
        page,
    }) => {
        await installApiMocks(page, { health: DEGRADED });
        await page.goto("/");

        await expect(page.getByText(/deterministic only/)).toBeVisible();
        await expect(page.getByText(/no OPENAI_API_KEY/)).toBeVisible();
    });

    test("running a shipped example renders the full pipeline trace", async ({
        page,
    }) => {
        await installApiMocks(page);
        await page.goto("/");

        // Wait for the example button to wire up, then click.
        // Anchor the regex so /case_1/ does not also match case_12_*.
        const exampleBtn = page.getByRole("button", { name: /^case_1\s/ });
        await expect(exampleBtn).toBeEnabled();
        await exampleBtn.click();

        // Banner with case id + agent reply.
        await expect(page.locator(".case-id")).toHaveText("case_1");
        await expect(
            page.getByText(/Pipeline complete — 1 risk flag raised/),
        ).toBeVisible();

        // Risk flags card surfaces the high-risk chip. The same flag also
        // shows up as a verifier finding in the timeline below — assert ≥ 1.
        await expect(
            page.getByText("bank_account_change_requested").first(),
        ).toBeVisible();
        await expect(
            page.getByText("pdf_image_text_overrides_body"),
        ).toBeVisible();

        // Pipeline timeline shows all three shots. Each shot name appears
        // both in the gauge tick row AND in the timeline row, so assert ≥ 2.
        await expect(
            page.getByRole("heading", { name: /Pipeline shots/ }),
        ).toBeVisible();
        for (const shot of ["extract", "verify_extraction", "injection_screen"]) {
            await expect(
                page.locator(".timeline .name", { hasText: new RegExp(`^${shot}$`) }),
            ).toBeVisible();
        }

        // Invoice card pulls vendor / invoice # / formatted total.
        // Use exact:true so we match the <div class="value"> field, not the
        // OutboundPanel JSON / summary <pre> dump that also contains them.
        await expect(
            page.getByText("ACME Robotics LLC", { exact: true }),
        ).toBeVisible();
        await expect(
            page.getByText("INV-2026-001", { exact: true }),
        ).toBeVisible();
        await expect(
            page.getByText("$1,234.56", { exact: true }),
        ).toBeVisible();
    });

    test("upload flow: Run intake button is disabled until an Email.json is picked", async ({
        page,
    }) => {
        await installApiMocks(page);
        await page.goto("/");

        const runBtn = page.getByRole("button", { name: /Run intake/ });
        await expect(runBtn).toBeDisabled();

        // Drop a synthetic Email.json via the hidden <input type=file>.
        const fileInput = page.locator('input[type="file"]');
        await fileInput.setInputFiles({
            name: "Email.json",
            mimeType: "application/json",
            buffer: Buffer.from(
                JSON.stringify({
                    subject: "test",
                    from: "vendor@example.com",
                    body: "Invoice attached.",
                    attachments: [],
                }),
            ),
        });

        await expect(page.getByText("📧 Email.json")).toBeVisible();
        await expect(runBtn).toBeEnabled();

        await runBtn.click();
        await expect(
            page.getByText("ACME Robotics LLC", { exact: true }),
        ).toBeVisible();
    });

    test("backend error surfaces as a banner, no result rendered", async ({
        page,
    }) => {
        await installApiMocks(page, {
            intakeStatus: 500,
            intakeError: { error: "stub_pipeline_failure" },
        });
        await page.goto("/");

        await page.getByRole("button", { name: /^case_1\s/ }).click();

        await expect(page.getByText(/stub_pipeline_failure/)).toBeVisible();
        await expect(
            page.getByText("ACME Robotics LLC", { exact: true }),
        ).toHaveCount(0);
    });

    test("theme toggle flips data-theme on <html> and persists to localStorage", async ({
        page,
    }) => {
        await installApiMocks(page);
        await page.goto("/");

        const html = page.locator("html");
        const initial = await html.getAttribute("data-theme");
        expect(initial === "dark" || initial === "light").toBeTruthy();

        const toggle = page.getByRole("button", { name: /Switch to .* theme/ });
        await toggle.click();

        const flipped = initial === "dark" ? "light" : "dark";
        await expect(html).toHaveAttribute("data-theme", flipped);

        const stored = await page.evaluate(() =>
            window.localStorage.getItem("iia-theme"),
        );
        expect(stored).toBe(flipped);
    });

    test("currency formatting is JPY-aware (no fractional yen)", async ({
        page,
    }) => {
        const jpy = buildIntakeResponse({
            outbound_json: {
                ...buildIntakeResponse().outbound_json,
                currency: "JPY",
                subtotal: 12000,
                taxes: [{ label: "Consumption tax", amount: 1200, rate: "10%" }],
                total_due: 13200,
                line_items: [],
            },
        });
        await installApiMocks(page, { intake: jpy });
        await page.goto("/");

        await page.getByRole("button", { name: /^case_1\s/ }).click();

        // Intl 'JPY' renders as ¥13,200 with no decimals.
        await expect(page.getByText("¥13,200")).toBeVisible();
        await expect(page.getByText(/¥13,200\.00/)).toHaveCount(0);
    });
});
