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

Phase 0 (ingestion), Phase 1 (prediction engine + backtest) and Phase 2 (API,
dashboard, MCP server) are complete. See
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

# Top up the archive with entries logged since the snapshot (REST API v5)
uv run geyser-ai sync

# Serve the JSON API + mobile dashboard at http://127.0.0.1:8000/
uv run geyser-ai serve
uv run geyser-ai serve --host 0.0.0.0 --port 8137

# Run the MCP server over stdio
uv run geyser-ai mcp
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
| `adaptive_lognormal` | Changepoint detection + held-out selection of the window length |
| `weibull_aft` | lifelines Weibull AFT with covariates, refit periodically |
| `duration_lognormal` | Old Faithful only: short/long preceding-duration split |
| `minor_conditional` | Castle & Old Faithful: conditions on whether the previous eruption was a *minor* |
| `entry_conditional` | Logger-heavy geysers: conditions on whether the anchor came from an electronic logger |

**Backtest.** Walk-forward over the last 3 years. At each evaluated eruption a
model sees only intervals strictly earlier than the one it is predicting. All
models are scored on the same set of target eruptions, so none benefits from
silently skipping hard cases. Metrics: CRPS, MAE of the predicted median, and
empirical coverage of the nominal 50% and 90% intervals.

## Results

Full table and figures: [`reports/calibration_report.md`](reports/calibration_report.md).

Best model per geyser, walk-forward over the last 3 years (CRPS in minutes, lower
is better):

| Geyser | Best model | CRPS | MAE | 50% cov | 90% cov |
|---|---|---:|---:|---:|---:|
| Old Faithful | `minor_conditional` | 4.5 | 6.1 | 59% | 94% |
| Grand | `adaptive_lognormal` | 38.7 | 54.1 | 48% | 91% |
| Daisy | `adaptive_lognormal` | 3.1 | 4.3 | 52% | 87% |
| Riverside | `adaptive_lognormal` | 12.8 | 17.5 | 53% | 91% |
| Castle | `minor_conditional` | 110.5 | 151.6 | 59% | 88% |
| Great Fountain | `lognormal` | 45.6 | 62.9 | 55% | 91% |
| Beehive | `rolling_normal` | 121.0 | 167.6 | 51% | 87% |

### What actually moved the numbers

**Data cleaning beat modeling, repeatedly.** Three successive fixes to the interval
validity filter each produced larger gains than any model ever did.

*1. Harmonics.* The first version used a 3x-median ceiling. Interval histograms
showed unmistakable secondary peaks at exactly 2x and 3x the median — one and two
*missed* eruptions, not real intervals. Tightening to 1.75x cut best-model CRPS by
20-87% per geyser (Riverside 106.6 -> 14.3).

*2. Drift.* A single median across the whole 1871-present record is wrong because
intervals drift: Daisy ran a 142-minute median in 2019 and 111 in 2026, so doubles
of the *modern* interval slid under a ceiling set by the *old* one. Daisy's nominal
50% interval was covering 87%. Making the median local fixed it (CRPS 12.4 -> 3.2,
coverage 87% -> 50%).

*3. Self-validating contamination.* A plain local median then broke Great Fountain
(CRPS 47.8 -> 145). Where observation is poor, missed eruptions are the *majority*
of recorded gaps, so the local median tracks the doubled value and legitimises the
contamination — Great Fountain's ran up to 1361 against a true interval near 690.
The fix exploits an asymmetry: a missed eruption only ever *adds* time, so a low
quantile is robust where the median is not. A local 25th percentile anchors the
true mode, then the median is recomputed over only the gaps near that anchor.
Post-filter p95/median (≈1.2-1.5 is clean unimodal, ≈2 means harmonics survived):
Great Fountain 2.01 -> 1.28, Beehive 1.59 -> 1.52, Grand 1.46 -> 1.40.

**The minor-eruption flag is the single best covariate found.** For Old Faithful the
post-minor and post-major interval distributions are almost disjoint — median 70 min
(p95 83) versus median 102 min (p05 91) — and 22% of recent eruptions are minors.
Conditioning on it roughly halves CRPS (8.3 -> 4.5) and beats the classic
duration-based split, because the observer-set flag is cleaner than raw duration
data, which is often missing. For Castle the gain is in *variance* rather than
location (post-minor sd 387 vs 164), and `minor_conditional` is its best model.
Both are legitimate: the flag is recorded when the eruption is logged, so it is
known at prediction time.

**The lifelines covariate model still does not earn its complexity.** `weibull_aft`
remains in the bottom half on essentially every geyser. Its early apparent win on
Riverside (CRPS 106.6 vs 120.8) was an artifact of dirty data — it was using the
previous interval to spot missed-eruption doubles. With the harmonics gone the
signal vanished. The dashboard-style baseline is competitive throughout and wins
outright on Beehive.

**Electronic loggers: a negative result, and a data-quality one.** Great Fountain is
~60% logger-recorded. Comparing entry-type transitions (2015+):

| transition | n | median | sd |
|---|---:|---:|---:|
| logger -> logger | 2051 | 669 | 95 |
| logger -> human | 627 | 665 | 88 |
| human -> logger | 626 | 686 | 91 |
| human -> human | 1139 | 708 | 276 |

This is **not** a timestamp offset — an offset would push the two mixed pairs apart
symmetrically while leaving like-to-like alone, which is not the pattern. It is a
data-quality difference: a logger catches every eruption, so consecutive logger
entries are true intervals, whereas human-only stretches still contain missed ones
(mean 813 vs median 708 is a heavy right tail). The implied model,
`entry_conditional`, **does not help** (Great Fountain 46.0 vs 45.6 for plain
lognormal). It is kept in the roster and reported rather than quietly dropped.

### Honest coverage

Everything above is scored only on intervals that passed the validity filter — which
excludes exactly the cases the filter exists to remove. A gazer on the boardwalk gets
no such exemption. Re-scoring a plain rolling `lognormal` against **every** interval:

| Geyser | % filter-rejected | 50% cov | 90% cov | 90% cov (filtered) |
|---|---:|---:|---:|---:|
| Old Faithful | 15.0% | 47% | 76% | 90% |
| Grand | 17.9% | 39% | 76% | 91% |
| Daisy | 21.9% | 41% | 69% | 89% |
| Riverside | 36.5% | 37% | 59% | 92% |
| Castle | 30.6% | 46% | 61% | 87% |
| Great Fountain | 43.5% | 31% | 52% | 91% |
| Beehive | 8.2% | 47% | 80% | 87% |

The gap between the last two columns is the real-world cost of observation gaps.
Treat the headline table as an upper bound on field reliability.

### Missed eruptions at prediction time

The naive forecast conditions on survival — *it hasn't erupted, so it's overdue* —
which is only sound if we would certainly have seen it. In crowdsourced data that
fails constantly: a silent 14 hours at Riverside usually means nobody was looking.

`predict` therefore treats the geyser as a **renewal process** from the last *logged*
eruption, with each eruption logged independently with probability `p_obs`. A path on
which k eruptions fell inside the silent window is consistent with the evidence only
if all k went unlogged, so it carries weight `(1 - p_obs)^k`. Weighting simulated
paths that way interpolates between the regimes automatically:

- **fresh data** (age << typical interval) — almost no path has a missed eruption, so
  this reduces to ordinary survival conditioning;
- **stale data** (age >> typical interval) — survival paths become astronomically
  unlikely, weight shifts onto the k-missed hypotheses, and the forecast correctly
  becomes *"it already went; the next one is roughly one interval from whenever
  that was"*.

`p_obs` is estimated from the data rather than tuned: it is the recent share of gaps
that came through the validity filter as single intervals. The CLI reports the
expected number of missed eruptions and flags stale rows. On a 17-hour-old snapshot
Old Faithful reports ~9.4 expected missed eruptions and predicts the next one just
after now, instead of claiming it is 17 hours overdue.

### Caveat on before/after comparisons

Each filter change alters *which* intervals are valid, so CRPS is not strictly
comparable across versions — the evaluation set moves with it. Coverage is the more
meaningful cross-version signal, and calibration improved on every geyser. Castle's
CRPS rose (104 -> 110) purely because its evaluation set became harder after the
two-stage filter removed contaminated easy cases; its coverage improved (50%: 70% ->
59%, 90%: 83% -> 88%).

## Serving predictions

`uv run geyser-ai serve` starts a FastAPI app with the JSON API and a
single-page dashboard. Interactive API docs are at `/docs`.

| Endpoint | What it returns |
|---|---|
| `GET /` | The dashboard |
| `GET /api/predictions?hours=12&points=96` | All seven geysers, sorted soonest first: median, 50%/90% windows, expected missed eruptions, data age, and a probability-density curve for charting |
| `GET /api/predictions/{geyser}?points=240` | One geyser, denser curve |
| `GET /api/eruptions/recent?hours=24` | Recently logged eruptions (`geyser=` and `targets_only=` filters) |
| `GET /api/stats?geyser=Grand` | Interval statistics per geyser |
| `GET /api/health` | Snapshot age, row counts, sync state |

**Freshness.** The archive snapshot is downloaded once and never re-fetched
automatically. `sync.py` tops it up from the documented
`entries_recent/{minutes}` endpoint, sizing the lookback from the gap since the
newest row we already hold so a single request bridges it. Responses are cached
with a five-minute TTL — GeyserTimes' policy calls polling the same URL more
than once a minute abusive — and a plain identifying User-Agent is sent rather
than a spoofed browser one. Network failures set an `error` field instead of
raising: a stale prediction with an honest age beats a broken page. In practice
this takes the data age from ~17 hours (archive only) to a few minutes.

### Dashboard

Mobile-first, no build step, served from a single self-contained HTML file.
The design is deliberate rather than templated:

- **The signature is the probability ribbon.** Every geyser is drawn on *one
  shared 12-hour axis*, so the page reads top-to-bottom as a single instrument
  and you can see which eruption lands first. The width of the smear **is** the
  confidence: Daisy is a needle, Beehive a broad wash. Honest uncertainty is the
  visually dominant object on the page, not a caveat in small print.
- The 50%/90% bands are **clipped to the density curve**, so they shade actual
  probability mass rather than sitting over it as opaque blocks.
- Palette comes from the subject — deep hot-spring teal as the single sequential
  hue, thermophile orange reserved solely for the `NOW` marker, warm sinter
  neutrals. Light and dark are both selected, not an automatic inversion.
- Monospace for every time and countdown: tabular figures read like the data
  loggers this subject actually uses, and they stop the layout jittering on the
  five-minute auto-refresh.
- The community's `wc` / `ie` / `E` shorthand survives as chips — a nod to the
  classic chat.geysertimes.org dashboard that gazers already know.

### MCP server

`uv run geyser-ai mcp` speaks stdio and exposes three tools — `get_predictions`,
`get_recent_eruptions`, `get_geyser_stats` — as thin wrappers over the same
`service.py` functions the HTTP API calls, so the two transports cannot drift
apart. Register it with any MCP client:

```json
{
  "mcpServers": {
    "geyser-ai": {
      "command": "uv",
      "args": ["run", "geyser-ai", "mcp"],
      "cwd": "/path/to/geyser-ai"
    }
  }
}
```

### Architecture

```
ingest.py ──> DuckDB ──┐
sync.py   ──>          │
                       ├─> service.py ─┬─> api.py         (HTTP + dashboard)
models.py ─> predict.py┘               └─> mcp_server.py  (stdio)
           backtest.py ─> report.py    (offline evaluation)
```

`service.py` is the single read layer; `api.py` and `mcp_server.py` are
transports over it and hold no logic of their own.

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
