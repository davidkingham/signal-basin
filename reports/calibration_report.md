# Calibration report

Walk-forward backtest, last 3 years. Generated 2026-08-03 from the GeyserTimes complete archive.

At every evaluated eruption each model sees **only** intervals strictly earlier than the one it is predicting. All models are scored on the same set of target eruptions, so no model benefits from skipping hard cases.

## Metrics

- **CRPS** (minutes, lower is better) — proper scoring rule over the whole predicted distribution.
- **MAE** (minutes) — absolute error of the predicted median.
- **50% / 90%** — empirical coverage of the nominal intervals. Closer to 50% / 90% is better; `·` marks a miss over 5 points, `⚠` over 10.


| Geyser | Model | n | CRPS (min) | MAE (min) | 50% cov | 90% cov |
|---|---|---:|---:|---:|---:|---:|
| Old Faithful | **minor_conditional** | 2,000 | 4.5 | 6.1 | 59.1% · | 93.8% |
| Old Faithful | duration_lognormal | 2,000 | 8.3 | 11.7 | 53.5% | 89.6% |
| Old Faithful | weibull | 2,000 | 8.9 | 12.3 | 52.7% | 90.2% |
| Old Faithful | best_parametric | 2,000 | 8.9 | 12.5 | 52.5% | 89.5% |
| Old Faithful | weibull_aft | 2,000 | 9.1 | 12.4 | 59.8% · | 92.0% |
| Old Faithful | rolling_normal | 2,000 | 9.1 | 12.8 | 50.7% | 88.6% |
| Old Faithful | lognormal | 2,000 | 9.1 | 13.0 | 52.8% | 89.7% |
| Old Faithful | adaptive_lognormal | 2,000 | 9.2 | 13.1 | 52.1% | 89.2% |
| Grand | **adaptive_lognormal** | 2,000 | 38.7 | 54.1 | 47.9% | 90.8% |
| Grand | rolling_normal | 2,000 | 39.3 | 54.7 | 49.5% | 91.0% |
| Grand | lognormal | 2,000 | 39.3 | 55.0 | 47.4% | 91.2% |
| Grand | best_parametric | 2,000 | 39.9 | 55.8 | 46.9% | 91.3% |
| Grand | weibull | 2,000 | 40.8 | 55.7 | 62.4% ⚠ | 92.9% |
| Grand | entry_conditional | 2,000 | 42.1 | 58.5 | 45.8% | 89.5% |
| Grand | weibull_aft | 2,000 | 47.1 | 63.3 | 55.2% · | 86.6% |
| Daisy | **adaptive_lognormal** | 2,000 | 3.1 | 4.3 | 51.9% | 87.4% |
| Daisy | rolling_normal | 2,000 | 3.2 | 4.3 | 50.3% | 86.8% |
| Daisy | lognormal | 2,000 | 3.2 | 4.4 | 53.4% | 89.1% |
| Daisy | best_parametric | 2,000 | 3.2 | 4.4 | 53.5% | 89.6% |
| Daisy | entry_conditional | 2,000 | 3.4 | 4.7 | 53.7% | 90.5% |
| Daisy | weibull | 2,000 | 3.5 | 4.4 | 68.2% ⚠ | 93.7% |
| Daisy | weibull_aft | 2,000 | 5.1 | 7.0 | 66.2% ⚠ | 95.7% · |
| Riverside | **adaptive_lognormal** | 1,585 | 12.8 | 17.5 | 53.3% | 91.2% |
| Riverside | best_parametric | 1,585 | 12.8 | 17.5 | 55.0% · | 91.2% |
| Riverside | lognormal | 1,585 | 12.9 | 17.8 | 57.0% · | 92.1% |
| Riverside | rolling_normal | 1,585 | 12.9 | 17.3 | 51.7% | 90.5% |
| Riverside | weibull | 1,585 | 14.2 | 17.4 | 54.4% | 92.0% |
| Riverside | weibull_aft | 1,585 | 18.5 | 22.8 | 77.1% ⚠ | 99.3% · |
| Castle | **minor_conditional** | 868 | 110.5 | 151.6 | 59.3% · | 88.0% |
| Castle | lognormal | 868 | 118.2 | 151.0 | 65.8% ⚠ | 87.2% |
| Castle | rolling_normal | 868 | 119.4 | 155.4 | 64.3% ⚠ | 85.8% |
| Castle | adaptive_lognormal | 868 | 119.7 | 153.5 | 65.4% ⚠ | 87.0% |
| Castle | entry_conditional | 868 | 120.0 | 150.4 | 69.5% ⚠ | 85.5% |
| Castle | best_parametric | 868 | 120.0 | 154.2 | 66.8% ⚠ | 86.6% |
| Castle | weibull | 868 | 120.9 | 156.5 | 67.6% ⚠ | 87.9% |
| Castle | weibull_aft | 868 | 124.6 | 155.4 | 62.1% ⚠ | 80.0% ⚠ |
| Great Fountain | **lognormal** | 408 | 45.6 | 62.9 | 55.1% · | 91.2% |
| Great Fountain | best_parametric | 408 | 45.7 | 63.0 | 55.9% · | 91.9% |
| Great Fountain | entry_conditional | 408 | 46.0 | 63.1 | 58.3% · | 93.4% |
| Great Fountain | adaptive_lognormal | 408 | 46.0 | 63.9 | 53.2% | 90.2% |
| Great Fountain | rolling_normal | 408 | 46.4 | 63.4 | 54.7% | 89.5% |
| Great Fountain | weibull | 408 | 48.6 | 63.9 | 71.6% ⚠ | 95.3% · |
| Great Fountain | weibull_aft | 408 | 48.8 | 63.8 | 74.0% ⚠ | 97.1% · |
| Beehive | **rolling_normal** | 1,180 | 121.0 | 167.5 | 51.4% | 87.3% |
| Beehive | adaptive_lognormal | 1,180 | 121.4 | 167.9 | 52.4% | 87.3% |
| Beehive | lognormal | 1,180 | 124.3 | 172.4 | 51.3% | 87.4% |
| Beehive | weibull | 1,180 | 126.1 | 175.3 | 56.4% · | 90.7% |
| Beehive | best_parametric | 1,180 | 126.8 | 177.4 | 50.9% | 88.4% |
| Beehive | weibull_aft | 1,180 | 170.0 | 249.1 | 48.5% | 94.4% |

**Bold** = best CRPS for that geyser.

## Which model wins

| Geyser | Best by CRPS | CRPS | Baseline CRPS | Improvement |
|---|---|---:|---:|---:|
| Old Faithful | minor_conditional | 4.5 | 9.1 | 50.6% |
| Grand | adaptive_lognormal | 38.7 | 39.3 | 1.4% |
| Daisy | adaptive_lognormal | 3.1 | 3.2 | 1.4% |
| Riverside | adaptive_lognormal | 12.8 | 12.9 | 1.3% |
| Castle | minor_conditional | 110.5 | 119.4 | 7.5% |
| Great Fountain | lognormal | 45.6 | 46.4 | 1.8% |
| Beehive | rolling_normal | 121.0 | 121.0 | 0.0% |

## Known gaps

- No geyser's best model misses nominal coverage by more than 10 points at the 50% level.


- **The covariate model did not earn its complexity.** `weibull_aft` (lifelines Weibull AFT with previous-interval, clock-time, seasonal and entry-flag covariates) ranks in the bottom half on 7 of 7 geysers: Old Faithful 5/8, Grand 7/7, Daisy 7/7, Riverside 6/6, Castle 8/8, Great Fountain 7/7, Beehive 6/6. The simple rolling lognormal/Weibull fits beat it nearly everywhere, and the dashboard-style baseline is competitive. Reported as-is.


### Honest coverage: scoring the intervals the filter throws away

Everything above is measured only on intervals that passed the validity filter, which quietly excludes exactly the cases the filter exists to remove — stretches where an eruption went unlogged. A gazer on the boardwalk gets no such exemption. The table below re-scores a plain rolling `lognormal` (trained only on valid history, as always) against **every** interval in the window, so the gap between the two numbers is the honest cost of observation gaps.


| Geyser | n (all) | % filter-rejected | 50% cov | 90% cov | 90% cov (filtered) |
|---|---:|---:|---:|---:|---:|
| Old Faithful | 1,500 | 15.0% | 47.1% | 76.1% | 89.7% |
| Grand | 1,500 | 17.9% | 38.7% | 76.1% | 91.2% |
| Daisy | 1,500 | 21.9% | 41.1% | 69.1% | 89.1% |
| Riverside | 1,500 | 36.5% | 36.7% | 59.0% | 92.1% |
| Castle | 1,250 | 30.6% | 45.7% | 60.6% | 87.2% |
| Great Fountain | 722 | 43.5% | 31.2% | 51.5% | 91.2% |
| Beehive | 1,285 | 8.2% | 47.1% | 80.2% | 87.4% |

The drop between the last two columns is the real-world penalty. Treat the headline table as an upper bound on field reliability, and see the renewal/missed-eruption handling in `predict` (README) for how the CLI compensates at prediction time.


## Neighbour-geyser conditioning (nowcast)

The interval harness above asks *how long is the gap after this eruption*. That cannot express what actually helps a gazer — *standing here now, with Beehive's Indicator running, when does it go?* — so these are scored from decision times on a fixed 30-minute grid, independent of when eruptions happen. Scoring only just-before-an-eruption would be conditioning on the answer.


Each decision time is scored **twice, identically**, with neighbour conditioning on and off. Only a paired delta is meaningful here: the conditioned moments are not a random sample of time.


| Geyser | Regime | n | CRPS off | CRPS on | Δ | 90% off | 90% on |
|---|---|---:|---:|---:|---:|---:|---:|
| Grand | **overall** | 21,656 | 44.5 | 44.5 | +0.2% | 90% | 90% |
| Grand | base | 18,241 | 43.8 | 43.8 | +0.0% | 90% | 90% |
| Grand | precursor_shifted | 1,688 | 53.6 | 54.4 | +1.6% | 87% | 86% |
| Grand | turban_gated | 1,727 | 42.3 | 42.4 | +0.2% | 92% | 92% |
| Beehive | **overall** | 31,008 | 118.2 | 114.8 | -2.9% | 85% | 89% |
| Beehive | base | 28,937 | 116.1 | 116.2 | +0.0% | 89% | 90% |
| Beehive | indicator_active | 2,071 | 147.4 | 95.2 | -35.4% | 29% | 87% |

**Beehive's Indicator works.** In the minutes it is running, CRPS falls by about a third and nominal 90% coverage goes from badly overconfident to roughly honest. The no-Indicator regime is untouched, which is the point — the conditioning adds information only when there is information to add. Residual error in that regime is dominated by cycles where the Beehive eruption itself was never logged (~6% of Indicator entries), not by the model.


**Grand's Turban lattice does not work, and is off by default.** Grand starts *with* a Turban — only 0.1% of starts fall 5-13 minutes after one, against 24% in the first two minutes — so gating the density onto that lattice looks obviously right. It isn't. Turban's own interval scatters (sd 4.2 min on a 19 min period) so extrapolated phase decoheres within about one cycle, and Grand's own uncertainty is ~100 min, five times the Turban period. The model's predicted median never drops below 40 minutes, so the lattice is never consulted at a range where it could discriminate. Rift and West Triplet shifts (+32 and +15 min, both highly significant under a length-bias-safe test) likewise fail to improve the distribution. Both are kept switchable so the negative result stays reproducible.


## Figures

![interval_histograms.png](figures/interval_histograms.png)

![calibration_reliability.png](figures/calibration_reliability.png)

![example_density_old_faithful.png](figures/example_density_old_faithful.png)

## Data-quality notes

- Of 1,340,410 consecutive-eruption gaps, 1,002,475 (74.8%) pass the per-geyser plausibility filter (0.35x-3x that geyser's median). The rest are overwhelmingly observation gaps — nobody is watching Riverside at 3am in February — not real eruptions.

- The ceiling is **1.75x** the median rather than the more obvious 3x because the interval histograms show clear **harmonics**: Riverside clusters at ~390, ~780 and ~1150 minutes, Great Fountain at ~686 and ~1400. Those secondary peaks sit at exactly 2x and 3x the median and are one and two missed eruptions. A 3x ceiling admits them, and models trained on the contaminated series predict distributions far wider than reality — it was worth several times more CRPS than any modeling choice in this report.

Observation-entry mix since 2015 (% of valid intervals):

| Geyser | webcam | electronic | approximate | in-eruption |
|---|---:|---:|---:|---:|
| Beehive | 48.2% | 1.2% | 1.5% | 2.7% |
| Castle | 49.0% | 19.5% | 1.2% | 11.4% |
| Daisy | 58.2% | 24.2% | 0.6% | 6.9% |
| Grand | 45.8% | 15.5% | 1.2% | 5.2% |
| Great Fountain | 0.0% | 62.5% | 0.8% | 2.6% |
| Old Faithful | 91.1% | 3.8% | 0.3% | 3.3% |
| Riverside | 62.0% | 1.1% | 1.2% | 21.9% |

Data courtesy of [GeyserTimes.org](https://geysertimes.org) and its community of volunteer observers.
