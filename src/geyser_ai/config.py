"""Shared paths and constants."""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("GEYSER_AI_DATA_DIR", PROJECT_ROOT / "data"))
RAW_DIR = DATA_DIR / "raw"
# Overridable so tests can point at a synthetic database and the deployed
# container can point at the snapshot it pulls from object storage. Read at
# import time, which is why the test suite sets it before importing the package.
DB_PATH = Path(os.environ.get("GEYSER_AI_DB", DATA_DIR / "geysertimes.duckdb"))
REPORTS_DIR = PROJECT_ROOT / "reports"
FIGURES_DIR = REPORTS_DIR / "figures"

ARCHIVE_BASE = "https://geysertimes.org/archive/complete"
GEYSERS_API = "https://www.geysertimes.org/api/v5/geysers"

# GeyserTimes runs Anubis, which challenges browser-like (Mozilla/...) user agents.
# A plain identifying UA is both more honest and what actually gets served.
USER_AGENT = "signal-basin/0.1 (open-source geyser prediction research; signalbasin.org)"

# Geysers we model. Names must match the archive's `geyser` column after normalization.
TARGET_GEYSERS: tuple[str, ...] = (
    "Old Faithful",
    "Grand",
    "Daisy",
    "Riverside",
    "Castle",
    "Great Fountain",
    "Beehive",
    "Fountain",
    "Lion",
    "Artemisia",
    "Lone Star",
    "Till",
    "Little Squirt",
)

# Geysers whose anchor can outlive its phase information. A prediction from
# a phase-dead anchor is "sometime in the next cycle" dressed up as a time,
# so the dashboard shows a PLANNING card unless the anchor is fresher than
# the geyser's measured phase window (in median cycles). The windows are
# EMPIRICAL -- residuals against `anchor + k x cycle` -- not derived from a
# random-walk model, which understates decoherence:
#   Lone Star  2.5  (90% band +/-45 min at k=1, gone by k=3; jitter 12%/cycle)
#   Till       8.0  (+/-78 at k=1, +/-183 at k=8, ceiling ~k=16; jitter 7%/cycle)
PHASE_WINDOW_CYCLES: dict[str, float] = {
    "Lone Star": 2.5,
    "Till": 8.0,
}
PHASE_LIMITED_GEYSERS: frozenset[str] = frozenset(PHASE_WINDOW_CYCLES)
