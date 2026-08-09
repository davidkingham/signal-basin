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

## Footnotes for data-quality.md

- The ledger scored an Old Faithful eruption at 08-06 15:32 UTC that no longer
  exists in the API — entries can be deleted after we score against them. The
  ledger is append-only, so a later deletion silently leaves a scored row with
  no surviving ground truth.
- Geysers.net's Riverside predictions are badly biased this week (MAE 38.3,
  40% in-window vs our 17.7 / 90%) — the scoreboard is doing its job in both
  directions.
