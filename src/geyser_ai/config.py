"""Shared paths and constants."""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
DB_PATH = DATA_DIR / "geysertimes.duckdb"
REPORTS_DIR = PROJECT_ROOT / "reports"
FIGURES_DIR = REPORTS_DIR / "figures"

ARCHIVE_BASE = "https://geysertimes.org/archive/complete"
GEYSERS_API = "https://www.geysertimes.org/api/v5/geysers"

# GeyserTimes runs Anubis, which challenges browser-like (Mozilla/...) user agents.
# A plain identifying UA is both more honest and what actually gets served.
USER_AGENT = "geyser-ai/0.1 (open-source geyser prediction research; +https://github.com/)"

# Geysers we model. Names must match the archive's `geyser` column after normalization.
TARGET_GEYSERS: tuple[str, ...] = (
    "Old Faithful",
    "Grand",
    "Daisy",
    "Riverside",
    "Castle",
    "Great Fountain",
    "Beehive",
)
