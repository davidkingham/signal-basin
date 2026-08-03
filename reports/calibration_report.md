# Calibration report

Walk-forward backtest, last 3 years. Generated 2026-08-03 from the GeyserTimes complete archive.

At every evaluated eruption each model sees **only** intervals strictly earlier than the one it is predicting. All models are scored on the same set of target eruptions, so no model benefits from skipping hard cases.

## Metrics

- **CRPS** (minutes, lower is better) — proper scoring rule over the whole predicted distribution.
- **MAE** (minutes) — absolute error of the predicted median.
- **50% / 90%** — empirical coverage of the nominal intervals. Closer to 50% / 90% is better; `·` marks a miss over 5 points, `⚠` over 10.


| Geyser | Model | n | CRPS (min) | MAE (min) | 50% cov | 90% cov |
|---|---|---:|---:|---:|---:|---:|
| Old Faithful | **duration_lognormal** | 2,000 | 8.1 | 11.5 | 53.1% | 89.2% |
| Old Faithful | weibull | 2,000 | 8.6 | 11.9 | 50.4% | 87.3% |
| Old Faithful | best_parametric | 2,000 | 8.6 | 11.9 | 50.1% | 86.5% |
| Old Faithful | weibull_aft | 2,000 | 8.6 | 11.8 | 52.5% | 84.5% · |
| Old Faithful | rolling_normal | 2,000 | 8.8 | 12.4 | 50.6% | 88.8% |
| Old Faithful | lognormal | 2,000 | 8.8 | 12.6 | 53.0% | 89.1% |
| Grand | **lognormal** | 2,000 | 41.8 | 57.6 | 51.9% | 91.5% |
| Grand | best_parametric | 2,000 | 42.3 | 58.3 | 50.3% | 92.0% |
| Grand | rolling_normal | 2,000 | 42.6 | 58.1 | 54.6% | 91.4% |
| Grand | weibull | 2,000 | 44.3 | 58.2 | 69.8% ⚠ | 93.5% |
| Grand | weibull_aft | 2,000 | 49.2 | 65.0 | 67.3% ⚠ | 91.6% |
| Daisy | **lognormal** | 2,000 | 12.9 | 14.8 | 86.5% ⚠ | 92.2% |
| Daisy | best_parametric | 2,000 | 13.0 | 14.8 | 86.5% ⚠ | 92.3% |
| Daisy | rolling_normal | 2,000 | 14.2 | 16.9 | 85.7% ⚠ | 91.4% |
| Daisy | weibull | 2,000 | 15.5 | 15.8 | 91.9% ⚠ | 92.4% |
| Daisy | weibull_aft | 2,000 | 16.1 | 18.4 | 90.8% ⚠ | 92.3% |
| Riverside | **best_parametric** | 1,592 | 14.3 | 18.3 | 54.4% | 91.1% |
| Riverside | lognormal | 1,592 | 14.4 | 18.8 | 56.0% · | 92.7% |
| Riverside | rolling_normal | 1,592 | 14.4 | 19.1 | 52.4% | 90.3% |
| Riverside | weibull | 1,592 | 15.7 | 18.2 | 53.1% | 91.6% |
| Riverside | weibull_aft | 1,592 | 21.4 | 25.0 | 81.7% ⚠ | 99.1% · |
| Castle | **weibull** | 819 | 104.1 | 125.9 | 69.8% ⚠ | 83.3% · |
| Castle | best_parametric | 819 | 104.6 | 128.0 | 69.6% ⚠ | 83.4% · |
| Castle | rolling_normal | 819 | 105.8 | 130.7 | 70.3% ⚠ | 85.5% |
| Castle | weibull_aft | 819 | 108.6 | 138.0 | 59.8% · | 81.0% · |
| Castle | lognormal | 819 | 110.1 | 133.5 | 72.5% ⚠ | 89.6% |
| Great Fountain | **lognormal** | 410 | 47.8 | 65.5 | 56.6% · | 92.0% |
| Great Fountain | best_parametric | 410 | 47.9 | 65.5 | 56.6% · | 92.9% |
| Great Fountain | rolling_normal | 410 | 49.3 | 66.4 | 55.9% · | 89.0% |
| Great Fountain | weibull_aft | 410 | 50.4 | 65.3 | 74.1% ⚠ | 96.6% · |
| Great Fountain | weibull | 410 | 51.5 | 66.4 | 73.4% ⚠ | 95.4% · |
| Beehive | **rolling_normal** | 1,200 | 131.1 | 181.6 | 52.6% | 88.1% |
| Beehive | lognormal | 1,200 | 132.7 | 184.3 | 50.7% | 87.5% |
| Beehive | best_parametric | 1,200 | 135.1 | 188.0 | 51.5% | 88.8% |
| Beehive | weibull | 1,200 | 135.5 | 187.9 | 57.6% · | 91.7% |
| Beehive | weibull_aft | 1,200 | 158.4 | 230.2 | 45.1% | 91.0% |

**Bold** = best CRPS for that geyser.

## Which model wins

| Geyser | Best by CRPS | CRPS | Baseline CRPS | Improvement |
|---|---|---:|---:|---:|
| Old Faithful | duration_lognormal | 8.1 | 8.8 | 8.4% |
| Grand | lognormal | 41.8 | 42.6 | 1.8% |
| Daisy | lognormal | 12.9 | 14.2 | 8.9% |
| Riverside | best_parametric | 14.3 | 14.4 | 0.8% |
| Castle | weibull | 104.1 | 105.8 | 1.6% |
| Great Fountain | lognormal | 47.8 | 49.3 | 3.0% |
| Beehive | rolling_normal | 131.1 | 131.1 | 0.0% |

## Known gaps

- **Daisy** — the best model (`lognormal`) is far too wide: its nominal 50% interval actually covers 86%. The predicted distribution is the wrong *shape*, not just the wrong width.
- **Castle** — the best model (`weibull`) is far too wide: its nominal 50% interval actually covers 70%. The predicted distribution is the wrong *shape*, not just the wrong width.

- **The covariate model did not earn its complexity.** `weibull_aft` (lifelines Weibull AFT with previous-interval, clock-time, seasonal and entry-flag covariates) ranks in the bottom half on 7 of 7 geysers: Old Faithful 4/6, Grand 5/5, Daisy 5/5, Riverside 5/5, Castle 4/5, Great Fountain 4/5, Beehive 5/5. The simple rolling lognormal/Weibull fits beat it nearly everywhere, and the dashboard-style baseline is competitive. Reported as-is.


- **Coverage here is measured only on intervals that passed the validity filter.** In the field a model also has to survive the case where an eruption was simply never logged, which these numbers do not capture. Treat them as an upper bound on real-world reliability.


## Figures

![interval_histograms.png](figures/interval_histograms.png)

![calibration_reliability.png](figures/calibration_reliability.png)

![example_density_old_faithful.png](figures/example_density_old_faithful.png)

## Data-quality notes

- Of 1,340,410 consecutive-eruption gaps, 984,095 (73.4%) pass the per-geyser plausibility filter (0.35x-3x that geyser's median). The rest are overwhelmingly observation gaps — nobody is watching Riverside at 3am in February — not real eruptions.

- The ceiling is **1.75x** the median rather than the more obvious 3x because the interval histograms show clear **harmonics**: Riverside clusters at ~390, ~780 and ~1150 minutes, Great Fountain at ~686 and ~1400. Those secondary peaks sit at exactly 2x and 3x the median and are one and two missed eruptions. A 3x ceiling admits them, and models trained on the contaminated series predict distributions far wider than reality — it was worth several times more CRPS than any modeling choice in this report.

Observation-entry mix since 2015 (% of valid intervals):

| Geyser | webcam | electronic | approximate | in-eruption |
|---|---:|---:|---:|---:|
| Beehive | 48.3% | 1.2% | 1.5% | 2.7% |
| Castle | 49.0% | 19.8% | 1.2% | 11.1% |
| Daisy | 58.7% | 23.5% | 0.6% | 7.2% |
| Grand | 46.3% | 15.4% | 1.2% | 5.4% |
| Great Fountain | 0.0% | 62.4% | 0.8% | 2.6% |
| Old Faithful | 91.1% | 3.8% | 0.3% | 3.3% |
| Riverside | 62.1% | 1.1% | 1.2% | 21.9% |

Data courtesy of [GeyserTimes.org](https://geysertimes.org) and its community of volunteer observers.
