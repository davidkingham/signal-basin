# Calibration report

Walk-forward backtest, last 3 years. Generated 2026-09-13 from the GeyserTimes complete archive.

At every evaluated eruption each model sees **only** intervals strictly earlier than the one it is predicting. All models are scored on the same set of target eruptions, so no model benefits from skipping hard cases.

## Metrics

- **CRPS** (minutes, lower is better) — proper scoring rule over the whole predicted distribution.
- **MAE** (minutes) — absolute error of the predicted median.
- **50% / 90%** — empirical coverage of the nominal intervals. Closer to 50% / 90% is better; `·` marks a miss over 5 points, `⚠` over 10.


| Geyser | Model | n | CRPS (min) | MAE (min) | 50% cov | 90% cov |
|---|---|---:|---:|---:|---:|---:|
| Old Faithful | **minor_conditional** | 2,000 | 5.0 | 6.7 | 59.3% · | 91.9% |
| Old Faithful | duration_lognormal | 2,000 | 8.9 | 12.5 | 54.1% | 88.6% |
| Old Faithful | weibull | 2,000 | 9.4 | 12.9 | 54.9% | 90.2% |
| Old Faithful | best_parametric | 2,000 | 9.4 | 13.1 | 53.6% | 89.8% |
| Old Faithful | weibull_aft | 2,000 | 9.4 | 12.7 | 59.4% · | 90.1% |
| Old Faithful | rolling_normal | 2,000 | 9.6 | 13.3 | 52.4% | 88.9% |
| Old Faithful | lognormal | 2,000 | 9.7 | 13.6 | 54.1% | 88.9% |
| Old Faithful | adaptive_lognormal | 2,000 | 9.7 | 13.7 | 53.8% | 89.0% |
| Grand | **adaptive_lognormal** | 1,994 | 39.3 | 54.9 | 47.9% | 90.4% |
| Grand | ar1_lognormal | 1,994 | 39.6 | 55.3 | 47.3% | 89.6% |
| Grand | lognormal | 1,994 | 39.8 | 55.7 | 47.3% | 90.9% |
| Grand | rolling_normal | 1,994 | 39.8 | 55.6 | 49.4% | 90.6% |
| Grand | best_parametric | 1,994 | 40.4 | 56.5 | 46.8% | 91.1% |
| Grand | weibull | 1,994 | 41.2 | 56.4 | 61.8% ⚠ | 92.9% |
| Grand | entry_conditional | 1,994 | 42.8 | 59.6 | 45.8% | 89.3% |
| Grand | weibull_aft | 1,994 | 46.9 | 62.4 | 57.8% · | 87.1% |
| Daisy | **ar1_lognormal** | 2,000 | 2.9 | 3.9 | 51.7% | 87.9% |
| Daisy | adaptive_lognormal | 2,000 | 3.2 | 4.3 | 50.4% | 88.0% |
| Daisy | lognormal | 2,000 | 3.2 | 4.4 | 53.3% | 89.9% |
| Daisy | rolling_normal | 2,000 | 3.2 | 4.3 | 49.4% | 87.5% |
| Daisy | best_parametric | 2,000 | 3.3 | 4.5 | 54.2% | 90.0% |
| Daisy | entry_conditional | 2,000 | 3.5 | 4.8 | 52.6% | 90.3% |
| Daisy | weibull | 2,000 | 3.6 | 4.4 | 67.8% ⚠ | 93.9% |
| Daisy | weibull_aft | 2,000 | 5.3 | 7.2 | 62.4% ⚠ | 94.1% |
| Riverside | **best_parametric** | 1,517 | 12.7 | 17.5 | 53.7% | 90.8% |
| Riverside | adaptive_lognormal | 1,517 | 12.8 | 17.5 | 52.7% | 91.2% |
| Riverside | lognormal | 1,517 | 12.9 | 17.8 | 56.0% · | 92.2% |
| Riverside | ar1_lognormal | 1,517 | 12.9 | 17.7 | 53.1% | 90.8% |
| Riverside | rolling_normal | 1,517 | 12.9 | 17.3 | 51.3% | 90.6% |
| Riverside | weibull | 1,517 | 14.1 | 17.3 | 53.1% | 91.8% |
| Riverside | weibull_aft | 1,517 | 18.5 | 22.4 | 79.0% ⚠ | 99.3% · |
| Castle | **minor_conditional** | 809 | 80.8 | 105.7 | 60.3% ⚠ | 86.4% |
| Castle | weibull | 809 | 174.1 | 221.4 | 64.3% ⚠ | 81.1% · |
| Castle | best_parametric | 809 | 174.8 | 223.5 | 63.9% ⚠ | 81.7% · |
| Castle | rolling_normal | 809 | 175.6 | 224.6 | 64.8% ⚠ | 83.4% · |
| Castle | weibull_aft | 809 | 181.0 | 252.7 | 49.6% | 77.1% ⚠ |
| Castle | lognormal | 809 | 188.0 | 260.0 | 66.9% ⚠ | 86.9% |
| Castle | entry_conditional | 809 | 188.7 | 270.3 | 64.8% ⚠ | 87.4% |
| Castle | adaptive_lognormal | 809 | 189.1 | 259.4 | 67.0% ⚠ | 87.4% |
| Great Fountain | **lognormal** | 401 | 45.7 | 63.0 | 55.4% · | 91.0% |
| Great Fountain | best_parametric | 401 | 45.8 | 63.1 | 56.1% · | 91.8% |
| Great Fountain | entry_conditional | 401 | 46.1 | 63.2 | 58.1% · | 93.3% |
| Great Fountain | adaptive_lognormal | 401 | 46.1 | 64.0 | 53.4% | 90.0% |
| Great Fountain | ar1_lognormal | 401 | 46.2 | 64.1 | 48.9% | 88.3% |
| Great Fountain | rolling_normal | 401 | 46.5 | 63.5 | 54.9% | 89.5% |
| Great Fountain | weibull | 401 | 48.7 | 64.0 | 71.3% ⚠ | 95.3% · |
| Great Fountain | weibull_aft | 401 | 49.0 | 63.9 | 74.1% ⚠ | 97.0% · |
| Beehive | **ar1_lognormal** | 1,138 | 119.0 | 163.9 | 51.8% | 86.2% |
| Beehive | adaptive_lognormal | 1,138 | 119.7 | 165.7 | 52.7% | 87.3% |
| Beehive | rolling_normal | 1,138 | 120.0 | 166.6 | 51.2% | 87.3% |
| Beehive | lognormal | 1,138 | 122.9 | 170.2 | 51.8% | 87.4% |
| Beehive | weibull | 1,138 | 125.1 | 174.0 | 56.6% · | 90.8% |
| Beehive | best_parametric | 1,138 | 126.3 | 176.7 | 50.6% | 88.3% |
| Beehive | weibull_aft | 1,138 | 170.5 | 250.7 | 48.6% | 94.5% |
| Fountain | **adaptive_lognormal** | 604 | 34.1 | 46.8 | 52.6% | 87.6% |
| Fountain | ar1_lognormal | 604 | 34.3 | 47.0 | 50.8% | 87.3% |
| Fountain | rolling_normal | 604 | 34.4 | 47.2 | 55.8% · | 89.1% |
| Fountain | lognormal | 604 | 35.5 | 49.5 | 51.0% | 89.4% |
| Fountain | best_parametric | 604 | 36.5 | 51.2 | 51.0% | 89.1% |
| Fountain | weibull | 604 | 36.9 | 51.0 | 60.1% ⚠ | 91.7% |
| Fountain | weibull_aft | 604 | 38.2 | 51.6 | 61.8% ⚠ | 88.7% |
| Lion | **series_conditional** | 2,000 | 117.3 | 181.6 | 61.5% ⚠ | 90.8% |
| Lion | weibull_aft | 2,000 | 132.8 | 194.1 | 45.4% | 91.7% |
| Lion | weibull | 2,000 | 138.3 | 208.7 | 35.9% ⚠ | 94.7% |
| Lion | best_parametric | 2,000 | 138.8 | 202.2 | 32.0% ⚠ | 95.0% |
| Lion | lognormal | 2,000 | 139.0 | 202.2 | 31.2% ⚠ | 94.7% |
| Lion | adaptive_lognormal | 2,000 | 139.5 | 202.9 | 30.0% ⚠ | 94.7% |
| Lion | rolling_normal | 2,000 | 147.6 | 228.1 | 33.0% ⚠ | 91.0% |
| Artemisia | **best_parametric** | 362 | 177.3 | 243.3 | 52.8% | 85.6% |
| Artemisia | adaptive_lognormal | 362 | 178.0 | 244.5 | 52.5% | 87.0% |
| Artemisia | lognormal | 362 | 178.5 | 245.8 | 52.8% | 85.9% |
| Artemisia | ar1_lognormal | 362 | 179.2 | 245.7 | 52.8% | 86.2% |
| Artemisia | weibull | 362 | 180.5 | 247.8 | 57.7% · | 90.3% |
| Artemisia | rolling_normal | 362 | 182.7 | 253.4 | 53.9% | 86.5% |
| Artemisia | weibull_aft | 362 | 200.7 | 268.3 | 54.4% | 85.9% |
| Lone Star | **best_parametric** | 44 | 17.7 | 24.4 | 56.8% · | 90.9% |
| Lone Star | lognormal | 44 | 17.7 | 24.4 | 52.3% | 88.6% |
| Lone Star | adaptive_lognormal | 44 | 17.8 | 24.2 | 52.3% | 88.6% |
| Lone Star | ar1_lognormal | 44 | 18.2 | 24.4 | 52.3% | 84.1% · |
| Lone Star | rolling_normal | 44 | 18.2 | 24.0 | 54.5% | 90.9% |
| Lone Star | weibull | 44 | 18.4 | 24.0 | 70.5% ⚠ | 95.5% · |
| Lone Star | weibull_aft | 44 | 19.1 | 25.7 | 56.8% · | 86.4% |
| Till | **adaptive_lognormal** | 63 | 26.1 | 34.4 | 57.1% · | 87.3% |
| Till | rolling_normal | 63 | 26.4 | 35.7 | 54.0% | 90.5% |
| Till | ar1_lognormal | 63 | 26.6 | 34.4 | 50.8% | 79.4% ⚠ |
| Till | lognormal | 63 | 45.9 | 58.9 | 82.5% ⚠ | 96.8% · |
| Till | weibull | 63 | 49.0 | 50.0 | 92.1% ⚠ | 98.4% · |
| Till | best_parametric | 63 | 60.9 | 85.6 | 81.0% ⚠ | 100.0% · |
| Till | weibull_aft | 63 | 73.4 | 110.6 | 34.9% ⚠ | 93.7% |
| Little Squirt | **rolling_normal** | 308 | 540.4 | 771.2 | 43.5% · | 87.0% |
| Little Squirt | ar1_lognormal | 308 | 540.9 | 767.8 | 41.2% · | 86.7% |
| Little Squirt | adaptive_lognormal | 308 | 547.6 | 785.9 | 42.9% · | 88.0% |
| Little Squirt | lognormal | 308 | 554.3 | 798.8 | 42.9% · | 88.0% |
| Little Squirt | best_parametric | 308 | 560.1 | 813.9 | 39.6% ⚠ | 89.9% |
| Little Squirt | weibull | 308 | 565.9 | 829.7 | 47.4% | 90.9% |
| Little Squirt | weibull_aft | 308 | 781.4 | 1,186.5 | 30.8% ⚠ | 94.8% |

**Bold** = best CRPS for that geyser.

## Which model wins

The last two columns are a paired bootstrap of per-eruption CRPS differences, winner minus the model actually served, on the identical evaluation set (4,000 resamples). A geyser is pinned to its winner only when that 95% interval is clear of zero -- **decisive** -- not when a percentage looks large. Where the winner *is* the served model the columns are empty.

| Geyser | Best by CRPS | CRPS | Served | Baseline CRPS | Improvement | Winner − served (min) | 95% CI |
|---|---|---:|---|---:|---:|---:|---:|
| Old Faithful | minor_conditional | 5.0 | minor_conditional | 9.6 | 47.6% |  |  |
| Grand | adaptive_lognormal | 39.3 | adaptive_lognormal | 39.8 | 1.3% |  |  |
| Daisy | ar1_lognormal | 2.9 | ar1_lognormal | 3.2 | 9.6% |  |  |
| Riverside | best_parametric | 12.7 | best_parametric | 12.9 | 1.4% |  |  |
| Castle | minor_conditional | 80.8 | minor_conditional | 175.6 | 54.0% |  |  |
| Great Fountain | lognormal | 45.7 | best_parametric | 46.5 | 1.8% | -0.10 | [-0.47, +0.31] |
| Beehive | ar1_lognormal | 119.0 | adaptive_lognormal | 120.0 | 0.8% | -0.69 | [-2.55, +1.18] |
| Fountain | adaptive_lognormal | 34.1 | adaptive_lognormal | 34.4 | 0.8% |  |  |
| Lion | series_conditional | 117.3 | series_conditional | 147.6 | 20.5% |  |  |
| Artemisia | best_parametric | 177.3 | best_parametric | 182.7 | 3.0% |  |  |
| Lone Star | best_parametric | 17.7 | best_parametric | 18.2 | 3.1% |  |  |
| Till | adaptive_lognormal | 26.1 | adaptive_lognormal | 26.4 | 1.4% |  |  |
| Little Squirt | rolling_normal | 540.4 | best_parametric | 540.4 | 0.0% | -19.75 | [-44.15, +4.78] |

## Known gaps

- **Castle** — the best model (`minor_conditional`) is far too wide: its nominal 50% interval actually covers 60%. The predicted distribution is the wrong *shape*, not just the wrong width.
- **Lion** — the best model (`series_conditional`) is far too wide: its nominal 50% interval actually covers 61%. The predicted distribution is the wrong *shape*, not just the wrong width.

- **The covariate model did not earn its complexity.** `weibull_aft` (lifelines Weibull AFT with previous-interval, clock-time, seasonal and entry-flag covariates) ranks in the bottom half on 12 of 13 geysers: Old Faithful 5/8, Grand 8/8, Daisy 8/8, Riverside 7/7, Castle 5/8, Great Fountain 8/8, Beehive 7/7, Fountain 7/7, Lion 2/7, Artemisia 7/7, Lone Star 7/7, Till 7/7, Little Squirt 7/7. The simple rolling lognormal/Weibull fits beat it nearly everywhere, and the dashboard-style baseline is competitive. Reported as-is.


### Honest coverage: scoring the intervals the filter throws away

Everything above is measured only on intervals that passed the validity filter, which quietly excludes exactly the cases the filter exists to remove — stretches where an eruption went unlogged. A gazer on the boardwalk gets no such exemption. The table below re-scores a plain rolling `lognormal` (trained only on valid history, as always) against **every** interval in the window, so the gap between the two numbers is the honest cost of observation gaps.


| Geyser | n (all) | % filter-rejected | 50% cov | 90% cov | 90% cov (filtered) |
|---|---:|---:|---:|---:|---:|
| Old Faithful | 1,500 | 14.3% | 47.2% | 75.7% | 88.9% |
| Grand | 1,500 | 20.3% | 38.0% | 72.9% | 90.9% |
| Daisy | 1,500 | 21.3% | 42.9% | 71.4% | 89.9% |
| Riverside | 1,500 | 34.3% | 36.5% | 60.3% | 92.2% |
| Castle | 1,181 | 31.5% | 50.4% | 72.1% | 86.9% |
| Great Fountain | 689 | 41.8% | 32.2% | 53.0% | 91.0% |
| Beehive | 1,242 | 8.4% | 47.5% | 80.1% | 87.4% |
| Fountain | 1,183 | 48.9% | 26.0% | 45.6% | 89.4% |
| Lion | 1,500 | 10.3% | 29.5% | 86.1% | 94.7% |
| Artemisia | 518 | 30.1% | 36.9% | 60.0% | 85.9% |
| Lone Star | 171 | 74.3% | 13.5% | 22.8% | 88.6% |
| Till | 218 | 71.1% | 23.9% | 28.0% | 96.8% |
| Little Squirt | 355 | 13.2% | 37.2% | 76.3% | 88.0% |

The drop between the last two columns is the real-world penalty. Treat the headline table as an upper bound on field reliability, and see the renewal/missed-eruption handling in `predict` (README) for how the CLI compensates at prediction time.


## Neighbour-geyser conditioning (nowcast)

The interval harness above asks *how long is the gap after this eruption*. That cannot express what actually helps a gazer — *standing here now, with Beehive's Indicator running, when does it go?* — so these are scored from decision times on a fixed 30-minute grid, independent of when eruptions happen. Scoring only just-before-an-eruption would be conditioning on the answer.


Each decision time is scored **twice, identically**, with neighbour conditioning on and off. Only a paired delta is meaningful here: the conditioned moments are not a random sample of time.


| Geyser | Regime | n | CRPS off | CRPS on | Δ | 90% off | 90% on |
|---|---|---:|---:|---:|---:|---:|---:|
| Grand | **overall** | 20,107 | 44.7 | 44.8 | +0.2% | 90% | 90% |
| Grand | base | 16,854 | 43.9 | 43.9 | +0.0% | 90% | 90% |
| Grand | precursor_shifted | 1,680 | 53.5 | 54.3 | +1.4% | 88% | 86% |
| Grand | turban_gated | 1,573 | 44.0 | 44.1 | +0.2% | 92% | 91% |
| Beehive | **overall** | 28,893 | 118.0 | 115.1 | -2.4% | 86% | 90% |
| Beehive | base | 27,000 | 116.0 | 116.0 | -0.0% | 90% | 90% |
| Beehive | indicator_active | 1,893 | 145.8 | 102.4 | -29.8% | 29% | 86% |

**Beehive's Indicator works.** In the minutes it is running, CRPS falls by about a third and nominal 90% coverage goes from badly overconfident to roughly honest. The no-Indicator regime is untouched, which is the point — the conditioning adds information only when there is information to add. Residual error in that regime is dominated by cycles where the Beehive eruption itself was never logged (~6% of Indicator entries), not by the model.


**Grand's Turban lattice does not work, and is off by default.** Grand starts *with* a Turban — only 0.1% of starts fall 5-13 minutes after one, against 24% in the first two minutes — so gating the density onto that lattice looks obviously right. It isn't. Turban's own interval scatters (sd 4.2 min on a 19 min period) so extrapolated phase decoheres within about one cycle, and Grand's own uncertainty is ~100 min, five times the Turban period. The model's predicted median never drops below 40 minutes, so the lattice is never consulted at a range where it could discriminate. Rift and West Triplet shifts (+32 and +15 min, both highly significant under a length-bias-safe test) likewise fail to improve the distribution. Both are kept switchable so the negative result stays reproducible.


## Figures

![interval_histograms.png](figures/interval_histograms.png)

![calibration_reliability.png](figures/calibration_reliability.png)

![example_density_old_faithful.png](figures/example_density_old_faithful.png)

## Data-quality notes

- Of 1,338,654 consecutive-eruption gaps, 1,075,432 (80.3%) pass the per-geyser plausibility filter (0.35x-3x that geyser's median). The rest are overwhelmingly observation gaps — nobody is watching Riverside at 3am in February — not real eruptions.

- The ceiling is **1.75x** the median rather than the more obvious 3x because the interval histograms show clear **harmonics**: Riverside clusters at ~390, ~780 and ~1150 minutes, Great Fountain at ~686 and ~1400. Those secondary peaks sit at exactly 2x and 3x the median and are one and two missed eruptions. A 3x ceiling admits them, and models trained on the contaminated series predict distributions far wider than reality — it was worth several times more CRPS than any modeling choice in this report.

Observation-entry mix since 2015 (% of valid intervals):

| Geyser | webcam | electronic | approximate | in-eruption |
|---|---:|---:|---:|---:|
| Artemisia | 15.4% | 35.6% | 9.1% | 21.7% |
| Beehive | 48.2% | 1.2% | 1.5% | 2.7% |
| Castle | 48.7% | 20.2% | 1.1% | 11.2% |
| Daisy | 58.2% | 24.2% | 0.6% | 6.9% |
| Fountain | 0.0% | 56.2% | 0.8% | 5.7% |
| Grand | 45.8% | 15.5% | 1.2% | 5.2% |
| Great Fountain | 0.0% | 62.5% | 0.8% | 2.6% |
| Lion | 69.9% | 4.4% | 0.2% | 9.4% |
| Little Squirt | 52.8% | 17.3% | 0.4% | 78.1% |
| Lone Star | 0.0% | 0.0% | 15.9% | 2.6% |
| Old Faithful | 91.1% | 3.8% | 0.3% | 3.4% |
| Riverside | 62.0% | 1.1% | 1.2% | 21.9% |
| Till | 0.0% | 59.3% | 0.9% | 12.8% |

Data courtesy of [GeyserTimes.org](https://geysertimes.org) and its community of volunteer observers.
