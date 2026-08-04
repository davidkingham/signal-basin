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
dashboard, MCP server) are complete, and the dashboard is deployed at
**[geyser-ai.david-016.workers.dev](https://geyser-ai.david-016.workers.dev)**
(see [Deployment](#deployment)). See
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
interval is marked `is_valid` only when it falls within **0.5×–1.75× a local,
regime-specific baseline** for that geyser. The upper bound drops missed
eruptions, the lower bound drops duplicate entries. About 75% of intervals
survive, and the raw value is retained so the thresholds can be revisited. The
four things that baseline has to survive are in
[What actually moved the numbers](#what-actually-moved-the-numbers).

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
| *nowcast + Indicator* | Beehive: switches to a ~12-minute countdown once Beehive's Indicator starts |

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
| Old Faithful | `minor_conditional` | 4.5 | 6.1 | 59% | 93% |
| Grand | `adaptive_lognormal` | 38.8 | 54.2 | 48% | 91% |
| Daisy | `adaptive_lognormal` | 3.1 | 4.3 | 52% | 89% |
| Riverside | `adaptive_lognormal` | 12.8 | 17.5 | 53% | 91% |
| Castle | `minor_conditional` | 77.2 | 101.2 | 61% | 87% |
| Great Fountain | `lognormal` | 45.6 | 62.9 | 55% | 91% |
| Beehive | `rolling_normal` | 120.4 | 166.9 | 52% | 87% |

**Each geyser is served by the model in that table**, not by a single global
default — see `models.BEST_MODEL_BY_GEYSER`. That distinction is only load-bearing
for the two geysers with a minor mode, where conditioning on it roughly halves
CRPS (Old Faithful 8.8 → 4.5, Castle 172.5 → 77.2 against `best_parametric`).
On the other five the winner beats `best_parametric` by 0.2–4.8%, which is inside
the noise, so they keep the default rather than pinning a choice on a coin flip.

### What actually moved the numbers

**Data cleaning beat modeling, repeatedly.** Four successive fixes to the interval
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

*4. Two processes, one baseline.* Every fix above assumes a geyser has **one**
interval distribution to be local about. Castle does not. An eruption that fails
to reach the steam phase is logged as a **minor**, and the interval that follows
it is a physically different, much shorter process. Pooled, the baseline tracked
the ~1000-minute post-major mode, so the 0.5× floor landed at ~500 minutes and
deleted the entire short mode as if it were duplicate entries: **103 post-minor
intervals under 400 minutes, not one of them surviving.** The training data then
claimed a minor is followed by a *longer* wait than a major, which is the
opposite of the truth.

The fix computes the baseline per regime — separately for post-minor and
post-major anchors — wherever a geyser has enough of a regime to compute one.
`prev_minor` is false for essentially every row of the other five geysers, so it
is a no-op there, and a row-count guard stops a handful of stray flags earning a
baseline out of nothing. Five geysers came through bit-identical; Old Faithful
gained 135 intervals out of 165,000 and did not move.

What it did to Castle:

| | post-minor median (valid) | post-minor n<400 min kept | `minor_conditional` CRPS | honest 90% coverage |
|---|---:|---:|---:|---:|
| before | 1000 min | 0 / 103 | 110.5 | 60.5% |
| after | 375 min | 101 / 103 | **77.2** | **73.2%** |

The model's two branches went from 1028 vs 1078 minutes — indistinguishable — to
371 vs 1081. Note also that every *unconditional* model on Castle got markedly
worse (`best_parametric` 120 -> 172), which is the correct result rather than a
regression: the bimodality is now visible in the data, so a model that refuses to
condition on the minor flag is finally being charged for it. That is also why the
earlier claim here that Castle's minor gain was "in variance rather than
location" was wrong — the flat location difference was an artifact of the filter,
not the physics.

**The minor-eruption flag is the single best covariate found.** For Old Faithful the
post-minor and post-major interval distributions are almost disjoint — median 70 min
(p95 83) versus median 102 min (p05 91) — and 22% of recent eruptions are minors.
Conditioning on it roughly halves CRPS (8.8 -> 4.5) and beats the classic
duration-based split, because the observer-set flag is cleaner than raw duration
data, which is often missing. For Castle the effect is larger still once the
filter stops hiding it — 172.5 -> 77.2, a 3x separation in the branch medians —
and `minor_conditional` is what actually serves both geysers. The flag is
recorded when the eruption is logged, so it is known at prediction time.

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

### Neighbour geysers: the Indicator works, the Turban lattice doesn't

Two geysers have documented neighbour relationships, and both are already in the
archive at no extra data cost. They are scored on a **nowcast** harness — decision
times on a fixed 30-minute grid, each scored twice with conditioning on and off —
because "how long until the next eruption, standing here now" is both what a gazer
asks and the only unbiased way to score a conditional regime.

| Geyser | Regime | n | CRPS off | CRPS on | Δ | 90% cov off | 90% cov on |
|---|---|---:|---:|---:|---:|---:|---:|
| Beehive | overall | 31,008 | 118.2 | 114.8 | −2.9% | 85% | 89% |
| Beehive | no Indicator | 28,937 | 116.1 | 116.2 | ±0.0% | 89% | 90% |
| **Beehive** | **Indicator running** | **2,071** | **147.4** | **95.2** | **−35.4%** | **29%** | **87%** |
| Grand | overall | 21,656 | 44.5 | 44.5 | +0.2% | 90% | 90% |
| Grand | Turban gated | 1,727 | 42.3 | 42.4 | +0.2% | 92% | 92% |
| Grand | Rift/W. Triplet shifted | 1,688 | 53.6 | 54.4 | +1.6% | 87% | 86% |

**Beehive's Indicator is the single most valuable signal found in this project.**
Beehive's Indicator starts, and Beehive follows about 12 minutes later (n=3,035
since 2015: mean 11.9 min, sd 4.8; a normal fits far better than lognormal or
gamma, KS 0.070 vs 0.178). Measured the other way round, 93.7% of Indicator
entries are followed by Beehive within 25 minutes. During those minutes CRPS falls
by a third and the nominal 90% interval goes from catching **29%** of eruptions to
**87%** — the unconditioned model is wildly overconfident exactly when someone is
standing there waiting. Outside that window nothing changes, which is the point.

It is implemented as a Bayesian **mixture**, not a switch: the Indicator branch
carries weight ∝ reliability × P(lead > elapsed), so if 30 minutes pass with no
eruption the branch decays on its own and the ordinary distribution takes back
over. An early version hard-switched and produced 141 min of error by insisting
"any second now" long after the Indicator had plainly failed.

**Grand's Turban lattice is a negative result, reported rather than deleted.**
Grand starts *with* a Turban: only 0.1% of Grand starts fall 5–13 minutes after
one, against 24% in the first two minutes. Gating the density onto that lattice
looks obviously right and does not work. Turban's interval scatters (sd 4.2 min on
a 19 min period) so extrapolated phase decoheres within about one cycle, and
Grand's own uncertainty is ~100 min — five times the Turban period. The model's
predicted median for Grand never drops below 40 minutes, so the lattice is never
consulted at a range where it could discriminate. Rift (+32 min) and West Triplet
(+15 min) both survive a length-bias-safe significance test yet still fail to
improve the distribution. All three are kept behind flags so the result stays
reproducible.

One trap worth recording: the naive "did Rift erupt anywhere between the two Grand
eruptions?" test gives +45 min, but that is **length-biased** — longer intervals
mechanically have more room for a Rift. Measured in a *fixed* early window it is
+32 min. Roughly 40% of the apparent effect was an artifact of the question.

### Honest coverage

Everything above is scored only on intervals that passed the validity filter — which
excludes exactly the cases the filter exists to remove. A gazer on the boardwalk gets
no such exemption. Re-scoring a plain rolling `lognormal` against **every** interval:

| Geyser | % filter-rejected | 50% cov | 90% cov | 90% cov (filtered) |
|---|---:|---:|---:|---:|
| Old Faithful | 14.0% | 46% | 76% | 93% |
| Grand | 19.3% | 38% | 74% | 91% |
| Daisy | 21.4% | 43% | 70% | 89% |
| Riverside | 37.2% | 36% | 58% | 91% |
| Castle | 30.0% | 51% | 73% | 87% |
| Great Fountain | 43.5% | 31% | 52% | 91% |
| Beehive | 8.2% | 47% | 80% | 87% |

The gap between the last two columns is the real-world cost of observation gaps.
Treat the headline table as an upper bound on field reliability. Castle is the
one row where that gap has meaningfully closed (90% coverage 61% -> 73%), because
the regime-aware baseline stopped throwing away the intervals it was worst at.

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
| `GET /api/scoreboard?days=30` | Rolling accuracy per geyser for this project, the NPS and Geysers.net |
| `GET /api/comparisons/recent?limit=20` | Recent eruptions with each source's prediction beside the actual |
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

## Comparing against the NPS and Geysers.net

Backtests only ever compare this project against itself. The question a gazer
actually asks is whether it beats the prediction already printed on the visitor
centre board — so the deployed service now scores three predictors against the
same eruptions.

### What the data actually allows

GeyserTimes publishes exactly one predictions route, `predictions_latest`: the
predictions open **right now**. There is no date-ranged predictions endpoint
(`/predictions/{from}/{to}` and `/predictions_recent/{minutes}` both 404) and the
nightly archive contains eruptions and notes only. **There is no historical
prediction data to be had at any price**, so a retrospective comparison is
impossible and every number on the scoreboard is accumulated prospectively, from
the moment logging started. `n` begins at zero and the dashboard says so.

Two predictors publish for the geysers modelled here:

| Source | Who | How it appears |
|---|---|---|
| `nps` | National Park Service visitor-centre predictions | Posted by the `GeyserTimes` account (userID 208), marked in the comment as uploaded from the NPS/CartoDB system |
| `geysers_net` | Geysers.net, a long-running third-party predictor | userID 44, with a stated method — usually "add average interval" — and a self-reported probability |

Both conditions are required to classify a prediction as NPS, so anything else
that account ever posts is not silently attributed to the Park Service. An
unrecognised predictor is dropped rather than lumped in with either.

Coverage is partial and worth knowing: in a typical snapshot both sources
predict Old Faithful, Grand, Daisy, Castle and Riverside; only Geysers.net
predicts Great Fountain; **neither predicts Beehive**.

### Scoring, and being fair about it

Each source is scored **against the window it states itself**. The NPS claims
about ±12 minutes on Old Faithful and over two hours on Grand; Geysers.net
states its own; this project's stated window is its nominal 90% interval. An
in-window rate is therefore meaningless on its own — a predictor claiming a
four-hour window should not out-rank one claiming twenty minutes — so the
median window width is reported beside the rate everywhere it appears, and a
source that states no window is simply not scored on that metric.

Three more rules, all applied identically to every source:

- **Latest before the eruption wins.** Sources re-predict constantly. A
  prediction issued at 14:00 and revised at 15:40 is not two attempts; the first
  was withdrawn. Superseded predictions are discarded, not counted as misses.
- **Predictions for a later eruption are excluded.** `futureEruptionNumber > 1`
  forecasts the eruption *after* next, and scoring it against the next one would
  be plain unfair.
- **Eruptions beyond a generous horizon are not scored at all.** If nobody logs
  Riverside overnight, the next logged eruption may be two cycles after the one a
  prediction was aimed at. Any pairing landing more than three window widths past
  the predicted time is dropped for everyone, and counted, so the censoring is
  visible rather than silent.

`scoring.py` is pure — dataclasses in, dataclasses out, no database, no clock,
no network — because the matching rules are where a three-way comparison is won
or lost, and they need to be testable in isolation.

### Cost to GeyserTimes: one more request per cycle

`predictions_latest` returns every open prediction from every predictor for every
geyser in a single response, so logging all sources costs **exactly one extra
HTTP request per five-minute cycle**, on the same TTL and with the same
identifying User-Agent as the eruption sync. That property is asserted in the
test suite alongside the existing sync guarantees.

### Persistence

The ledger has to outlive the container, whose disk is ephemeral, or the
scoreboard would reset to `n=0` every time the service sleeps. Locally it is a
JSON file next to the DuckDB database; deployed, the container PUTs and GETs it
through the same virtual hostname it already uses to pull the snapshot, and the
Worker answers from R2 — so the container still holds no object-storage
credentials. Writes are refused outside the `ledger/` prefix, so a bug in the app
cannot overwrite the eruption archive underneath itself.

Nothing in the path can break a prediction: a ledger that cannot be read starts
empty, one that cannot be written retries next cycle, and the whole scoreboard
update is wrapped so that a failure surfaces as a field on the response rather
than a 500 on the page.

## Deployment

Live at **https://geyser-ai.david-016.workers.dev**.

The Workers runtime has no DuckDB, no SciPy and no lifelines, so there is no
version of this app that runs as a plain Worker. Instead the app runs unmodified
inside a **Cloudflare Container**, and a thin Worker in front of it is the public
front door.

```
browser ──> Worker ──> Container: uvicorn + FastAPI + DuckDB (exactly one instance)
              │             │
              │             └──> geysertimes.org/api/v5/entries_recent  (5-min TTL)
              └──> R2 bucket: geysertimes.duckdb snapshot
```

**A deployed container never downloads the GeyserTimes archive.** The complete
archive is fetched exactly once, by a developer running `uv run geyser-ai ingest`
locally; the resulting DuckDB file is published to R2 and every container reads
it from there:

```bash
uv run geyser-ai ingest          # one archive download, cached in data/raw/
./deploy/publish-snapshot.sh     # ~200 MB -> r2://geyser-ai-snapshots
```

The container's only live traffic to GeyserTimes is the same
`entries_recent/{minutes}` sync the local app makes, on the same five-minute TTL
and with the same identifying User-Agent. That is why the container is capped at
**one instance**: a second one would run its own sync timer and double the
request rate against a small nonprofit's server for no benefit.

The container holds no R2 credentials. It fetches `http://geyser-snapshot.r2/…`,
and the Worker intercepts that hostname with an
[outbound handler](https://developers.cloudflare.com/containers/platform-details/workers-connections/)
that answers from its R2 binding inside the Workers runtime. Only plain HTTP is
intercepted, so the HTTPS calls to geysertimes.org leave the container untouched.

Startup is deliberately split. Cloudflare gives a container about twenty seconds
to start listening, and the snapshot takes longer than that to pull, so
`deploy/entrypoint.py` starts uvicorn immediately and downloads on a background
thread. Until the file lands the API returns its existing "no database" 503, the
Worker holds the request through the container's startup hook, and anything
still early gets a self-refreshing "warming up" page rather than an error.

### Why there is a cache

A prediction request costs the deployed container **20–40 seconds**. That is not
the models being slow — the identical image answers in 0.7 s in a local container
pinned to the same fraction of a CPU. Cloudflare's container CPU is simply far
slower for this scalar NumPy/SciPy work, and it did not improve when tested on
`standard-1` (double the vCPU, four times the memory), so the cheaper instance is
the one deployed.

Rather than change any modelling code, the Worker keeps the last computed
response in the container's Durable Object storage and recomputes it off the
request path:

- a reader is served the cached answer in **under 200 ms**;
- past ten minutes of staleness the Worker stops trusting the cron and
  recomputes on the request, so nobody is ever handed a stale prediction;
- `/api/health` is never cached — it is the honest freshness probe.

The five-minute cron does two different jobs. The **ledger tick is
unconditional**: predictions can only be scored against eruptions that have
already happened, so a scoreboard that advanced only while somebody was watching
would have permanent holes exactly where the park is quietest, and the
comparison against the NPS and Geysers.net would be drawn from a biased sample of
the day. One `/api/predictions` run generates the forecast, logs it, pulls every
open third-party prediction and scores whatever has erupted, so a single call
covers all of it. **Response-cache warming stays visitor-gated** — there is no
point recomputing a dashboard shape nobody has asked for.

That keeps the container awake permanently, which is a deliberate trade. At
`basic` the standing cost is memory and disk rather than CPU: roughly
**$8/month** on top of the $5 Workers Paid plan (≈$6.35 memory, ≈$0.85 CPU,
≈$0.69 disk, after the included 25 GiB-hours, 375 vCPU-minutes and 200 GB-hours).
Halving the cron cadence would roughly double the CPU line and leave the rest
alone, which is why it runs at five minutes and not two.

Served responses carry `x-geyser-cache: hit|stale|miss` and `x-geyser-cache-age`
so the freshness is inspectable rather than implied.

### Settings

| Setting | Value | Why |
|---|---|---|
| `instance_type` | `basic` (1/4 vCPU, 1 GiB, 4 GB disk) | Peak RSS is ~450 MB and the snapshot ~200 MB; larger instances bought no speed |
| `max_instances` | 1 | One sync timer, one poll rate against GeyserTimes |
| `sleepAfter` | 30m | Survives a skipped cron without a cold start |
| cron | `*/5 * * * *` | Drives the ledger around the clock; matches the sync TTL |
| R2 bucket | `geyser-ai-snapshots` | Holds `geysertimes.duckdb` and the ledger |

### Deploying

```bash
npm install
npx wrangler types          # regenerates worker-configuration.d.ts
npx wrangler deploy         # builds the image, pushes it, deploys the Worker
npx wrangler tail           # live logs, container output included
```

Requires Docker running locally and the Workers Paid plan — Containers are not
available on the free plan. Container rollouts are gradual, so for a minute or
two after a deploy some requests can hit the previous version.

**Caveats.** A cold start costs a container boot plus a ~200 MB snapshot
download, then one uncached prediction run — but with the cron running around
the clock, cold starts should only happen after a deploy. Refreshing the archive
means re-running `ingest` and `publish-snapshot.sh`; running containers pick up
the new snapshot on their next cold start, so **a change to the ingest SQL is not
live until the snapshot is republished**.

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
