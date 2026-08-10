# Live scoreboard: why Old Faithful was 3× worse than the backtest promised

First live head-to-head against the NPS and Geysers.net (ledger, 2026-08-04 →
2026-08-08). The backtest said Old Faithful was our best geyser — MAE 6.1 with
`minor_conditional`. The scoreboard said otherwise:

| Old Faithful | n | MAE | bias | in window |
|---|---:|---:|---:|---:|
| NPS | 35 | **5.6** | +2.7 | 86% |
| Geysers.net | 37 | 10.6 | +7.1 | 92% |
| **us** | 37 | **19.2** | +6.2 | 95% |

## The diagnosis: production predicted 93.0 minutes every single time

Reconstructing each prediction's interval from the ledger
(`predicted_epoch − anchor epoch`): 29 of the last 31 predictions were exactly
**93.0 min**, the rest 92–97. The actual intervals over the same window are
cleanly bimodal — 63–82 min after a minor, 96–118 after a full eruption. NPS
and Geysers.net track both modes; we served the midpoint of a distribution the
geyser never draws from, erring **+15 to +25 on long intervals and −15 to −30
on short ones**. Small bias, huge MAE — the signature of a conditional model
that is not conditioning.

## The mechanism: the serving path discards the model it just ran

`predict.predict_geyser` runs `minor_conditional` correctly — and then serves
something else:

```python
pred = model.fit_predict(hist, row)            # branch-aware fit, correct
...
base_dist = fit_tail_mixture(hist["interval_min"].to_numpy()) or pred.dist
rpred, ... = renewal_forecast(base_dist, max(age_min, 0.0), p_obs)
med = rpred.median()                           # <- what gets served and logged
```

`fit_tail_mixture` fits the **unconditional marginal** over all recent
intervals — a lognormal whose median for Old Faithful is ~93 min, the geometric
mean of the 70-min and 102-min modes. The renewal wrapper (correct in itself,
and needed for stale data) is anchored to that marginal, so on fresh data it
faithfully reproduces… the marginal. The branch-aware `pred` survives only in
the `naive_*` diagnostic fields, which nothing serves; the `or pred.dist`
fallback fires only when `fit_tail_mixture` returns `None`, i.e. under 12
intervals of history — never, for these geysers. The ledger logs
`median_interval_min` (service.py line ~176), so the scoreboard scored the
marginal, correctly.

The tell in hindsight: `model-results.md` celebrated switching production to
`minor_conditional` (−49% CRPS), but the switch only changed which model name
appears in `detail`. The served number never depended on it.

**Castle, same signature, worse magnitude**: live MAE 476 min, bias +247 (n=6)
vs NPS 30. Castle's modes are 375 vs ~1081 min; the marginal's geometric mean
(~640) is far from both. The commit that "serves the model that uses it" was
defeated by the same override.

## The fix is viable: the `minor` flag is live in the API feed

Concern checked before proposing a fix: is the flag present at prediction time,
or added by later edits? Pulled the raw API entries for the scored window
(43 entries, 14 flagged minor). The flag was in the feed and fully predictive:

- every minor-flagged eruption → next interval **63–82 min**
- every unflagged eruption → next interval **96–118 min** (missed-eruption
  gaps aside)

Our predictions were issued with ~90–100 min lead, i.e. minutes after the
anchor was logged, and the branches above are computed from those same live
entries — so the flag arrives with the entry, not with an edit.

## Counterfactual on the same scored eruptions

Replaying the exact scored intervals with the backtest branch medians (70 post-
minor, 102 post-major), excluding 7 intervals >150 min where GT plainly missed
an eruption:

| | MAE (clean rows) |
|---|---:|
| served (constant 93) | 15.3 |
| conditional branches | **7.1** |
| NPS | 5.6 |

The fix closes most of the gap to NPS. The remaining ~1.5 min is structural:
NPS conditions on *duration* (finer than the binary flag) and re-anchors from
Visitor Center logs on eruptions GT never receives — our two worst clean-path
misses (+78.8, +100.7) were 171- and 198-min GT gaps that NPS scored at +6 and
+15 because they saw the intervening eruption and we did not.

**The fix (implemented 2026-08-08)**: `fit_tail_mixture` takes an optional
`narrow=` distribution, so the serving path widens the *conditional* fit
(`pred.dist`) instead of replacing it — the wide component is centred on the
narrow one's median, so widening never moves the median. `renewal_forecast`
gained `rest_dist=`: the first simulated interval draws from the branch fit,
but intervals past the first missed eruption revert to the marginal, because
that eruption's branch is unknown. With fresh data the forecast now reduces to
the naive conditional answer, which is what the docstring always claimed.
Regression-tested in `tests/test_serving_path.py` against a synthetic geyser
with Old Faithful's branch structure whose last eruption is a minor: the old
path serves 91.8 where the branch answer is 70.2; the new path serves the
branch.

## The second catch: "overdue" at 9am (2026-08-09)

The morning Fountain went live, the dashboard called it **overdue** after a
12-hour overnight silence — exactly the failure the renewal forecast exists to
prevent, live on the front page.

The mechanism: observation completeness was evaluated **at the present
instant** and applied to the whole silent window. At 9am with 11 basin entries
in 45 minutes, live activity said `p_obs = 0.995`; at 0.995 a missed eruption
carries weight 0.005, so the model concluded nothing could have been missed
and Fountain must be running 12 hours late. The eruptions were missed at
~2am, when nobody was at Fountain Paint Pots. The seven original geysers never
tripped this because their data is never 12 hours stale in season — Fountain
is the first target sparse enough to expose it, on day one.

A second trap underneath: the existing hourly table (`hourly_observation_rate`)
could not correct this, because it buckets *validity by the anchor's hour* —
survivorship keeps its night buckets high (Fountain's 2am bucket reads 0.83;
the only people logging 2am anchors are all-night gazers who also catch the
next one).

**The fix**: `hourly_logging_profile` estimates P(an eruption at local hour h
gets logged) from entry *density* by clock hour — geysers don't keep clock
time, so logged-entry density by hour is proportional to exactly this — and
`renewal_forecast` now weights each simulated missed eruption by the
probability **at the hour it would have occurred**, with live basin activity
lifting only the current hour. Fountain's warm-season profile: 0.35–0.43 at
night, ~0.99 through the watched day. Same 12-hour window after the fix:
current-cycle probability 0.007, ~1.7 eruptions missed, overdue **false**,
next predicted about an interval after the inferred overnight eruption.
Regression-tested in `test_overdue.py::TestOvernightGapIsNotOverdue`, including
the executable form of the trap and the dual invariant (a geyser late across
*watched* hours must still read overdue). Note the bug only reproduces on the
tail-widened mixture production actually serves — on a bare lognormal the
missed-eruption branch wins even at p_obs 0.995, which is itself a lesson in
testing against the served distribution.

**Coda, same day**: Castle then wore the "overdue — expected any minute" badge
at 27 h post-major (1.5× its 18 h median) *with the fix active*. Not the same
bug — Castle's night logging is real (26.6% of its entries land 23:00–05:59;
it is webcam-visible), so the model kept 26% on "still in cycle" — but the
`overdue` flag fired on `p_current > 0.1`, asserting a hypothesis the model
itself rejected at 3-to-1, and one the empirical record prices at 0.3% (16 of
4,772 post-major intervals since 2015 reached 26.9 h). The flag now requires
the current-cycle hypothesis to be **dominant** (`p_current > 0.5`): a watched
geyser running late keeps its badge, because while people are watching, misses
are near-impossible and p_current stays high; a plausibly-missed one reads
"likely unlogged" instead. Left open, deliberately: the tail mixture gives
"27 h late" a ~4% prior against Castle's empirical 0.3% — the wide component
may be too heavy for the long-interval geysers, but that is a calibration
question for the backtest, not a badge question.

## The third catch: the Indicator nowcast was blind in production (2026-08-10)

A scored Beehive eruption showed the tell: "9.3 min early, IN WINDOW,
**18h 25m ahead**" — the base interval model, scored at eighteen hours'
lead, on an eruption whose Indicator was logged in GeyserTimes **ten
minutes before the water**, in real time. The nowcast should have issued
and superseded the base prediction; it never did, because
`load_eruption_epochs` read only the frozen archive snapshot and never
unioned `recent_eruptions` — the live-sync table where every real entry
lives between snapshot publishes. The anchor path got that union from day
one, which is why base predictions worked live and hid the gap: **the
project's flagship live signal had never fired in production.** Local
tests never caught it because the fixture builds everything into the
archive path. Fixed with the same union the anchor uses, plus a
regression test whose Indicator entry exists ONLY in the sync table.
Pattern to remember: any reader that consumes eruption times must union
both tables, and a test proving a live-only entry is visible belongs next
to every one of them.

## Footnotes for data-quality.md

- The ledger scored an Old Faithful eruption at 08-06 15:32 UTC that no longer
  exists in the API — entries can be deleted after we score against them. The
  ledger is append-only, so a later deletion silently leaves a scored row with
  no surviving ground truth.
- Geysers.net's Riverside predictions are badly biased this week (MAE 38.3,
  40% in-window vs our 17.7 / 90%) — the scoreboard is doing its job in both
  directions.


## Disposition of the pre-calibration rows (2026-08-10)

The 130 rows this project scored before the serving fix went live
(2026-08-09T04:00Z) are **removed from the ledger and all public surfaces**
at the owner's decision: the official record starts with the calibrated
system. The removal is enforced at ledger load (`config.CALIBRATION_EPOCH`),
so no flush resurrects them; the rows themselves are preserved off-display
in `ledger/archive-precalibration.json` in R2, and the numbers quoted
throughout this document remain the receipts.

Extended the same day: the record start is **source-blind** — the 219
third-party rows from the same era were aligned out as well (also
archived), so the head-to-head comparison runs every source over the same
window, and the scoreboard header dates the record from the calibration
epoch. Nothing was backfilled: every number that remains on the scoreboard
was logged before its eruption, without exception.
