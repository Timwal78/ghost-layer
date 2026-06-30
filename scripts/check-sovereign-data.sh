#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# SOVEREIGN DATA ENFORCEMENT — ScriptMasterLabs
#
# Scans Python source files for any pattern that indicates synthetic, fake,
# demo, placeholder, or hardcoded trading data returned from an API handler.
#
# EXIT 1 if violations found — blocks commit and CI.
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

VIOLATIONS=0
SCAN_DIRS=("ghost" ".")
EXTENSIONS=("py")

FILES=()
for dir in "${SCAN_DIRS[@]}"; do
  [[ -d "$dir" ]] || continue
  while IFS= read -r f; do
    # Skip venv, node_modules, __pycache__
    [[ "$f" == *"venv"* || "$f" == *"node_modules"* || "$f" == *"__pycache__"* ]] && continue
    FILES+=("$f")
  done < <(find "$dir" -maxdepth 4 -name "*.py" 2>/dev/null)
done

if [[ ${#FILES[@]} -eq 0 ]]; then
  echo "No Python source files found — skipping sovereign data check."
  exit 0
fi

PATTERNS=(
  # Hardcoded trading signal strings returned in responses
  "\"signal\":[[:space:]]*\"(BUY|SELL|HOLD)\""
  "'signal':[[:space:]]*'(BUY|SELL|HOLD)'"
  # Hardcoded confidence scores
  "\"confidence\":[[:space:]]*0\.[0-9]{2,}"
  "'confidence':[[:space:]]*0\.[0-9]{2,}"
  # Hardcoded squeeze detection
  "\"squeeze\":[[:space:]]*(True|False|true|false)"
  # Mock/fake/demo/placeholder/simulation intent markers in comments or strings
  "#[[:space:]]*(mock|fake|demo|placeholder|simul|hardcoded|synthetic|dummy|stub)[[:space:]]"
  # Hardcoded fake agent names
  "QUANT_ALPHA|RISK_SENTINEL|MACRO_ORACLE|SENTIMENT_AI|CHAIN_ANALYST|VOLUME_HAWK|BREAKOUT_BOT"
  # String literals that declare fake data
  "\"(mock|fake|placeholder|simulation|synthetic|dummy)\"|'(mock|fake|placeholder|simulation|synthetic|dummy)'"
  # Hardcoded entry/stopLoss in dict literals
  "\"entry\":[[:space:]]*[0-9]+\.[0-9]+.*\"stopLoss\":"
  "\"riskReward\":[[:space:]]*[0-9]+\.[0-9]"
)

echo "──────────────────────────────────────────────────────────────────────"
echo "  SOVEREIGN DATA ENFORCEMENT SCAN"
echo "  Repo: $(basename "$PWD")  |  Files: ${#FILES[@]}"
echo "──────────────────────────────────────────────────────────────────────"

for file in "${FILES[@]}"; do
  for pattern in "${PATTERNS[@]}"; do
    matches=$(grep -nE "$pattern" "$file" 2>/dev/null || true)
    if [[ -n "$matches" ]]; then
      echo -e "${RED}VIOLATION${NC} in ${YELLOW}${file}${NC}:"
      while IFS= read -r line; do
        echo "  $line"
      done <<< "$matches"
      echo ""
      VIOLATIONS=$((VIOLATIONS + 1))
    fi
  done
done

echo "──────────────────────────────────────────────────────────────────────"
if [[ $VIOLATIONS -gt 0 ]]; then
  echo -e "${RED}BLOCKED: ${VIOLATIONS} sovereign data violation(s) detected.${NC}"
  echo ""
  echo "  All handlers MUST return live data from upstream sources."
  echo "  No hardcoded signals, confidence scores, or fabricated trading data"
  echo "  may be returned from any endpoint — ever."
  echo ""
  echo "  Fix violations before committing."
  exit 1
else
  echo "  PASS — no sovereign data violations found."
  exit 0
fi
