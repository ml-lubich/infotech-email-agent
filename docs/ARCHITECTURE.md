# Architecture

Single-purpose CLI that runs an OpenAI Agents SDK agent over one email + one
PDF and emits a Customer Service notification.

## Table of contents

- [Layers](#layers)
- [Module map](#module-map)
- [Architectural invariants](#architectural-invariants)

## Layers

```
main.py  ──►  invoice_agent.cli.main()
                │
                ├── load .env, validate OPENAI_API_KEY
                ├── resolve out_dir = ./out/<case-folder-name>/
                └── invoice_agent.agent.run_intake(email, pdf, out_dir)
                        │
                        ├── parse email JSON, resolve sibling PDF
                        ├── export INVOICE_OUT_DIR for tools
                        └── Runner.run_sync(Agent, user_prompt)
                                │
                                ├─ tool: extract_invoice_from_pdf
                                │     - PyMuPDF text per page
                                │     - PyMuPDF + Pillow extract embedded images
                                │     - one combined vision call (text + all images)
                                │       returning a parsed InvoicePayload
                                │
                                └─ tool: send_customer_service_notification
                                      - writes outbound_email.txt
                                      - writes outbound_email.json
```

## Module map

| Module | Responsibility |
|---|---|
| `invoice_agent/cli.py` | Argparse, .env, logging, exit codes. |
| `invoice_agent/agent.py` | Agent assembly, run orchestration. |
| `invoice_agent/tools.py` | The two `@function_tool`s. |
| `invoice_agent/pdf_extract.py` | Deterministic PDF text + image extraction. |
| `invoice_agent/schema.py` | Pydantic `InvoicePayload` + nested models. |
| `invoice_agent/models.py` | Allow-list (`gpt-5-mini` / `gpt-5-nano`). |

## Architectural invariants

- **Model allow-list.** Only `gpt-5-mini` and `gpt-5-nano`. Enforced in
  `models.resolve_model`. Any other id aborts startup.
- **Tools called once.** Agent instructions forbid re-invocation. Token
  budget discipline.
- **No silent fallbacks.** Missing PDF, unreadable image, schema validation
  warnings all surface (raise or `source_warnings`).
- **Outputs are case-scoped.** `./out/<case-folder>/` keeps runs separate
  without polluting the example directories.
- **Secrets via env only.** `.env` is git-ignored; `.env.example` is the
  documented contract.
- **`INVOICE_OUT_DIR` is a per-run side-channel.** `agent.run_intake` sets
  it before invoking `Runner.run_sync`; `tools.send_customer_service_notification`
  reads it. The constant name lives in `tools.OUT_DIR_ENV` (single source
  of truth). Do not set it manually.
