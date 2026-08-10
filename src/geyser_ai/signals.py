"""Live precursor signals, each wearing its own measured hit rate.

Beehive's Indicator already owns the only validated countdown; everything
here is a lower tier -- signals gazers themselves watch, surfaced with the
historical numbers that say exactly how much they mean. The rates below were
measured over eight years of the archive (see model-results.md):

* Beehive's South Bubbler: 63% of logged spells precede Beehive within 3 h
  (median 38 min). A heads-up, not a countdown.
* Beehive's Close to Cone Indicator: 36% within 2 h. Weaker still.
* Turban / West Triplet at Grand: 79% / 60% are followed by Grand within
  2-4 h -- but that is LOGGING BIAS, not physics: people log Turban when
  they are sitting at Grand near due time. The honest signal is presence
  ("observers on station"), and that is all the card claims.
* Giant Hot Period: only 7% precede Giant within 6 h -- and gazers sprint
  for every one, because Giant erupts a handful of times a year. Park-wide
  news, flagged with its number.
* Fan & Mortar Event Cycle: 18% precede an eruption within 24 h. Same tier.

Riverside and Great Fountain overflow -- the two signals that would support
real nowcasts -- live in GeyserTimes NOTES, which the public API does not
expose. That is the standing ask for the GT collaboration conversation.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import duckdb

from .config import DB_PATH

# (signal geyser, target card, lookback minutes, note template)
CARD_SIGNALS = [
    (
        "Beehive's Indicator",
        "Beehive",
        30,
        "Indicator logged {ago} min ago — Beehive typically follows within ~13 min",
    ),
    (
        "Beehive's South Bubbler",
        "Beehive",
        60,
        "South Bubbler active {ago} min ago — 63% of spells precede Beehive within 3 h",
    ),
    (
        "Turban",
        "Grand",
        35,
        "Turban logged {ago} min ago — observers on station at Grand",
    ),
    (
        "West Triplet",
        "Grand",
        90,
        "West Triplet logged {ago} min ago — observers on station at Grand",
    ),
]

# Park-wide rare-event alerts: (signal geyser, lookback minutes, note template)
PARK_SIGNALS = [
    (
        "Giant Hot Period",
        360,
        "Giant hot period logged {ago_h} — historically 7% lead to Giant within 6 h",
    ),
    (
        "Fan and Mortar Event Cycle",
        1440,
        "F&M event cycle reported {ago_h} — historically 18% precede an eruption within 24 h",
    ),
]


def _latest(con, geyser: str, since_epoch: int) -> int | None:
    for table in ("recent_eruptions", "eruptions"):
        try:
            row = con.execute(
                f"SELECT max(epoch) FROM {table} WHERE geyser = ? AND epoch > ?",
                [geyser, since_epoch],
            ).fetchone()
        except duckdb.Error:
            continue
        if row and row[0]:
            return int(row[0])
    return None


def live_signals(db_path=DB_PATH, now: dt.datetime | None = None) -> dict[str, Any]:
    """{"cards": {target: [note, ...]}, "park": [note, ...]}.

    Read-only and cheap; failures return empty rather than ever touching the
    prediction path.
    """
    now = now or dt.datetime.now(dt.UTC)
    now_e = int(now.timestamp())
    cards: dict[str, list[str]] = {}
    park: list[str] = []
    try:
        con = duckdb.connect(str(db_path), read_only=True)
    except Exception:
        return {"cards": {}, "park": []}
    try:
        for sig, target, lookback, template in CARD_SIGNALS:
            e = _latest(con, sig, now_e - lookback * 60)
            if e is not None:
                ago = max(0, (now_e - e) // 60)
                cards.setdefault(target, []).append(template.format(ago=ago))
        for sig, lookback, template in PARK_SIGNALS:
            e = _latest(con, sig, now_e - lookback * 60)
            if e is not None:
                mins = max(0, (now_e - e) // 60)
                ago_h = f"{mins} min ago" if mins < 90 else f"{mins / 60:.1f} h ago"
                park.append(template.format(ago_h=ago_h))
    except Exception:
        return {"cards": {}, "park": []}
    finally:
        con.close()
    return {"cards": cards, "park": park}
