#!/usr/bin/env bash
# Publish the local DuckDB snapshot to R2, where deployed containers read it.
#
# The deployed app never downloads the GeyserTimes complete archive. Exactly one
# machine does: whichever laptop runs `uv run geyser-ai ingest`. This script
# ships the result of that single download to object storage.
#
#   uv run geyser-ai ingest        # builds data/geysertimes.duckdb (cached archive)
#   ./deploy/publish-snapshot.sh   # ~200 MB upload, a couple of minutes
#
# Running containers pick the new snapshot up on their next cold start.
set -euo pipefail

BUCKET="${GEYSER_AI_R2_BUCKET:-geyser-ai-snapshots}"
KEY="${GEYSER_AI_R2_KEY:-geysertimes.duckdb}"
DB="${GEYSER_AI_DB:-data/geysertimes.duckdb}"

if [[ ! -f "$DB" ]]; then
  echo "No snapshot at $DB. Run 'uv run geyser-ai ingest' first." >&2
  exit 1
fi

echo "Uploading $DB ($(du -h "$DB" | cut -f1)) to r2://$BUCKET/$KEY"
npx wrangler r2 object put "$BUCKET/$KEY" \
  --file "$DB" \
  --content-type application/octet-stream \
  --remote
