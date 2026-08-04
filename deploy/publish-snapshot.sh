#!/usr/bin/env bash
# Publish the local DuckDB snapshot to R2, where deployed containers read it.
#
# The deployed app never downloads the GeyserTimes complete archive. Exactly one
# machine does: whichever laptop runs `uv run geyser-ai ingest`. This script
# ships the result of that single download to object storage.
#
#   uv run geyser-ai ingest        # builds data/geysertimes.duckdb (cached archive)
#   ./deploy/publish-snapshot.sh   # a couple of minutes
#
# Running containers pick the new snapshot up on their next cold start, so a
# change to the ingest SQL is not live until this has run.
#
# The upload is CHUNKED. A single ~200 MB PUT through `wrangler r2 object put`
# turns out to be fragile -- on some networks anything past about 50 MB fails
# immediately with `fetch failed`, while small objects go through fine. Chunks
# are uploaded as separate objects and stitched back together by the container
# (see deploy/entrypoint.py), which also means a failed publish can be resumed
# by simply re-running this script.
set -euo pipefail

BUCKET="${GEYSER_AI_R2_BUCKET:-geyser-ai-snapshots}"
PREFIX="${GEYSER_AI_R2_PREFIX:-snapshot}"
DB="${GEYSER_AI_DB:-data/geysertimes.duckdb}"
CHUNK_MB="${GEYSER_AI_CHUNK_MB:-8}"

if [[ ! -f "$DB" ]]; then
  echo "No snapshot at $DB. Run 'uv run geyser-ai ingest' first." >&2
  exit 1
fi

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

echo "Splitting $DB ($(du -h "$DB" | cut -f1)) into ${CHUNK_MB}MB parts ..."
split -b "${CHUNK_MB}m" "$DB" "$WORK/part."
PARTS=("$WORK"/part.*)
BYTES=$(wc -c < "$DB" | tr -d ' ')

echo "Uploading ${#PARTS[@]} parts to r2://$BUCKET/$PREFIX/ ..."
i=0
for part in "${PARTS[@]}"; do
  name=$(printf "%s/part-%04d" "$PREFIX" "$i")
  for attempt in 1 2 3 4 5; do
    if npx wrangler r2 object put "$BUCKET/$name" \
        --file "$part" --content-type application/octet-stream --remote >/dev/null 2>&1; then
      break
    fi
    if [[ $attempt -eq 5 ]]; then
      echo "  part $i failed after 5 attempts" >&2
      exit 1
    fi
    sleep $((attempt * 5))
  done
  i=$((i + 1))
  printf "\r  %d/%d parts" "$i" "${#PARTS[@]}"
done
echo

# The manifest is written LAST and is what the container keys off, so a publish
# that dies halfway never leaves a container assembling a half-written snapshot.
MANIFEST="$WORK/manifest.json"
printf '{"version":1,"parts":%d,"bytes":%s,"prefix":"%s"}\n' \
  "${#PARTS[@]}" "$BYTES" "$PREFIX" > "$MANIFEST"

npx wrangler r2 object put "$BUCKET/$PREFIX/manifest.json" \
  --file "$MANIFEST" --content-type application/json --remote >/dev/null

echo "Published ${#PARTS[@]} parts ($BYTES bytes) and manifest to r2://$BUCKET/$PREFIX/"
