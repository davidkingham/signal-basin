# Calibration report

Walk-forward backtest, last 3 years. Generated 2026-08-04 from the GeyserTimes complete archive.

At every evaluated eruption each model sees **only** intervals strictly earlier than the one it is predicting. All models are scored on the same set of target eruptions, so no model benefits from skipping hard cases.

## Metrics

- **CRPS** (minutes, lower is better) — proper scoring rule over the whole predicted distribution.
- **MAE** (minutes) — absolute error of the predicted median.
- **50% / 90%** — empirical coverage of the nominal intervals. Closer to 50% / 90% is better; `·` marks a miss over 5 points, `⚠` over 10.


| Geyser | Model | n | CRPS (min) | MAE (min) | 50% cov | 90% cov |
|---|---|---:|---:|---:|---:|---:|
| Old Faithful | **minor_conditional** | 2,000 | 4.5 | 6.1 | 58.9% · | 93.3% |
| Old Faithful | duration_lognormal | 2,000 | 8.2 | 11.6 | 55.1% · | 89.3% |
| Old Faithful | weibull | 2,000 | 8.8 | 12.0 | 57.0% · | 91.2% |
| Old Faithful | best_parametric | 2,000 | 8.8 | 12.2 | 54.4% | 90.8% |
| Old Faithful | rolling_normal | 2,000 | 8.9 | 12.4 | 53.8% | 89.8% |
| Old Faithful | weibull_aft | 2,000 | 8.9 | 11.9 | 61.6% ⚠ | 91.0% |
| Old Faithful | lognormal | 2,000 | 9.0 | 12.7 | 54.4% | 89.3% |
| Old Faithful | adaptive_lognormal | 2,000 | 9.0 | 12.7 | 54.9% | 89.4% |
| Grand | **adaptive_lognormal** | 2,000 | 38.8 | 54.2 | 47.8% | 90.6% |
| Grand | lognormal | 2,000 | 39.3 | 55.0 | 47.2% | 91.0% |
| Grand | rolling_normal | 2,000 | 39.3 | 54.9 | 49.2% | 90.6% |
| Grand | best_parametric | 2,000 | 39.9 | 55.8 | 46.7% | 91.3% |
| Grand | weibull | 2,000 | 40.8 | 55.8 | 61.8% ⚠ | 93.0% |
| Grand | entry_conditional | 2,000 | 42.0 | 58.5 | 46.0% | 89.5% |
| Grand | weibull_aft | 2,000 | 46.7 | 62.8 | 55.5% · | 86.7% |
| Daisy | **adaptive_lognormal** | 2,000 | 3.1 | 4.3 | 51.9% | 88.6% |
| Daisy | lognormal | 2,000 | 3.2 | 4.3 | 54.1% | 90.0% |
| Daisy | rolling_normal | 2,000 | 3.2 | 4.3 | 51.4% | 87.5% |
| Daisy | best_parametric | 2,000 | 3.2 | 4.4 | 54.9% | 90.1% |
| Daisy | entry_conditional | 2,000 | 3.4 | 4.7 | 54.1% | 91.1% |
| Daisy | weibull | 2,000 | 3.5 | 4.3 | 69.6% ⚠ | 94.1% |
| Daisy | weibull_aft | 2,000 | 5.1 | 6.9 | 67.0% ⚠ | 95.9% · |
| Riverside | **adaptive_lognormal** | 1,581 | 12.8 | 17.5 | 53.3% | 91.1% |
| Riverside | best_parametric | 1,581 | 12.8 | 17.5 | 54.9% | 91.1% |
| Riverside | lognormal | 1,581 | 12.9 | 17.8 | 56.9% · | 92.1% |
| Riverside | rolling_normal | 1,581 | 12.9 | 17.3 | 51.6% | 90.4% |
| Riverside | weibull | 1,581 | 14.2 | 17.4 | 54.3% | 92.0% |
| Riverside | weibull_aft | 1,581 | 18.6 | 22.8 | 77.0% ⚠ | 99.2% · |
| Castle | **minor_conditional** | 874 | 77.2 | 101.2 | 60.9% ⚠ | 87.2% |
| Castle | weibull | 874 | 171.8 | 220.7 | 63.5% ⚠ | 81.1% · |
| Castle | best_parametric | 874 | 172.5 | 222.6 | 63.0% ⚠ | 81.7% · |
| Castle | rolling_normal | 874 | 173.6 | 224.4 | 64.0% ⚠ | 83.6% · |
| Castle | weibull_aft | 874 | 177.6 | 248.0 | 51.6% | 77.7% ⚠ |
| Castle | lognormal | 874 | 186.0 | 259.5 | 66.7% ⚠ | 87.2% |
| Castle | entry_conditional | 874 | 186.3 | 267.4 | 65.2% ⚠ | 87.4% |
| Castle | adaptive_lognormal | 874 | 187.1 | 258.9 | 66.7% ⚠ | 87.3% |
| Great Fountain | **lognormal** | 408 | 45.6 | 62.9 | 55.1% · | 91.2% |
| Great Fountain | best_parametric | 408 | 45.7 | 63.0 | 55.9% · | 91.9% |
| Great Fountain | entry_conditional | 408 | 46.0 | 63.1 | 58.3% · | 93.4% |
| Great Fountain | adaptive_lognormal | 408 | 46.0 | 63.9 | 53.2% | 90.2% |
| Great Fountain | rolling_normal | 408 | 46.4 | 63.4 | 54.7% | 89.5% |
| Great Fountain | weibull | 408 | 48.6 | 63.9 | 71.6% ⚠ | 95.3% · |
| Great Fountain | weibull_aft | 408 | 48.8 | 63.8 | 74.0% ⚠ | 97.1% · |
| Beehive | **rolling_normal** | 1,179 | 120.4 | 166.9 | 51.5% | 87.4% |
| Beehive | adaptive_lognormal | 1,179 | 120.8 | 167.2 | 52.4% | 87.4% |
| Beehive | lognormal | 1,179 | 123.8 | 171.8 | 51.3% | 87.4% |
| Beehive | weibull | 1,179 | 125.6 | 174.8 | 56.5% · | 90.8% |
| Beehive | best_parametric | 1,179 | 126.4 | 177.0 | 51.0% | 88.5% |
| Beehive | weibull_aft | 1,179 | 169.6 | 248.5 | 48.3% | 94.5% |

**Bold** = best CRPS for that geyser.

## Which model wins

| Geyser | Best by CRPS | CRPS | Baseline CRPS | Improvement |
|---|---|---:|---:|---:|
| Old Faithful | minor_conditional | 4.5 | 8.9 | 49.2% |
| Grand | adaptive_lognormal | 38.8 | 39.3 | 1.4% |
| Daisy | adaptive_lognormal | 3.1 | 3.2 | 1.5% |
| Riverside | adaptive_lognormal | 12.8 | 12.9 | 1.4% |
| Castle | minor_conditional | 77.2 | 173.6 | 55.5% |
| Great Fountain | lognormal | 45.6 | 46.4 | 1.8% |
| Beehive | rolling_normal | 120.4 | 120.4 | 0.0% |

## Known gaps

- **Castle** — the best model (`minor_conditional`) is far too wide: its nominal 50% interval actually covers 61%. The predicted distribution is the wrong *shape*, not just the wrong width.

- **The covariate model did not earn its complexity.** `weibull_aft` (lifelines Weibull AFT with previous-interval, clock-time, seasonal and entry-flag covariates) ranks in the bottom half on 7 of 7 geysers: Old Faithful 6/8, Grand 7/7, Daisy 7/7, Riverside 6/6, Castle 5/8, Great Fountain 7/7, Beehive 6/6. The simple rolling lognormal/Weibull fits beat it nearly everywhere, and the dashboard-style baseline is competitive. Reported as-is.


### Honest coverage: scoring the intervals the filter throws away

Everything above is measured only on intervals that passed the validity filter, which quietly excludes exactly the cases the filter exists to remove — stretches where an eruption went unlogged. A gazer on the boardwalk gets no such exemption. The table below re-scores a plain rolling `lognormal` (trained only on valid history, as always) against **every** interval in the window, so the gap between the two numbers is the honest cost of observation gaps.


| Geyser | n (all) | % filter-rejected | 50% cov | 90% cov | 90% cov (filtered) |
|---|---:|---:|---:|---:|---:|
| Old Faithful | 1,500 | 14.0% | 46.1% | 76.0% | 89.3% |
| Grand | 1,500 | 19.3% | 38.2% | 74.1% | 91.0% |
| Daisy | 1,500 | 21.4% | 42.6% | 70.0% | 90.0% |
| Riverside | 1,500 | 37.2% | 35.8% | 57.5% | 92.1% |
| Castle | 1,249 | 30.0% | 51.2% | 73.2% | 87.2% |
| Great Fountain | 722 | 43.5% | 31.2% | 51.5% | 91.2% |
| Beehive | 1,284 | 8.2% | 47.1% | 80.3% | 87.4% |

The drop between the last two columns is the real-world penalty. Treat the headline table as an upper bound on field reliability, and see the renewal/missed-eruption handling in `predict` (README) for how the CLI compensates at prediction time.


## Neighbour-geyser conditioning (nowcast)

The interval harness above asks *how long is the gap after this eruption*. That cannot express what actually helps a gazer — *standing here now, with Beehive's Indicator running, when does it go?* — so these are scored from decision times on a fixed 30-minute grid, independent of when eruptions happen. Scoring only just-before-an-eruption would be conditioning on the answer.


Each decision time is scored **twice, identically**, with neighbour conditioning on and off. Only a paired delta is meaningful here: the conditioned moments are not a random sample of time.


| Geyser | Regime | n | CRPS off | CRPS on | Δ | 90% off | 90% on |
|---|---|---:|---:|---:|---:|---:|---:|
| Grand | **overall** | 21,631 | 44.6 | 44.6 | +0.1% | 90% | 90% |
| Grand | base | 18,228 | 43.9 | 43.9 | +0.0% | 90% | 90% |
| Grand | precursor_shifted | 1,687 | 53.8 | 54.6 | +1.3% | 87% | 86% |
| Grand | turban_gated | 1,716 | 42.3 | 42.5 | +0.3% | 92% | 91% |
| Beehive | **overall** | 30,965 | 118.2 | 114.7 | -2.9% | 86% | 89% |
| Beehive | base | 28,882 | 116.1 | 116.1 | +0.0% | 90% | 90% |
| Beehive | indicator_active | 2,083 | 147.7 | 96.2 | -34.9% | 30% | 88% |

**Beehive's Indicator works.** In the minutes it is running, CRPS falls by about a third and nominal 90% coverage goes from badly overconfident to roughly honest. The no-Indicator regime is untouched, which is the point — the conditioning adds information only when there is information to add. Residual error in that regime is dominated by cycles where the Beehive eruption itself was never logged (~6% of Indicator entries), not by the model.


**Grand's Turban lattice does not work, and is off by default.** Grand starts *with* a Turban — only 0.1% of starts fall 5-13 minutes after one, against 24% in the first two minutes — so gating the density onto that lattice looks obviously right. It isn't. Turban's own interval scatters (sd 4.2 min on a 19 min period) so extrapolated phase decoheres within about one cycle, and Grand's own uncertainty is ~100 min, five times the Turban period. The model's predicted median never drops below 40 minutes, so the lattice is never consulted at a range where it could discriminate. Rift and West Triplet shifts (+32 and +15 min, both highly significant under a length-bias-safe test) likewise fail to improve the distribution. Both are kept switchable so the negative result stays reproducible.


## Figures

![interval_histograms.png](figures/interval_histograms.png)

![calibration_reliability.png](figures/calibration_reliability.png)

![example_density_old_faithful.png](figures/example_density_old_faithful.png)

## Data-quality notes

- Of 1,340,410 consecutive-eruption gaps, 1,008,658 (75.2%) pass the per-geyser plausibility filter (0.35x-3x that geyser's median). The rest are overwhelmingly observation gaps — nobody is watching Riverside at 3am in February — not real eruptions.

- The ceiling is **1.75x** the median rather than the more obvious 3x because the interval histograms show clear **harmonics**: Riverside clusters at ~390, ~780 and ~1150 minutes, Great Fountain at ~686 and ~1400. Those secondary peaks sit at exactly 2x and 3x the median and are one and two missed eruptions. A 3x ceiling admits them, and models trained on the contaminated series predict distributions far wider than reality — it was worth several times more CRPS than any modeling choice in this report.

Observation-entry mix since 2015 (% of valid intervals):

| Geyser | webcam | electronic | approximate | in-eruption |
|---|---:|---:|---:|---:|
| Beehive | 48.2% | 1.2% | 1.5% | 2.7% |
| Castle | 48.7% | 20.2% | 1.1% | 11.2% |
| Daisy | 58.2% | 24.2% | 0.6% | 6.9% |
| Grand | 45.8% | 15.5% | 1.2% | 5.2% |
| Great Fountain | 0.0% | 62.5% | 0.8% | 2.6% |
| Old Faithful | 91.1% | 3.8% | 0.3% | 3.4% |
| Riverside | 62.0% | 1.1% | 1.2% | 21.9% |

Data courtesy of [GeyserTimes.org](https://geysertimes.org) and its community of volunteer observers.
