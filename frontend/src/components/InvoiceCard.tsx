import type { OutboundInvoice } from "../types";

interface Props {
    invoice: OutboundInvoice;
}

function fmtMoney(value: number | null | undefined, currency: string | null | undefined): string {
    if (value == null) return "—";
    const code = currency ?? "USD";
    try {
        return new Intl.NumberFormat("en-US", {
            style: "currency",
            currency: code,
            maximumFractionDigits: code === "JPY" ? 0 : 2,
        }).format(value);
    } catch {
        return `${value.toFixed(2)} ${code}`;
    }
}

function Field({
    label,
    value,
    full = false,
}: {
    label: string;
    value: string | number | null | undefined;
    full?: boolean;
}) {
    const isEmpty = value === null || value === undefined || value === "";
    return (
        <div className={full ? "field full" : "field"}>
            <div className="label">{label}</div>
            <div className={isEmpty ? "value muted" : "value"}>
                {isEmpty ? "—" : String(value)}
            </div>
        </div>
    );
}

export function InvoiceCard({ invoice }: Props) {
    const taxes = invoice.taxes ?? [];
    const lineItems = invoice.line_items ?? [];
    const currency = invoice.currency ?? null;

    return (
        <div className="card">
            <h2>Extracted invoice</h2>

            <div className="invoice-grid">
                <Field label="Vendor" value={invoice.vendor_name} full />
                <Field label="Invoice #" value={invoice.invoice_number} />
                <Field label="PO #" value={invoice.customer_po_number} />
                <Field label="Invoice date" value={invoice.invoice_date} />
                <Field label="Due date" value={invoice.due_date} />
                <Field label="Currency" value={invoice.currency} />
                <Field label="Payment terms" value={invoice.payment_terms} />
            </div>

            <div className="totals">
                <div className="row">
                    <span>Subtotal</span>
                    <span>{fmtMoney(invoice.subtotal, currency)}</span>
                </div>
                {taxes.map((t, i) => (
                    <div className="row" key={`${t.label ?? "tax"}-${i}`}>
                        <span>
                            {t.label ?? "Tax"}
                            {t.rate ? ` (${t.rate})` : ""}
                        </span>
                        <span>{fmtMoney(t.amount ?? null, currency)}</span>
                    </div>
                ))}
                <div className="row total">
                    <span>Total due</span>
                    <span>{fmtMoney(invoice.total_due, currency)}</span>
                </div>
            </div>

            {lineItems.length > 0 && (
                <>
                    <div className="section-title">Line items ({lineItems.length})</div>
                    <table className="lines">
                        <thead>
                            <tr>
                                <th>SKU</th>
                                <th>Description</th>
                                <th className="num">Qty</th>
                                <th className="num">Unit</th>
                                <th className="num">Total</th>
                            </tr>
                        </thead>
                        <tbody>
                            {lineItems.map((li, i) => (
                                <tr key={`${li.sku ?? "row"}-${i}`}>
                                    <td>{li.sku ?? "—"}</td>
                                    <td>{li.description ?? "—"}</td>
                                    <td className="num">{li.quantity ?? "—"}</td>
                                    <td className="num">{fmtMoney(li.unit_price, currency)}</td>
                                    <td className="num">{fmtMoney(li.line_total, currency)}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </>
            )}
        </div>
    );
}
