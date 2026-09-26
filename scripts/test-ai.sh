#!/bin/bash
# Testar AI:n med nycklarna i .env – Claude (Anthropic) och OpenAI.
#
#   ./scripts/test-ai.sh          provanrop per leverantör: svar i JSON-format och läsverktyg
#                                 (samma väg som AI-analytikern). Inga kunddata skickas.
#   ./scripts/test-ai.sh --eval   dessutom AI-utvärderingen på de syntetiska demobolagen mot de
#                                 riktiga modellerna (samma kontroller som i CI; kostar några kronor).
#
# Kräver att programmet är byggt (./scripts/start-mac.sh). Fungerar på macOS och Linux.

set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] || { echo "Hittar ingen .env – kör ./scripts/start-mac.sh först."; exit 1; }

run() { docker compose run --rm --no-deps api redovisningai "$@"; }

run ai-check
if [ "${1:-}" = "--eval" ]; then
  for platform in anthropic openai; do
    key="$(echo "$platform" | tr '[:lower:]' '[:upper:]')_API_KEY"
    if grep -Eq "^${key}=[^[:space:]#]" .env; then
      printf "\nAI-utvärdering mot %s:\n" "$platform"
      run eval --provider "$platform"
    fi
  done
fi
