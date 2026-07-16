#!/usr/bin/env bash
# Ingest the sector representatives with the same recipe as the nine:
# DFP 2021-2025 (five closed years) + ITR 2024-2026 (for the live TTM). CVM is a
# local index read after the yearly ZIP is cached, so the inter-call delay is set
# to 0 for this batch. Not committed as a workflow — a one-off operational run.
set -euo pipefail
cd /c/Users/gabri/project-smaug
REPS="-t ABEV3 -t LREN3 -t HAPV3 -t TOTS3 -t VIVT3"
export REQUEST_DELAY_SECONDS=0

for Y in 2021 2022 2023 2024 2025; do
  echo "### DFP $Y"
  uv run python -m smaug.entrypoints.cli ingest $REPS --document DFP --year "$Y" 2>&1 | tail -3
done
for Y in 2024 2025 2026; do
  echo "### ITR $Y"
  uv run python -m smaug.entrypoints.cli ingest $REPS --document ITR --year "$Y" 2>&1 | tail -3
done
echo "### DONE ingest"
