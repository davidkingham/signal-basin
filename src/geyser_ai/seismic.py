"""The Steamboat seismic watch: detection where the data earned it, silence
where it did not.

Every constant here was set by measurement against WY.YNM waveforms and
GeyserTimes ground truth -- four validation rounds, 212 swept days, 4,797
hours; see docs/findings/seismic.md. The short version:

* A Steamboat major is an abrupt rise of minute-RMS to a sustained
  6,000-16,000 counts from a quiet (<2,500) baseline, holding for 15+
  minutes. Minors collapse within ~4 minutes; the sustained-minimum test is
  what rejects them.
* Teleseisms reproduce the signature locally but shake the whole region:
  the M7.6 Aomori surface waves lifted YNR 145x while real eruptions leave
  it at 0.9-2.3x. Simultaneous YNR (or YFT) elevation >= 3x is a veto.
* The oversnow season defeats the station entirely -- snowcoach stops at
  the Norris warming hut beside the vault, groomers at night, plowing at
  300k-650k counts -- co-located sources no geometry can veto. The watch is
  SUSPENDED mid-December through late March and says so.
* Summer late mornings occasionally cross the floor, so summer keeps the
  night gate (17:00-08:00). The shoulder seasons validated clean around
  the clock (Oct 1 - Dec 5 alone: 66 days, zero surviving fires).

Detection latency is ~15-20 minutes by construction (the sustained-minimum
window plus feed lag) -- still hours ahead of a human report overnight.

The watch NEVER claims certainty: a detection is "a seismic signature
consistent with a major eruption", and every non-watching mode states its
reason. Silence is no-information, not no-eruption, and the card says so.
"""

from __future__ import annotations

import datetime as dt
import io
import json
import logging
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import numpy as np

from .config import DATA_DIR

LOG = logging.getLogger(__name__)

PARK_TZ = ZoneInfo("America/Denver")
DATASELECT = "https://service.earthscope.org/fdsnws/dataselect/1/query"

# Detector constants -- measured, not tuned. See module docstring.
QUIET_BASELINE_MAX = 2500.0
FIRE_FLOOR = 6000.0
FIRE_RATIO = 3.0
SUSTAIN_MIN = 15
BASELINE_MIN = 360  # trailing minutes for the quiet baseline
BASELINE_MIN_VALID = 120
REFRACTORY_MIN = 60
VETO_RATIO = 3.0
STATE_MAX_MIN = 600  # rolling RMS kept, minutes

SEISMIC_URL = os.environ.get("GEYSER_AI_SEISMIC_URL", "").strip()
SEISMIC_PATH = Path(os.environ.get("GEYSER_AI_SEISMIC_PATH", DATA_DIR / "seismic.json"))


# -- season and hours policy (round four) -----------------------------------


def watch_mode(now_utc: dt.datetime) -> tuple[str, str]:
    """(mode, reason). Modes: watching | paused_daytime | suspended_winter."""
    local = now_utc.astimezone(PARK_TZ)
    md = (local.month, local.day)
    if md >= (12, 15) or md <= (3, 21):
        return (
            "suspended_winter",
            "winter operations at Norris (snowcoaches and groomers beside the "
            "station) are indistinguishable from eruption tremor",
        )
    if (5, 1) <= md <= (9, 30) and 8 <= local.hour < 17:
        return ("paused_daytime", "summer daytime noise at the Norris Museum")
    return ("watching", "")


# -- waveform fetch ----------------------------------------------------------


def fetch_minute_rms(
    station: str, start: dt.datetime, end: dt.datetime, timeout: float = 45.0
) -> list[tuple[int, float]] | None:
    """[(epoch_minute, rms), ...] for WY.<station> HHZ, or None if dark.

    Raw counts, per-record median removed. No filtering: the validated
    signature lives in broadband minute-RMS.
    """
    from simplemseed.miniseed import readMiniseed2Records

    try:
        resp = httpx.get(
            DATASELECT,
            params={
                "net": "WY",
                "sta": station,
                "loc": "01",
                "cha": "HHZ",
                "start": start.strftime("%Y-%m-%dT%H:%M:%S"),
                "end": end.strftime("%Y-%m-%dT%H:%M:%S"),
            },
            timeout=timeout,
        )
        if resp.status_code != 200 or len(resp.content) < 1000:
            return None
        buckets: dict[int, list[float]] = {}
        for rec in readMiniseed2Records(io.BytesIO(resp.content)):
            data = np.asarray(rec.decompress(), dtype=float)
            data -= np.median(data)
            t0 = rec.starttime().timestamp()
            sr = rec.header.sampleRate or 100.0
            for i in range(0, len(data), int(sr * 60)):
                chunk = data[i : i + int(sr * 60)]
                minute = int((t0 + i / sr) // 60)
                buckets.setdefault(minute, []).extend(chunk.tolist())
        out = []
        for minute, samples in sorted(buckets.items()):
            if len(samples) >= 30 * 100 // 2:  # at least half a minute of data
                a = np.asarray(samples)
                out.append((minute, float(np.sqrt(np.mean(a**2)))))
        return out or None
    except Exception as exc:  # network failure = station dark, never an error
        LOG.warning("seismic fetch failed for %s: %s", station, exc)
        return None


def regional_veto(
    fire_minute: int, fetch: Callable[..., Any] = fetch_minute_rms
) -> tuple[bool, str]:
    """(vetoed, detail). A fire that also lifts a distant station is regional."""
    t = dt.datetime.fromtimestamp(fire_minute * 60, dt.UTC)
    for ref in ("YNR", "YFT"):
        base = fetch(ref, t - dt.timedelta(hours=3), t - dt.timedelta(minutes=10))
        win = fetch(ref, t, t + dt.timedelta(minutes=SUSTAIN_MIN))
        if not base or not win:
            continue
        b = float(np.median([v for _, v in base]))
        w = float(np.median([v for _, v in win]))
        shift = w / max(b, 1.0)
        if shift >= VETO_RATIO:
            return True, f"{ref} lifted {shift:.0f}x — regional event, not Steamboat"
        return False, f"{ref} flat at {shift:.1f}x — source is local to Norris"
    return False, "no reference station available — veto unchecked"


# -- durable state -----------------------------------------------------------


def _load_state() -> dict[str, Any]:
    try:
        if SEISMIC_URL:
            resp = httpx.get(SEISMIC_URL, timeout=20.0)
            if resp.status_code == 200:
                return resp.json()
        elif SEISMIC_PATH.exists():
            return json.loads(SEISMIC_PATH.read_text())
    except Exception as exc:
        LOG.warning("seismic state load failed: %s", exc)
    return {"version": 1, "rms": [], "detections": [], "last_fire_minute": 0}


def _save_state(state: dict[str, Any]) -> None:
    payload = json.dumps(state, separators=(",", ":"))
    try:
        if SEISMIC_URL:
            httpx.put(
                SEISMIC_URL,
                content=payload.encode(),
                headers={"Content-Type": "application/json"},
                timeout=30.0,
            ).raise_for_status()
        else:
            SEISMIC_PATH.parent.mkdir(parents=True, exist_ok=True)
            SEISMIC_PATH.write_text(payload)
    except Exception as exc:
        LOG.warning("seismic state save failed: %s", exc)


# -- the tick ----------------------------------------------------------------


def watch_tick(
    now: dt.datetime | None = None,
    fetch: Callable[..., Any] = fetch_minute_rms,
    veto: Callable[..., Any] = regional_veto,
    state: dict[str, Any] | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    """One watch cycle. Returns the status the Steamboat card renders.

    Data is fetched in every mode (baseline continuity is what makes the
    first evening minutes after a summer pause trustworthy); only the fire
    logic is gated. All failure modes degrade to an honest status.
    """
    now = now or dt.datetime.now(dt.UTC)
    mode, reason = watch_mode(now)
    st = state if state is not None else _load_state()

    rms: list[list[float]] = [list(x) for x in st.get("rms", [])]
    last_min = int(rms[-1][0]) if rms else 0
    now_min = int(now.timestamp() // 60)

    fetched = None
    if mode != "suspended_winter":
        start_min = max(last_min + 1, now_min - 60)
        if start_min <= now_min:
            fetched = fetch(
                "YNM",
                dt.datetime.fromtimestamp(start_min * 60, dt.UTC),
                now,
            )
            if fetched:
                known = {int(m) for m, _ in rms}
                rms.extend([m, v] for m, v in fetched if m not in known)
                rms.sort(key=lambda x: x[0])
        rms = [x for x in rms if x[0] >= now_min - STATE_MAX_MIN]

    # -- fire scan over minutes not yet evaluated ---------------------------
    detections = list(st.get("detections", []))
    last_fire = int(st.get("last_fire_minute", 0))
    evaluated_to = int(st.get("evaluated_to", 0))
    if mode == "watching" and rms:
        series = {int(m): v for m, v in rms}
        minutes = sorted(series)
        for m in minutes:
            if m <= evaluated_to or m < last_fire + REFRACTORY_MIN:
                continue
            window = [series.get(m + k) for k in range(SUSTAIN_MIN)]
            if any(v is None for v in window):
                continue  # not enough forward data yet; retry next tick
            trail = [series[k] for k in range(m - BASELINE_MIN, m) if k in series]
            if len(trail) < BASELINE_MIN_VALID:
                evaluated_to = m
                continue
            base = float(np.median(trail))
            if base >= QUIET_BASELINE_MAX:
                evaluated_to = m
                continue
            if min(window) >= max(FIRE_FLOOR, FIRE_RATIO * base):
                vetoed, detail = veto(m)
                detections.append(
                    {
                        "detected_utc": dt.datetime.fromtimestamp(m * 60, dt.UTC).isoformat(),
                        "peak_rms": round(max(window), 0),
                        "baseline_rms": round(base, 0),
                        "vetoed": vetoed,
                        "veto_detail": detail,
                    }
                )
                last_fire = m
            evaluated_to = m
    detections = detections[-50:]

    st.update(
        {
            "rms": rms,
            "detections": detections,
            "last_fire_minute": last_fire,
            "evaluated_to": evaluated_to,
            "updated_utc": now.isoformat(),
        }
    )
    if persist:
        _save_state(st)

    # -- status for the card ------------------------------------------------
    data_lag_min = now_min - (int(rms[-1][0]) if rms else 0)
    live = [d for d in detections if not d["vetoed"]]
    latest = live[-1] if live else None
    recent = None
    if latest:
        age_h = (now - dt.datetime.fromisoformat(latest["detected_utc"])).total_seconds() / 3600
        if age_h < 48:
            recent = {**latest, "hours_ago": round(age_h, 1)}

    if mode == "suspended_winter":
        status = "suspended"
    elif mode == "paused_daytime":
        status = "paused"
    elif not rms or data_lag_min > 30:
        status, reason = "no_data", "station dark or feed lagging — watch offline"
    else:
        status = "watching"

    return {
        "status": status,
        "reason": reason,
        "station": "WY.YNM (Norris Museum, ~1.5 km from Steamboat)",
        "checked_utc": now.isoformat(),
        "data_lag_min": data_lag_min if rms else None,
        "recent_detection": recent,
        "detection_latency_min": SUSTAIN_MIN,
    }
