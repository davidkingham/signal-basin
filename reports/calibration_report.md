# Calibration report

Walk-forward backtest, last 3 years. Generated 2026-08-03 from the GeyserTimes complete archive.

At every evaluated eruption each model sees **only** intervals strictly earlier than the one it is predicting. All models are scored on the same set of target eruptions, so no model benefits from skipping hard cases.

## Metrics

- **CRPS** (minutes, lower is better) — proper scoring rule over the whole predicted distribution.
- **MAE** (minutes) — absolute error of the predicted median.
- **50% / 90%** — empirical coverage of the nominal intervals. Closer to 50% / 90% is better; `·` marks a miss over 5 points, `⚠` over 10.


| Geyser | Model | n | CRPS (min) | MAE (min) | 50% cov | 90% cov |
|---|---|---:|---:|---:|---:|---:|
| Old Faithful | **duration_lognormal** | 2,000 | 10.1 | 13.2 | 64.0% ⚠ | 90.2% |
| Old Faithful | best_parametric | 2,000 | 10.9 | 14.2 | 63.7% ⚠ | 90.5% |
| Old Faithful | lognormal | 2,000 | 10.9 | 14.3 | 63.3% ⚠ | 90.5% |
| Old Faithful | rolling_normal | 2,000 | 11.2 | 14.2 | 62.5% ⚠ | 91.8% |
| Old Faithful | weibull | 2,000 | 11.4 | 13.9 | 70.5% ⚠ | 94.5% |
| Old Faithful | weibull_aft | 2,000 | 12.4 | 15.4 | 72.2% ⚠ | 96.5% · |
| Grand | **lognormal** | 2,000 | 105.3 | 143.3 | 53.0% | 90.0% |
| Grand | best_parametric | 2,000 | 106.2 | 144.0 | 52.8% | 90.2% |
| Grand | rolling_normal | 2,000 | 109.6 | 153.6 | 57.2% · | 89.8% |
| Grand | weibull_aft | 2,000 | 109.6 | 146.4 | 61.1% ⚠ | 88.1% |
| Grand | weibull | 2,000 | 110.7 | 150.0 | 71.6% ⚠ | 92.0% |
| Daisy | **lognormal** | 2,000 | 22.2 | 26.6 | 84.5% ⚠ | 89.5% |
| Daisy | best_parametric | 2,000 | 22.3 | 26.6 | 84.4% ⚠ | 89.5% |
| Daisy | rolling_normal | 2,000 | 25.0 | 31.1 | 85.4% ⚠ | 91.2% |
| Daisy | weibull | 2,000 | 25.4 | 28.0 | 88.2% ⚠ | 92.8% |
| Daisy | weibull_aft | 2,000 | 25.6 | 28.7 | 87.3% ⚠ | 92.8% |
| Riverside | **weibull_aft** | 2,000 | 106.6 | 146.8 | 64.8% ⚠ | 91.8% |
| Riverside | best_parametric | 2,000 | 120.8 | 174.3 | 53.4% | 90.8% |
| Riverside | lognormal | 2,000 | 121.3 | 174.4 | 51.7% | 89.5% |
| Riverside | weibull | 2,000 | 125.3 | 185.3 | 63.9% ⚠ | 91.7% |
| Riverside | rolling_normal | 2,000 | 128.8 | 190.1 | 52.2% | 88.0% |
| Castle | **weibull_aft** | 1,138 | 245.2 | 324.2 | 54.0% | 78.7% ⚠ |
| Castle | weibull | 1,138 | 246.3 | 319.9 | 62.7% ⚠ | 82.9% · |
| Castle | best_parametric | 1,138 | 246.9 | 320.2 | 62.7% ⚠ | 82.8% · |
| Castle | lognormal | 1,138 | 247.9 | 315.7 | 62.5% ⚠ | 86.9% |
| Castle | rolling_normal | 1,138 | 248.0 | 330.3 | 60.3% ⚠ | 84.7% · |
| Great Fountain | **rolling_normal** | 653 | 194.7 | 286.6 | 43.2% · | 89.7% |
| Great Fountain | weibull_aft | 653 | 208.2 | 289.7 | 44.0% · | 76.3% ⚠ |
| Great Fountain | lognormal | 653 | 213.6 | 320.5 | 30.5% ⚠ | 90.5% |
| Great Fountain | weibull | 653 | 216.5 | 328.2 | 38.9% ⚠ | 92.3% |
| Great Fountain | best_parametric | 653 | 219.3 | 329.9 | 28.2% ⚠ | 91.1% |
| Beehive | **lognormal** | 1,269 | 178.8 | 242.0 | 54.2% | 88.7% |
| Beehive | rolling_normal | 1,269 | 179.4 | 244.2 | 57.0% · | 89.3% |
| Beehive | best_parametric | 1,269 | 182.1 | 245.9 | 56.7% · | 90.1% |
| Beehive | weibull | 1,269 | 185.9 | 248.2 | 67.0% ⚠ | 93.5% |
| Beehive | weibull_aft | 1,269 | 240.6 | 339.1 | 57.4% · | 95.0% · |

**Bold** = best CRPS for that geyser.

## Which model wins

| Geyser | Best by CRPS | CRPS | Baseline CRPS | Improvement |
|---|---|---:|---:|---:|
| Old Faithful | duration_lognormal | 10.1 | 11.2 | 9.5% |
| Grand | lognormal | 105.3 | 109.6 | 3.9% |
| Daisy | lognormal | 22.2 | 25.0 | 10.9% |
| Riverside | weibull_aft | 106.6 | 128.8 | 17.3% |
| Castle | weibull_aft | 245.2 | 248.0 | 1.1% |
| Great Fountain | rolling_normal | 194.7 | 194.7 | 0.0% |
| Beehive | lognormal | 178.8 | 179.4 | 0.3% |

## Figures

![interval_histograms.png](figures/interval_histograms.png)

![calibration_reliability.png](figures/calibration_reliability.png)

![example_density_old_faithful.png](figures/example_density_old_faithful.png)

## Data-quality notes

- Of 1,340,410 consecutive-eruption gaps, 1,104,855 (82.4%) pass the per-geyser plausibility filter (0.35x-3x that geyser's median). The rest are overwhelmingly observation gaps — nobody is watching Riverside at 3am in February — not real eruptions.

Observation-entry mix since 2015 (% of valid intervals):

| Geyser | webcam | electronic | approximate | in-eruption |
|---|---:|---:|---:|---:|
| Beehive | 49.4% | 1.2% | 1.5% | 3.0% |
| Castle | 49.1% | 19.4% | 1.1% | 12.2% |
| Daisy | 59.3% | 23.0% | 0.6% | 7.4% |
| Grand | 47.0% | 14.8% | 1.5% | 5.8% |
| Great Fountain | 0.0% | 56.8% | 1.0% | 3.9% |
| Old Faithful | 91.0% | 3.8% | 0.3% | 3.5% |
| Riverside | 62.1% | 0.9% | 1.5% | 22.9% |

Data courtesy of [GeyserTimes.org](https://geysertimes.org) and its community of volunteer observers.
