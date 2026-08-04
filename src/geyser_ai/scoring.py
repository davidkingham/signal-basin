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
MIN_MATCH_HORIZON_SECONDS = 6 * 3600

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


def _in_range(value: int, low: int | None, high: int | None) -> bool | None:
    if low is None or high is None:
        return None
    return low <= value <= high


def _horizon_seconds(pred: LoggedPrediction) -> float:
    """How late an eruption may be before we stop believing it is the right one."""
    if pred.window_open_epoch is not None and pred.window_close_epoch is not None:
        width = max(pred.window_close_epoch - pred.window_open_epoch, 0)
        return max(MATCH_HORIZON_FACTOR * width, MIN_MATCH_HORIZON_SECONDS)
    return float(MIN_MATCH_HORIZON_SECONDS)


def score_one(pred: LoggedPrediction, eruption: Eruption) -> ScoredPrediction:
    """Judge a single prediction. Positive signed error means the eruption ran late."""
    signed = (eruption.epoch - pred.predicted_epoch) / 60.0
    width = None
    if pred.window_open_epoch is not None and pred.window_close_epoch is not None:
        width = (pred.window_close_epoch - pred.window_open_epoch) / 60.0

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
    )


def match_and_score(
    open_predictions: list[LoggedPrediction],
    eruptions: list[Eruption],
    now_epoch: int,
    already_scored: set[tuple[str, int]] | None = None,
    stale_open_seconds: int = STALE_OPEN_SECONDS,
) -> MatchResult:
    """Pair every eruption with the prediction each source had open for it.

    `already_scored` holds `(source, eruption_id)` pairs that have been scored on
    a previous pass, so re-running over an overlapping window of eruptions --
    which the five-minute sync does constantly -- cannot double-count.
    """
    already = already_scored or set()
    result = MatchResult()

    # Predictions grouped by who made them and about what.
    groups: dict[tuple[str, str], list[LoggedPrediction]] = {}
    for pred in open_predictions:
        groups.setdefault((pred.source, pred.geyser), []).append(pred)

    consumed: set[str] = set()

    for eruption in sorted(eruptions, key=lambda e: e.epoch):
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

            if eruption.epoch > winner.predicted_epoch + _horizon_seconds(winner):
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
