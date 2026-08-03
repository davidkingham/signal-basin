"""Container entrypoint: pull the DuckDB snapshot, then serve the app.

Two constraints shape this file.

*Startup budget.* Cloudflare gives a container ~20 s to start listening on its
port. Loading a ~200 MB snapshot can take longer than that, so the HTTP server
comes up immediately and the snapshot is fetched on a background thread. Until
the file lands, the API's existing "no database" guard returns 503 and the
Worker holds the request; nothing here needs to know about that.

*Politeness to GeyserTimes.* The container never downloads the complete archive.
It reads a snapshot that a developer built locally with `geyser-ai ingest` and
uploaded to R2; the only live traffic a deployed instance sends to GeyserTimes
is the same five-minute-TTL `entries_recent` sync the local app already does.
"""

from __future__ import annotations

import logging
import os
import pathlib
import sys
import threading
import time
import urllib.error
import urllib.request

LOG = logging.getLogger("geyser-ai.entrypoint")

DB_PATH = pathlib.Path(os.environ.get("GEYSER_AI_DB", "/data/geysertimes.duckdb"))
SNAPSHOT_URL = os.environ.get("GEYSER_AI_SNAPSHOT_URL", "").strip()
# Binds all interfaces because the container's port is only reachable through
# the Worker in front of it, never from the public internet directly.
HOST = os.environ.get("GEYSER_AI_HOST", "0.0.0.0")
PORT = int(os.environ.get("GEYSER_AI_PORT", "8080"))

# Enough attempts to ride out a cold R2 read or a blip, not so many that a
# genuinely missing object keeps a container spinning.
MAX_ATTEMPTS = 5
BACKOFF_SECONDS = 3
CHUNK = 1 << 20
MIN_PLAUSIBLE_BYTES = 1 << 20  # a real snapshot is ~200 MB; anything tiny is an error page


def _download(url: str, dest: pathlib.Path) -> int:
    """Stream `url` to `dest` via a temp file, so readers never see a partial DB."""
    tmp = dest.with_suffix(dest.suffix + ".part")
    tmp.unlink(missing_ok=True)
    total = 0
    req = urllib.request.Request(url, headers={"User-Agent": "geyser-ai-container"})
    with urllib.request.urlopen(req, timeout=120) as resp, tmp.open("wb") as fh:
        while chunk := resp.read(CHUNK):
            fh.write(chunk)
            total += len(chunk)
    if total < MIN_PLAUSIBLE_BYTES:
        tmp.unlink(missing_ok=True)
        raise OSError(f"snapshot at {url} was only {total} bytes")
    os.replace(tmp, dest)
    return total


def fetch_snapshot() -> None:
    """Populate DB_PATH from object storage. Runs on a background thread."""
    if DB_PATH.exists():
        LOG.info("snapshot already present at %s, skipping download", DB_PATH)
        return
    if not SNAPSHOT_URL:
        LOG.warning("GEYSER_AI_SNAPSHOT_URL is unset and %s is missing", DB_PATH)
        return

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(1, MAX_ATTEMPTS + 1):
        started = time.monotonic()
        try:
            size = _download(SNAPSHOT_URL, DB_PATH)
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            LOG.warning("snapshot fetch attempt %d/%d failed: %s", attempt, MAX_ATTEMPTS, exc)
            if attempt == MAX_ATTEMPTS:
                LOG.error("giving up on snapshot; the API will keep returning 503")
                return
            time.sleep(BACKOFF_SECONDS * attempt)
            continue

        LOG.info(
            "snapshot ready: %.1f MB in %.1f s -> %s",
            size / 1e6,
            time.monotonic() - started,
            DB_PATH,
        )
        return


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stdout,
    )

    # Start the fetch before the (slow) scientific-stack import below, so the
    # download overlaps with interpreter warm-up.
    threading.Thread(target=fetch_snapshot, name="snapshot-fetch", daemon=True).start()

    import uvicorn

    from geyser_ai.api import app

    LOG.info("serving on %s:%d (db=%s)", HOST, PORT, DB_PATH)
    uvicorn.run(app, host=HOST, port=PORT, log_level="info", access_log=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
