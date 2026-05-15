#!/usr/bin/env bash
# install.sh — one-shot installer for the InfoTech Invoice Intake Agent.
#
# Works on macOS and Linux (any distro). Idempotent: safe to re-run.
#
# What it does (in order):
#   1. Installs `uv` if missing (official installer, no sudo).
#   2. Runs `uv sync` to install Python deps + lock the venv.
#   3. Scaffolds `.env` from `.env.example` if missing.
#   4. Scaffolds the global TOML config at the OS-correct XDG/AppSupport
#      path if missing (no overwrite — you can hand-edit it after).
#   5. Builds the React dashboard bundle if Bun is installed.
#   6. Prints next steps.
#
# Re-run after pulling new commits to pick up new deps.
#
# Usage:
#   ./scripts/install.sh
#   ./scripts/install.sh --no-frontend     # skip the bun build step
#   ./scripts/install.sh --force-config    # overwrite existing global config

set -euo pipefail

# Resolve repo root from this script's location so `./scripts/install.sh`
# and `bash scripts/install.sh` both work the same way.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

NO_FRONTEND=0
FORCE_CONFIG=0
for arg in "$@"; do
  case "$arg" in
    --no-frontend) NO_FRONTEND=1 ;;
    --force-config) FORCE_CONFIG=1 ;;
    -h|--help)
      sed -n '2,25p' "$0"; exit 0 ;;
    *) echo "unknown flag: $arg" >&2; exit 2 ;;
  esac
done

# ---------- pretty output ------------------------------------------------ #
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
  C_GRN=$'\033[32m'; C_YLW=$'\033[33m'; C_RED=$'\033[31m'
  C_DIM=$'\033[2m'; C_BOLD=$'\033[1m'; C_RST=$'\033[0m'
else
  C_GRN=""; C_YLW=""; C_RED=""; C_DIM=""; C_BOLD=""; C_RST=""
fi
say()  { printf "  %s%s%s %s\n" "$C_GRN" "✓" "$C_RST" "$*"; }
warn() { printf "  %s%s%s %s\n" "$C_YLW" "!" "$C_RST" "$*"; }
err()  { printf "  %s%s%s %s\n" "$C_RED" "✗" "$C_RST" "$*" >&2; }
step() { printf "\n%s%s%s\n" "$C_BOLD" "$*" "$C_RST"; }

# ---------- detect platform --------------------------------------------- #
OS="$(uname -s)"
case "$OS" in
  Darwin) PLATFORM="macos" ;;
  Linux)  PLATFORM="linux" ;;
  *)      err "unsupported OS: $OS (this installer supports macOS and Linux)"
          exit 1 ;;
esac

# Compute the XDG / Apple-Support config path the same way platformdirs does.
if [ "$PLATFORM" = "macos" ]; then
  CFG_DIR="${HOME}/Library/Application Support/infotech-email-agent"
else
  CFG_DIR="${XDG_CONFIG_HOME:-${HOME}/.config}/infotech-email-agent"
fi
CFG_FILE="${CFG_DIR}/config.toml"

step "InfoTech Email Agent · installer (${PLATFORM})"

# ---------- 1. uv -------------------------------------------------------- #
step "1/5  Python toolchain (uv)"
if ! command -v uv >/dev/null 2>&1; then
  warn "uv not found — installing (official script, no sudo)"
  # https://docs.astral.sh/uv/getting-started/installation/
  curl -LsSf https://astral.sh/uv/install.sh | sh
  # The installer drops uv in ~/.local/bin (Linux) or ~/.cargo/bin / ~/.local/bin (macOS).
  # Add it to PATH for THIS shell so the rest of the script works.
  export PATH="${HOME}/.local/bin:${HOME}/.cargo/bin:${PATH}"
  if ! command -v uv >/dev/null 2>&1; then
    err "uv install finished but the binary is still not on PATH."
    err "Open a new terminal and re-run this script."
    exit 1
  fi
fi
say "uv present: $(uv --version)"

# ---------- 2. dependencies --------------------------------------------- #
step "2/5  Python dependencies (uv sync)"
uv sync
say "venv ready at ${REPO_ROOT}/.venv"

# macOS Finder/Spotlight occasionally sets UF_HIDDEN on .venv contents,
# which makes site.py skip our editable .pth file. Strip it defensively.
if [ "$PLATFORM" = "macos" ]; then
  chflags -R nohidden .venv 2>/dev/null || true
fi

# ---------- 3. .env scaffold -------------------------------------------- #
step "3/5  Secrets (.env)"
if [ ! -f .env ]; then
  if [ -f .env.example ]; then
    cp .env.example .env
    say "created .env from .env.example"
    warn "edit .env and set OPENAI_API_KEY=sk-... before running the agent"
  else
    cat > .env <<'EOF'
# Required: your OpenAI API key (https://platform.openai.com/api-keys)
OPENAI_API_KEY=sk-replace-me
EOF
    say "created a fresh .env stub"
    warn "edit .env and set OPENAI_API_KEY=sk-... before running the agent"
  fi
else
  say ".env already exists (left untouched)"
fi

# ---------- 4. global TOML config --------------------------------------- #
step "4/5  Global config (${CFG_FILE})"
if [ -f "${CFG_FILE}" ] && [ "${FORCE_CONFIG}" -eq 0 ]; then
  say "global config already exists (left untouched — pass --force-config to overwrite)"
else
  mkdir -p "${CFG_DIR}"
  cat > "${CFG_FILE}" <<'EOF'
# InfoTech Email Agent — global (per-user) configuration.
#
# Precedence (lowest to highest):
#   defaults  <  THIS FILE  <  ./infotech-email-agent.toml or
#                              ./pyproject.toml [tool.infotech-email-agent]
#                            <  environment variables (INFOTECH_* / INVOICE_*)
#                            <  command-line flags
#
# All keys below are optional. Uncomment to override a default.
# Models MUST be one of: "gpt-5-mini", "gpt-5-nano".

# agent_model    = "gpt-5-mini"
# extract_model  = "gpt-5-mini"
# critic_model   = "gpt-5-nano"

# web_host       = "127.0.0.1"
# web_port       = 8000
# web_runs_dir   = "/absolute/path/to/per-request/case/dirs"

# llm_disabled   = false   # set true to skip Pass 3 + Pass 4 LLM shots
EOF
  say "wrote default global config"
fi

# ---------- 5. frontend bundle ------------------------------------------ #
step "5/5  Frontend dashboard"
if [ "${NO_FRONTEND}" -eq 1 ]; then
  warn "skipping frontend build (--no-frontend)"
elif [ -f src/frontend/dist/index.html ]; then
  say "frontend bundle already built"
elif command -v bun >/dev/null 2>&1; then
  ( cd src/frontend && bun install && bun run build )
  say "frontend bundle built"
else
  warn "Bun not installed — dashboard bundle skipped."
  warn "Install Bun (https://bun.sh) and re-run, OR use the CLI batch mode:"
  warn "  uv run invoice-intake --email ./examples/case_1/Email.json"
fi

# ---------- next steps -------------------------------------------------- #
step "Done"
echo
echo "  ${C_BOLD}Next:${C_RST}"
echo "    1. ${C_DIM}edit${C_RST} .env  →  OPENAI_API_KEY=sk-..."
echo "    2. ${C_DIM}edit (optional)${C_RST} ${CFG_FILE}"
echo "    3. ${C_DIM}run dashboard${C_RST}    uv run infotech-email-agent"
echo "    4. ${C_DIM}or batch mode${C_RST}    uv run invoice-intake --email ./examples/case_1/Email.json"
echo "    5. ${C_DIM}or run tests${C_RST}     uv run pytest"
echo
