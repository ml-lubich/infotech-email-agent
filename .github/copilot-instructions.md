# Copilot / AI-agent instructions for this repo

**ALWAYS START IN `docs/` BEFORE WRITING CODE.** This repository treats
`docs/` as the authoritative specification. Any non-trivial change MUST
be planned against the canonical docs and reconciled with them in the
same transaction as the code change.

## Mandatory docs-first workflow

1. **Read first.** Before editing any file in `src/`, `tests/`, or
   `scripts/`, open and read:
   - [docs/ARCHITECTURE.md](../docs/ARCHITECTURE.md) — module map,
     layers, sequence diagram, invariants.
   - [docs/API.md](../docs/API.md) — public surface (CLI flags, env
     vars, exit codes, payload schema).
   - [docs/TESTING.md](../docs/TESTING.md) — Definition of Done,
     verification commands, current test map.
   - [docs/RUNBOOK.md](../docs/RUNBOOK.md) — how to run, common
     failures.
   - [docs/CHANGELOG.md](../docs/CHANGELOG.md) — most recent decisions
     under `[Unreleased]`.
2. **Plan.** For any multi-step or non-trivial change, write a short
   plan to `tmp/plan*.md` BEFORE editing code. The plan must reference
   the relevant section of `docs/ARCHITECTURE.md` it is consistent
   with, and contain these three sections:
   - `## Will NOT change` — invariants, public surfaces, allow-listed
     model strings.
   - `## Drift risks` — what could silently regress.
   - `## Verification plan` — exact `uv run …` commands to prove it.
3. **Implement.** Make the smallest additive change that satisfies the
   plan. Prefer adding modules / fields over reshaping existing ones
   (append-only semantics).
4. **Reconcile docs.** In the SAME change, update whichever canonical
   doc(s) are affected, and add an entry under `[Unreleased]` in
   `docs/CHANGELOG.md`. A change that touches behavior without
   updating `docs/` is **not done**.
5. **Verify.** Run `uv run pytest -q` from the repo root. Do not claim
   completion until exit code is 0 and coverage ≥ 80%.

## Hard invariants (do not break)

- **Model allow-list.** Only `gpt-5-mini` and `gpt-5-nano`. Enforced
  by `invoice_agent.models.resolve_model`. Adding any other model
  string is a breaking change.
- **No silent fallbacks.** Every fallback path MUST log a WARNING (or
  raise). Empty `except Exception: pass` is banned.
- **Each `@function_tool` is called once per run.** Verifier and
  post-checks live INSIDE the existing tools.
- **Outputs are case-scoped.** Artefacts go to `./out/<case>/`. Tools
  read `INVOICE_OUT_DIR` (the single source of truth lives in
  `tools.OUT_DIR_ENV`).
- **Secrets via env only.** `.env` is git-ignored. Never echo a real
  key, token, or session cookie.
- **Strict typing.** No `Any`. Pydantic models or dataclasses across
  module boundaries — not loose `dict`.
- **`docs/` is append-only and canonical.** Max 10 `.md` files under
  `docs/`. Do NOT create feature-specific docs; fold notes into the
  five canonical files (`ARCHITECTURE`, `API`, `TESTING`, `RUNBOOK`,
  `CHANGELOG`).

## Package manager

- **uv only.** Use `uv add <pkg>`, `uv add --dev <pkg>`,
  `uv run <cmd>`. Do not invoke `pip`, `poetry`, or `conda` directly.
- Tool installs go through `uv tool install` / `uvx`.

## Testing rules

- Tests in `tests/` import from `src/invoice_agent/`. They MUST run
  with **no real OpenAI calls** by default — inject a stub client.
- File-writing tests use real `tmp_path` (no filesystem mocks).
- A feature is not done until: (a) feature test exists and passes;
  (b) the smoke / full suite passes with no regressions;
  (c) `docs/TESTING.md` describes the new test.

## When in doubt

- Re-read [docs/ARCHITECTURE.md](../docs/ARCHITECTURE.md) and follow
  the closest analogous pattern.
- Ask ONE specific clarifying question instead of guessing.
- Never assume — if the answer is not in `docs/`, the change either
  belongs in `docs/` first, or is out of scope.
