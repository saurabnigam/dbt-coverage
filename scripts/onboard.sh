#!/usr/bin/env bash
# dbt-coverage-lib onboarding script
# Usage: bash scripts/onboard.sh [--project-path <path>] [--dialect <dialect>]
#
# What it does:
#   1. Checks Python ≥ 3.11
#   2. Creates a virtual environment (.venv) and installs dbt-coverage-lib
#   3. Generates a dbtcov.yml config in your dbt project (via `dbtcov init`)
#   4. Runs a full scan and writes JSON + SARIF output to ./dbtcov-out/
#   5. Shows the quality gate result
#   6. Runs `dbtcov models` to print a per-model score table
#
# Requirements: Python 3.11+, bash 4+
# Safe to re-run: idempotent (skips steps already done)

set -euo pipefail

# ─── Colours ────────────────────────────────────────────────────────────────
RED='\033[0;31m'; YELLOW='\033[0;33m'; GREEN='\033[0;32m'
BOLD='\033[1m'; RESET='\033[0m'
info()    { echo -e "${BOLD}[dbtcov]${RESET} $*"; }
success() { echo -e "${GREEN}${BOLD}[dbtcov]${RESET}${GREEN} $*${RESET}"; }
warn()    { echo -e "${YELLOW}${BOLD}[dbtcov]${RESET}${YELLOW} $*${RESET}"; }
fail()    { echo -e "${RED}${BOLD}[dbtcov]${RESET}${RED} $*${RESET}"; exit 1; }

# ─── Defaults ───────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PROJECT_PATH=""          # dbt project root (auto-detected if empty)
DIALECT=""               # SQL dialect override (auto from dbt_project.yml if empty)
VENV_DIR="$REPO_ROOT/.venv"
MIN_PYTHON_MINOR=11

# ─── Argument parsing ────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --project-path)  PROJECT_PATH="$2"; shift 2 ;;
    --dialect)       DIALECT="$2";      shift 2 ;;
    --venv)          VENV_DIR="$2";     shift 2 ;;
    --help|-h)
      sed -n '/^# dbt-coverage/,/^# Safe to re-run/p' "$0" | sed 's/^# *//'
      exit 0 ;;
    *) fail "Unknown argument: $1  (try --help)" ;;
  esac
done

echo ""
echo -e "${BOLD}╔══════════════════════════════════════════════════╗${RESET}"
echo -e "${BOLD}║        dbt-coverage-lib  ·  Onboarding          ║${RESET}"
echo -e "${BOLD}╚══════════════════════════════════════════════════╝${RESET}"
echo ""

# ─── Step 1: Python version check ───────────────────────────────────────────
info "Step 1/6 — Checking Python version"

PYTHON_BIN=""
for candidate in python3 python python3.12 python3.11; do
  if command -v "$candidate" &>/dev/null; then
    version=$("$candidate" -c 'import sys; print(sys.version_info.minor)' 2>/dev/null || echo "0")
    major=$("$candidate"   -c 'import sys; print(sys.version_info.major)' 2>/dev/null || echo "0")
    if [[ "$major" -eq 3 && "$version" -ge "$MIN_PYTHON_MINOR" ]]; then
      PYTHON_BIN="$candidate"
      full_ver=$("$candidate" -c 'import sys; print(".".join(map(str,sys.version_info[:3])))')
      success "Found Python $full_ver at $(command -v "$candidate")"
      break
    fi
  fi
done

[[ -z "$PYTHON_BIN" ]] && fail \
  "Python 3.$MIN_PYTHON_MINOR+ not found. Install from https://www.python.org/downloads/"

# ─── Step 2: Virtual environment + install ───────────────────────────────────
info "Step 2/6 — Setting up virtual environment at $VENV_DIR"

if [[ ! -d "$VENV_DIR" ]]; then
  "$PYTHON_BIN" -m venv "$VENV_DIR"
  success "Virtual environment created"
else
  success "Virtual environment already exists — skipping creation"
fi

# Activate
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

info "  Installing dbt-coverage-lib …"
pip install --quiet --upgrade pip
pip install --quiet -e "$REPO_ROOT"
success "dbtcov installed: $(dbtcov --version 2>/dev/null || echo 'see: dbtcov --help')"

# ─── Step 3: Locate dbt project ──────────────────────────────────────────────
info "Step 3/6 — Locating dbt project"

if [[ -z "$PROJECT_PATH" ]]; then
  # Search upward from cwd, then from REPO_ROOT
  SEARCH_ROOTS=("$PWD" "$REPO_ROOT")
  for root in "${SEARCH_ROOTS[@]}"; do
    found=$(find "$root" -maxdepth 4 -name "dbt_project.yml" -not -path "*/\.*" \
            -not -path "*/.venv/*" -not -path "*/node_modules/*" 2>/dev/null | head -1)
    if [[ -n "$found" ]]; then
      PROJECT_PATH="$(dirname "$found")"
      break
    fi
  done
fi

if [[ -z "$PROJECT_PATH" ]]; then
  warn "No dbt_project.yml found automatically."
  echo -n "  Enter path to your dbt project (or press Enter to use current dir): "
  read -r user_input
  PROJECT_PATH="${user_input:-$PWD}"
fi

PROJECT_PATH="$(cd "$PROJECT_PATH" && pwd)"
success "Using dbt project at: $PROJECT_PATH"

# Sniff dialect from dbt_project.yml if not set by user
if [[ -z "$DIALECT" && -f "$PROJECT_PATH/dbt_project.yml" ]]; then
  sniffed=$(grep -E '^\s*(adapter|profile):' "$PROJECT_PATH/dbt_project.yml" \
    | grep -oE 'snowflake|bigquery|redshift|databricks|duckdb|postgres|spark|trino' \
    | head -1 || true)
  if [[ -n "$sniffed" ]]; then
    DIALECT="$sniffed"
    success "Detected dialect from dbt_project.yml: $DIALECT"
  else
    DIALECT="postgres"
    warn "Could not detect dialect — defaulting to 'postgres'. Override with --dialect."
  fi
fi

# ─── Step 4: Generate dbtcov.yml ─────────────────────────────────────────────
info "Step 4/6 — Generating dbtcov.yml config"

CONFIG_FILE="$PROJECT_PATH/dbtcov.yml"
if [[ -f "$CONFIG_FILE" ]]; then
  warn "dbtcov.yml already exists at $CONFIG_FILE — skipping init."
  warn "To regenerate: rm $CONFIG_FILE && dbtcov init --path $PROJECT_PATH"
else
  INIT_ARGS=("--path" "$PROJECT_PATH")
  [[ -n "$DIALECT" ]] && INIT_ARGS+=("--dialect" "$DIALECT")
  dbtcov init "${INIT_ARGS[@]}"
  success "Config written to $CONFIG_FILE"
  echo ""
  echo -e "  ${BOLD}Tip:${RESET} edit $CONFIG_FILE to tune rules, thresholds, and coverage gates."
fi

# ─── Step 5: Run the scan ────────────────────────────────────────────────────
info "Step 5/6 — Running dbtcov scan"
echo ""

OUT_DIR="$PROJECT_PATH/dbtcov-out"
mkdir -p "$OUT_DIR"

SCAN_ARGS=(
  "--path"   "$PROJECT_PATH"
  "--format" "json" "sarif"
  "--out"    "$OUT_DIR"
)
[[ -n "$DIALECT" ]] && SCAN_ARGS+=("--dialect" "$DIALECT")

set +e
dbtcov scan "${SCAN_ARGS[@]}"
SCAN_EXIT=$?
set -e

echo ""
if [[ $SCAN_EXIT -eq 0 ]]; then
  success "Scan complete — gate PASSED (exit 0)"
elif [[ $SCAN_EXIT -eq 1 ]]; then
  warn "Scan complete — gate FAILED (exit 1)"
  warn "See findings below. Fix the issues or lower gate thresholds in dbtcov.yml."
elif [[ $SCAN_EXIT -eq 2 ]]; then
  fail "No models found. Check --path points to your dbt project root."
elif [[ $SCAN_EXIT -eq 3 ]]; then
  warn "Scan complete — >90% parse failures (exit 3)."
  warn "Check --dialect matches your warehouse (current: $DIALECT)."
fi

# ─── Step 6: Per-model assessment ────────────────────────────────────────────
info "Step 6/6 — Per-model quality score table"
echo ""

if [[ -f "$OUT_DIR/findings.json" ]]; then
  # Show worst 20 models (score ≤ 79 = at-risk)
  dbtcov models \
    --results "$OUT_DIR/findings.json" \
    --min-score 79 \
    --sort score || true
  echo ""
  echo -e "  ${BOLD}Full table:${RESET}"
  echo -e "    dbtcov models --results $OUT_DIR/findings.json"
  echo -e ""
  echo -e "  ${BOLD}Machine-readable JSON:${RESET}"
  echo -e "    dbtcov models --results $OUT_DIR/findings.json --format json"
else
  warn "findings.json not found — skipping per-model table."
  warn "Re-run: dbtcov scan --path $PROJECT_PATH --format json sarif --out $OUT_DIR"
fi

# ─── Summary ────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}═══════════════════════════════════════════════════${RESET}"
echo -e "${BOLD}  Output files${RESET}"
echo -e "${BOLD}═══════════════════════════════════════════════════${RESET}"
echo -e "  ${GREEN}$OUT_DIR/findings.json${RESET}   ← full scan result + model scores"
echo -e "  ${GREEN}$OUT_DIR/findings.sarif${RESET}  ← GitHub Code Scanning (SARIF 2.1.0)"
echo -e "  ${GREEN}$OUT_DIR/coverage.json${RESET}   ← coverage metrics only"
echo ""
echo -e "${BOLD}  Next steps${RESET}"
echo -e "  1. Review $CONFIG_FILE and adjust coverage thresholds"
echo -e "  2. Add dbtcov to CI:  dbtcov scan --format json sarif --out ./dbtcov-out"
echo -e "  3. Upload SARIF to GitHub:  gh upload sarif $OUT_DIR/findings.sarif"
echo -e "  4. Re-assess models:  dbtcov models --results $OUT_DIR/findings.json"
echo ""

exit $SCAN_EXIT
