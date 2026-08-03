# Geyser AI

An open-source analysis layer on top of the [GeyserTimes](https://geysertimes.org)
database. GeyserTimes and its community of volunteer gazers have built the most
complete geyser eruption record anywhere; this project adds probabilistic
next-eruption prediction on top of it — full probability distributions rather
than a point estimate with a fixed window, backtested honestly with published
calibration scores so anyone can check whether the models actually beat existing
methods. It is gazers-first and non-commercial, and the intent is to build this
*with* GeyserTimes as an official companion project rather than merely on top of
their public data.

## Status

Phase 0 (ingestion) and Phase 1 (prediction engine + backtest) are complete. See
[`reports/calibration_report.md`](reports/calibration_report.md) for the full
metrics table and calibration plots.

## Setup

Requires [uv](https://docs.astral.sh/uv/).

```bash
git clone <repo> && cd geyser-ai
uv sync
```

## CLI

```bash
# Download one GeyserTimes archive snapshot and build data/geysertimes.duckdb
uv run geyser-ai ingest

# Walk-forward backtest -> reports/calibration_report.md + reports/figures/
uv run geyser-ai backtest
uv run geyser-ai backtest --geyser "Old Faithful" --years 5

# Next-eruption prediction from the latest data in the DB (table + JSON)
uv run geyser-ai predict
uv run geyser-ai predict --geyser Grand --json
```

## How it works

**Ingestion.** One complete-archive snapshot
(`geysertimes_eruptions_complete_<date>.tsv.gz`, ~27 MB, ~1.53 M eruption
entries) is downloaded into `data/raw/` and cached; it is never re-downloaded
unless you pass `--force`. The raw TSV is mirrored verbatim into
`eruptions_raw`, and a cleaned `eruptions` view plus an `intervals` table are
built on top of it.

**Interval validity.** This is crowdsourced data with real observation gaps —
nobody is watching Riverside at 3 a.m. in February — so a raw gap between
consecutive entries is often several eruption cycles rather than one. An
interval is marked `is_valid` only when it falls within **0.35×–3× that geyser's
own median**, computed per geyser. The upper bound drops missed eruptions, the
lower bound drops duplicate entries. About 82% of intervals survive, and the raw
value is retained so the thresholds can be revisited.

**Observation flags** (`webcam`, `electronic`, `approximate`, `in_eruption`,
`near_start`, …) are preserved as columns so models can use or exclude them.
Community-flagged `questionable` entries are excluded from the `eruptions` view.

**Models.** Every model returns a full distribution over the next interval:

| Model | What it is |
|---|---|
| `rolling_normal` | Dashboard-style rolling mean ± window, read as a normal |
| `lognormal` / `weibull` | Rolling-window MLE fits |
| `best_parametric` | Picks lognormal vs Weibull per prediction by held-out likelihood |
| `weibull_aft` | lifelines Weibull AFT with covariates, refit periodically |
| `duration_lognormal` | Old Faithful only: short/long preceding-duration split |

**Backtest.** Walk-forward over the last 3 years. At each evaluated eruption a
model sees only intervals strictly earlier than the one it is predicting. All
models are scored on the same set of target eruptions, so none benefits from
silently skipping hard cases. Metrics: CRPS, MAE of the predicted median, and
empirical coverage of the nominal 50% and 90% intervals.

## Results

Full table and figures: [`reports/calibration_report.md`](reports/calibration_report.md).

Best CRPS per geyser (minutes; lower is better), walk-forward over the last 3 years:

| Geyser | Best model | CRPS | MAE | 50% cov | 90% cov | vs. baseline |
|---|---|---:|---:|---:|---:|---:|
| Old Faithful | `duration_lognormal` | 8.1 | 11.5 | 53% | 89% | −8.4% |
| Grand | `lognormal` | 41.8 | 57.6 | 52% | 92% | −1.9% |
| Daisy | `lognormal` | 12.9 | 14.9 | 87% | 92% | −8.9% |
| Riverside | `best_parametric` | 14.3 | 18.3 | 54% | 91% | −0.8% |
| Castle | `weibull` | 104.1 | 125.9 | 70% | 83% | −1.6% |
| Great Fountain | `lognormal` | 47.8 | 65.5 | 57% | 92% | −3.0% |
| Beehive | `rolling_normal` | 131.1 | 181.6 | 53% | 88% | 0.0% |

Three findings worth stating plainly:

**1. Data cleaning mattered far more than modeling.** The first run used a 3×-median
ceiling for interval validity. The interval histograms then showed unmistakable
harmonics — Riverside clusters at ~390, ~780 and ~1150 minutes, Great Fountain at
~686 and ~1400 — which are one and two *missed* eruptions, not real intervals.
Tightening the ceiling to 1.75× cut CRPS by far more than any model ever did:

| Geyser | Best CRPS @ 3× | Best CRPS @ 1.75× | Change |
|---|---:|---:|---:|
| Old Faithful | 10.1 | 8.1 | −20% |
| Grand | 105.3 | 41.8 | −60% |
| Daisy | 22.3 | 12.9 | −42% |
| Riverside | 106.6 | 14.3 | **−87%** |
| Castle | 245.2 | 104.1 | −58% |
| Great Fountain | 194.7 | 47.8 | −75% |
| Beehive | 178.8 | 131.1 | −27% |

**2. The lifelines covariate model did not earn its complexity — reported honestly.**
`weibull_aft` ranks in the *bottom half* on all seven geysers. On dirty data it
looked like the clear winner on Riverside (CRPS 106.6 vs 120.8 for the next model);
that entire advantage was an artifact — it was using the previous interval to
detect "the last gap was a double, so this one might be too." Once the harmonics
were removed the signal vanished and the simple rolling lognormal/Weibull fits beat
it nearly everywhere. The dashboard-style baseline is competitive too, and actually
*wins* on Beehive.

**3. The one covariate that genuinely helps is Old Faithful's eruption duration.**
`duration_lognormal`, which splits on whether the preceding eruption was shorter or
longer than ~2.5 minutes, is the best model for Old Faithful (CRPS 8.06 vs 8.56 for
the next best). The classic short/long duration → interval relationship still holds
in current data.

**Remaining calibration gap:** Daisy's nominal 50% interval covers 87% of actuals.
Daisy is extremely regular, and a rolling window wide enough to fit stably spans
level shifts, so the fitted marginal is much wider than the local conditional. A
shorter window or a changepoint-aware model is the obvious next step. Castle is also
off (70% at nominal 50%), most likely because its major/minor eruption distinction
is not modeled — 23% of Castle entries are flagged `minor`.

## Data attribution and gentle use

All eruption data comes from **[GeyserTimes.org](https://geysertimes.org)** and
the volunteer observers who record every entry. GeyserTimes is run by a small
nonprofit on modest infrastructure, so this project is deliberately gentle with
it:

- **One** archive download per snapshot, cached locally and never re-fetched
  unless you explicitly pass `--force`.
- The archive route is strongly preferred over the REST API; the only API call is
  a single fetch of the ~481-row geysers reference table.
- No crawling, no bulk API pagination, no scheduled polling.
- GeyserTimes runs [Anubis](https://github.com/TecharoHQ/anubis) to keep AI
  scrapers off the site. This client sends a plain identifying User-Agent rather
  than impersonating a browser, which is both more honest and what actually gets
  served.

If you fork this, please keep that posture, and consider supporting GeyserTimes
directly.

## License

MIT.
