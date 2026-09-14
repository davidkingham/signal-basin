"""Match published predictions to the eruptions that actually happened, and score them.

Everything here is pure: dataclasses in, dataclasses out, no database, no clock
of its own, no network. That is deliberate -- the matching rules are where a
comparison between three predictors is won or lost, so they have to be testable
in isolation.

The rules, and why each one is there:

*Latest before the eruption wins.* Sources re-predict constantly. A prediction
issued at 14:00 and revised at 15:40 is not two attempts at the same eruption;
the 14:00 one was withdrawn. Only the last prediction issued strictly before the
eruption is scored, and the ones it replaced are discarded unscored rather than
counted as misses.

*Each source is scored in its own stated window.* NPS states a window of about
±12 minutes for Old Faithful and over two hours for Grand; Geysers.net states
its own; this project states a nominal 90% interval. Imposing one definition on
all three would flatter whoever happens to claim the widest window, so in-window
rate is always reported next to the median window width, and a source that
states no window is simply not scored on that metric.

*Eruptions beyond a generous horizon are not scored at all.* This is
crowd-sourced data with observation gaps: if nobody logs Riverside overnight,
the next logged eruption may be two cycles after the one a prediction was aimed
at. Charging that gap to the predictor would be measuring the observers, not the
forecast. Any pairing where the eruption lands more than `MATCH_HORIZON_FACTOR`
window widths (at least `MIN_MATCH_HORIZON_SECONDS`) past the predicted time is
dropped for every source identically, and counted so the censoring is visible.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

# How far past its predicted time an eruption may land and still be treated as
# the eruption that prediction was about.
MATCH_HORIZON_FACTOR = 3.0
# The floor is a fraction of the geyser's own cycle, not a fixed number of
# hours. A fixed 6-hour floor never let the width rule bind on any geyser whose
# band is under two hours, so a Daisy eruption one unlogged cycle late (108
# min against a 23-min band) was scored as a +108 miss -- exactly the case the
# horizon exists to drop. Measured on the first month of the live ledger it
# was most of the published gap to the NPS: Old Faithful MAE 25.2 with those
# rows, 7.5 without (NPS 5.3); Daisy 16.7 vs 6.2 (NPS 5.4); Great Fountain
# 326 vs 75. Half a cycle is the natural line: past it, the next cycle's
# eruption is the better explanation than a very late one.
HORIZON_CYCLE_FRACTION = 0.5
# Used only when the caller cannot say how long the geyser's cycle is.
MIN_MATCH_HORIZON_SECONDS = 6 * 3600
# Two entries this close are one eruption logged twice (the archive's ingest
# collapses 60 s; live entries by different observers land minutes apart).
# Scoring the second one produced a Daisy row at -97 min with a 5.6-minute
# lead. No served geyser has a real interval anywhere near this short.
DUPLICATE_WINDOW_SECONDS = 15 * 60

# A prediction nothing ever matched is abandoned after this long.
STALE_OPEN_SECONDS = 2 * 24 * 3600


@dataclass(frozen=True)
class LoggedPrediction:
    """A prediction as issued, before anyone knows whether it was any good."""

    source: str
    geyser: str
    key: str
    issued_epoch: int
    predicted_epoch: int
    window_open_epoch: int | None = None
    window_close_epoch: int | None = None
    # This project also states a 50% interval. Nobody else does, so it is
    # optional and reported separately rather than compared across sources.
    inner_open_epoch: int | None = None
    inner_close_epoch: int | None = None
    detail: str = ""
    # A bimodal forecast (Lion) also states the probability that the eruption
    # falls BEFORE the valley between its modes. The point estimate of such a
    # forecast is a poor summary; this is the claim worth scoring.
    mode_boundary_epoch: int | None = None
    mode_short_prob: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> LoggedPrediction:
        allowed = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in allowed})


@dataclass(frozen=True)
class Eruption:
    """An eruption as logged, which is the only ground truth available."""

    geyser: str
    eruption_id: int
    epoch: int


@dataclass(frozen=True)
class ScoredPrediction:
    """One prediction judged against one eruption."""

    source: str
    geyser: str
    eruption_id: int
    actual_epoch: int
    issued_epoch: int
    predicted_epoch: int
    signed_error_min: float
    abs_error_min: float
    lead_minutes: float
    in_window: bool | None
    window_width_min: float | None
    window_open_epoch: int | None
    window_close_epoch: int | None
    in_inner_window: bool | None
    detail: str
    # Kept so the 50% rate can be shown beside the width it was earned on, the
    # same rule every other rate on the scoreboard obeys. Defaulted because
    # ledgers written before these existed must still load.
    inner_open_epoch: int | None = None
    inner_close_epoch: int | None = None
    # Brier score of the stated short-mode probability, 0 (perfect) to 1;
    # 0.25 is what always saying 50% earns. None where no mode was stated.
    mode_brier: float | None = None
    mode_short_prob: float | None = None
    mode_hit_short: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ScoredPrediction:
        allowed = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in allowed})


@dataclass
class MatchResult:
    scored: list[ScoredPrediction] = field(default_factory=list)
    still_open: list[LoggedPrediction] = field(default_factory=list)
    superseded: int = 0
    expired: int = 0
    beyond_horizon: int = 0
    duplicates: int = 0


def _in_range(value: int, low: int | None, high: int | None) -> bool | None:
    if low is None or high is None:
        return None
    return low <= value <= high


def _horizon_seconds(pred: LoggedPrediction, cycle_seconds: float | None) -> float:
    """How late an eruption may be before we stop believing it is the right one."""
    floor = (
        HORIZON_CYCLE_FRACTION * cycle_seconds
        if cycle_seconds and cycle_seconds > 0
        else float(MIN_MATCH_HORIZON_SECONDS)
    )
    if pred.window_open_epoch is not None and pred.window_close_epoch is not None:
        width = max(pred.window_close_epoch - pred.window_open_epoch, 0)
        return max(MATCH_HORIZON_FACTOR * width, floor)
    return floor


def _drop_duplicates(eruptions: list[Eruption]) -> tuple[list[Eruption], int]:
    """Collapse entries of the same geyser closer than `DUPLICATE_WINDOW_SECONDS`.

    The earlier entry stands; the later one is the second observer.
    """
    kept: list[Eruption] = []
    last: dict[str, int] = {}
    dropped = 0
    for e in sorted(eruptions, key=lambda e: e.epoch):
        prev = last.get(e.geyser)
        if prev is not None and e.epoch - prev < DUPLICATE_WINDOW_SECONDS:
            dropped += 1
            continue
        last[e.geyser] = e.epoch
        kept.append(e)
    return kept, dropped


def score_one(pred: LoggedPrediction, eruption: Eruption) -> ScoredPrediction:
    """Judge a single prediction. Positive signed error means the eruption ran late."""
    signed = (eruption.epoch - pred.predicted_epoch) / 60.0
    width = None
    if pred.window_open_epoch is not None and pred.window_close_epoch is not None:
        width = (pred.window_close_epoch - pred.window_open_epoch) / 60.0
    brier = hit_short = None
    if pred.mode_boundary_epoch is not None and pred.mode_short_prob is not None:
        hit_short = eruption.epoch < pred.mode_boundary_epoch
        brier = round((pred.mode_short_prob - float(hit_short)) ** 2, 4)

    return ScoredPrediction(
        source=pred.source,
        geyser=pred.geyser,
        eruption_id=eruption.eruption_id,
        actual_epoch=eruption.epoch,
        issued_epoch=pred.issued_epoch,
        predicted_epoch=pred.predicted_epoch,
        signed_error_min=round(signed, 1),
        abs_error_min=round(abs(signed), 1),
        lead_minutes=round((eruption.epoch - pred.issued_epoch) / 60.0, 1),
        in_window=_in_range(eruption.epoch, pred.window_open_epoch, pred.window_close_epoch),
        window_width_min=round(width, 1) if width is not None else None,
        window_open_epoch=pred.window_open_epoch,
        window_close_epoch=pred.window_close_epoch,
        in_inner_window=_in_range(eruption.epoch, pred.inner_open_epoch, pred.inner_close_epoch),
        detail=pred.detail,
        inner_open_epoch=pred.inner_open_epoch,
        inner_close_epoch=pred.inner_close_epoch,
        mode_brier=brier,
        mode_short_prob=pred.mode_short_prob,
        mode_hit_short=hit_short,
    )


def match_and_score(
    open_predictions: list[LoggedPrediction],
    eruptions: list[Eruption],
    now_epoch: int,
    already_scored: set[tuple[str, int]] | None = None,
    stale_open_seconds: int = STALE_OPEN_SECONDS,
    cycle_seconds: dict[str, float] | None = None,
) -> MatchResult:
    """Pair every eruption with the prediction each source had open for it.

    `already_scored` holds `(source, eruption_id)` pairs that have been scored on
    a previous pass, so re-running over an overlapping window of eruptions --
    which the five-minute sync does constantly -- cannot double-count.

    `cycle_seconds` gives each geyser's typical interval, which sets the floor
    of the match horizon (see `HORIZON_CYCLE_FRACTION`). Without it the fixed
    `MIN_MATCH_HORIZON_SECONDS` applies.
    """
    already = already_scored or set()
    cycles = cycle_seconds or {}
    result = MatchResult()

    # Predictions grouped by who made them and about what.
    groups: dict[tuple[str, str], list[LoggedPrediction]] = {}
    for pred in open_predictions:
        groups.setdefault((pred.source, pred.geyser), []).append(pred)

    consumed: set[str] = set()
    eruptions, result.duplicates = _drop_duplicates(eruptions)

    for eruption in eruptions:
        for (source, geyser), preds in groups.items():
            if geyser != eruption.geyser:
                continue

            candidates = [
                p for p in preds if p.key not in consumed and p.issued_epoch < eruption.epoch
            ]
            if not candidates:
                continue

            # Latest issued wins; the ones it replaced are spent, not missed.
            winner = max(candidates, key=lambda p: (p.issued_epoch, p.key))
            for p in candidates:
                consumed.add(p.key)
            result.superseded += len(candidates) - 1

            horizon = _horizon_seconds(winner, cycles.get(geyser))
            if eruption.epoch > winner.predicted_epoch + horizon:
                # Almost certainly an unlogged eruption in between. Not scored,
                # for anyone, rather than blamed on the forecaster.
                result.beyond_horizon += 1
                continue

            if (source, eruption.eruption_id) in already:
                continue

            result.scored.append(score_one(winner, eruption))

    for pred in open_predictions:
        if pred.key in consumed:
            continue
        if now_epoch - pred.issued_epoch > stale_open_seconds:
            result.expired += 1
            continue
        result.still_open.append(pred)

    return result
