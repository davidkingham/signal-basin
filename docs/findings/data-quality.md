# Data quality: the archive and API as they actually behave

Everything in this project rests on the GeyserTimes archive, and every large
improvement so far has come from understanding what is wrong with a naive
reading of it. This is the document to read first.

## The core problem: a gap is not an interval

The archive records when somebody *logged* an eruption, not when eruptions
*happened*. Nobody watches Riverside at 3 a.m. in February. So the difference
between two consecutive entries is frequently two or three eruption cycles, not
one — and a naive statistic computed straight off consecutive entries silently
mixes them in.

This is visible, not theoretical. Interval histograms show sharp secondary peaks
at almost exactly 2× and 3× the median: Riverside clusters near 390, 780 and
1150 minutes; Great Fountain near 686 and 1400. Those are not long intervals.
They are one and two eruptions nobody logged.

## The validity filter, and its six generations

An interval is marked `is_valid` only if it is plausible for that geyser. The
rule is simple; getting the *baseline* right took four attempts, and each attempt
moved the headline numbers more than any model change ever has.

```
valid  <=>  0.5 x baseline  <=  interval  <=  1.75 x baseline
```

The upper bound rejects missed eruptions. The lower bound rejects duplicate
entries — two observers logging the same eruption. (Entries within 60 s of each
other are collapsed before this, so the lower bound is catching near-duplicates
rather than exact ones.)

### Generation 1 — a global median, 3x ceiling

The first version used a 3× ceiling against each geyser's all-time median. That
ceiling sits exactly on top of the 2× and 3× harmonics, so it admitted precisely
the contamination it existed to remove. Tightening to **1.75×** cut best-model
CRPS by **20–87% per geyser** (Riverside 106.6 → 14.3).

**Lesson:** look at the interval histogram before choosing a threshold.

### Generation 2 — the median must be local

A single median across the whole 1871-present record is wrong because intervals
*drift*. Daisy ran a 142-minute median in 2019 and 111 in 2026. A threshold
calibrated on the old era lets doubles of the *modern* interval through as a
phantom second mode.

Daisy's nominal 50% interval was covering 87% of eruptions — wildly
under-confident — purely from this. Making the baseline a rolling local one
fixed it: **CRPS 12.4 → 3.2, 50% coverage 87% → 50%**.

### Generation 3 — a local median validates its own contamination

Generation 2 then broke Great Fountain badly (**CRPS 47.8 → 145**). Where
observing is thin, missed eruptions are the *majority* of recorded gaps, so a
local median drifts up to the doubled value and then blesses it. Great
Fountain's local median ran as high as **1361 minutes against a true interval
near 690**.

The fix exploits an asymmetry: **a missed eruption only ever adds time, never
subtracts it.** So a low quantile is robust where the median is not. The current
two-stage baseline is:

1. `base0` = rolling **25th percentile** over ±400 rows — a robust anchor that
   sits inside the true mode even when the majority of gaps are doubles.
2. `med_interval` = rolling median over ±300 rows, computed **only over gaps
   near that anchor** (0.55–1.4 × `base0`). The `CASE` returns NULL outside the
   band and `median()` skips NULLs, so the refined median never sees the
   harmonics.

Post-filter p95/median ratio (≈1.2–1.5 means a clean unimodal distribution, ≈2
means harmonics survived): Great Fountain **2.01 → 1.28**, Beehive 1.59 → 1.52,
Grand 1.46 → 1.40. Old Faithful, Daisy and Riverside were already clean.

The windows are **centred**, deliberately. Identifying corrupt records is
preprocessing, not prediction; a centred baseline tracks drift far better than a
trailing one, and it is smooth and slowly-varying enough to carry no meaningful
information about any individual interval.

### Generation 4 — one baseline cannot serve two processes

Every generation above assumes a geyser has **one** interval distribution to be
local about. Castle does not.

An eruption that fails to reach the steam phase is logged as a **minor**, and the
interval that follows it is a physically different, much shorter process. With
one pooled baseline, the threshold gets set by the ~1000-minute post-major mode,
so the 0.5× floor lands at ~500 minutes — **above the entire short mode** — and
deletes it as if it were duplicate entries.

The numbers, on three years of Castle:

| | pooled baseline | per-regime baseline |
|---|---:|---:|
| post-minor intervals under 400 min, kept | **0 / 103** | **101 / 103** |
| post-minor median (valid) | 1000 min | 375 min |
| post-minor rejection rate | 51.5% | 49.7% |
| post-major median (valid) | 1011 min | 1012 min |

The training data had been asserting that a minor is followed by a *longer* wait
than a major, which is the exact opposite of the truth. The model's two branches
were **1028 vs 1078 minutes** — indistinguishable. After the fix they are **371
vs 1081**.

The baseline is now computed per regime (`prev_minor` true/false) wherever a
geyser has at least `MIN_REGIME_ROWS = 200` rows in that regime. `prev_minor` is
false for essentially every row of the other five geysers, so it is a no-op
there. Verified: five geysers came through **bit-identical**, Old Faithful gained
135 intervals out of 165,812.

Effect on Castle: `minor_conditional` CRPS **110.5 → 77.2**, MAE 151.7 → 101.2,
honest 90% coverage **60.5% → 73.2%**.

**Lesson, and the general form of it:** a validity threshold assumes
unimodality. The `minor` flag tells you when that assumption is false. Before
adding a geyser, check whether it has a documented second mode.

### Generation 5 — a second mode the regime split cannot reach

Lion (added 2026-08-09) broke generation 4's fix in a new way. Lion erupts in
SERIES — ~83-minute intervals while a series runs, ~10 hours between series —
so its distribution is bimodal like Castle's, but the regime flag that
separates Castle's modes does not separate Lion's: the interval after a
non-initial eruption is *itself* a 40/60 mixture of "series continues" and
"series over". No `prev_*` partition makes either side unimodal.

Measured before fixing: with the short mode holding 53% of gaps, the p25
baseline anchors there and the filter deleted **all 7,410 series gaps since
2015** — the Castle deletion, mirrored.

The fix accepts a gap near **either** local mode: a second baseline at the
local 75th percentile, refined the same way as the first, with its own
`[0.5, 1.75]×` band. The dangerous part is that a phantom mode from missed
eruptions sits at exactly 2× (or 3×) the true interval, so the second band
only engages when the long mode is at least **3.5×** the short one
(`SECOND_MODE_RATIO`). Lion's real ratio is ~7; Great Fountain's old
contamination sat at 2.0 and stays rejected.

Verified: Old Faithful, Grand, Riverside, Great Fountain and Beehive came
through **bit-identical**. Lion's series gaps went 0 → 5,742 of 7,410 kept
(the rejected tail is >26 h gaps that plausibly contain a missed series).
Three geysers gained a few *historical* rows — Fountain +532 (3.1–3.6× the
short mode, consistent with its documented Morning-active long regime),
Daisy +116 and Castle +76 (pre-2000 only, 4.2–9.3×) — none in the 2020s, so
no production training window changed for the existing eight.

### Generation 6 — backcountry data breaks three assumptions at once

Lone Star (added 2026-08-09 in planning mode) required three targeted rules,
each of which broke a different assumption the filter had been standing on:

1. **Not every logged event is a cycle event.** Lone Star's minors precede
   the major of the same cycle by ~37 minutes (IQR 28–44, n=107); chained as
   eruptions they shatter a 186-minute cycle into 37/150-minute phantoms.
   They are excluded from the interval chain (`cycle_events` CTE) — the same
   reasoning that keeps Beehive's Indicator out of Beehive's chain, applied
   to a flag instead of a name.

2. **The p25 anchor assumes singles are ≥25% of local gaps.** Backcountry
   logging makes singles the *minority* (~31% recently, fewer before), so the
   p25 sat on a harmonic and the refined median self-validated a 1270-minute
   "cycle". `SPARSE_SINGLES_GEYSERS` anchors at the local **p10**, still
   safely above duplicate noise, which the 60-second dedupe and the 0.5×
   floor already handle.

3. **The second-mode ratio guard assumes harmonics live at 2–3×.** With half
   the eruptions unlogged, the local p75 sits in a *smear* of 4–12×
   missed-day multiples that sails past the 3.5× guard — so for
   sparse-singles geysers the second-mode band stays off entirely: where
   singles are the minority, there is no trustworthy long mode by
   construction.

Result: 686 valid intervals, median 180 min, log-sd 0.133 — against a
pre-fix "valid" set with median 1270 and log-sd 1.35. Every other geyser's
rows were verified unchanged by rules 2 and 3 (they key on the geyser name),
and rule 1 removed exactly the 730 Lone Star minor-entry rows.

### A trap when comparing filter generations

Changing the filter changes *which intervals are in the evaluation set*, so CRPS
is not strictly comparable across generations — the exam gets easier or harder at
the same time as the student changes. Castle's CRPS once *rose* (104 → 110)
purely because the two-stage filter removed contaminated easy cases.

**Coverage against all intervals is the meaningful cross-version signal.** Always
report it when changing the filter.

Related: after generation 4, every *unconditional* model on Castle got markedly
worse (`best_parametric` 120 → 172). That is the correct result, not a
regression — the bimodality is now visible in the data, so a model that refuses
to condition on the minor flag is finally being charged for it.

## Honest coverage

The headline table scores only intervals that pass the filter — which excludes
exactly the cases the filter exists to remove. A gazer gets no such exemption.
Re-scoring a plain rolling lognormal against **every** interval:

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
Publish both columns, always.

## Traps in the archive

- **`associated_primaryID` is self-referential for primary eruptions**, not
  NULL. The intuitive `WHERE associated_primaryID IS NULL` filter returns zero
  rows. The correct test is `associated_primaryID = eruptionID`.
- **Eruption epochs are negative for historical records.** The archive reaches
  back well before 1970, so `WHERE epoch > 0` silently drops the early record.
- **Community-flagged `questionable` (`q`) entries** are excluded from the
  `eruptions` view. They are usually "I think I saw it erupt".
- **Observer-attention confound.** When gazers cluster at one geyser, the others'
  apparent intervals inflate because eruptions go unlogged. Any cross-geyser
  correlation study **must** control for this, or it will discover that geysers
  are correlated with where people are standing.
- **`E` (electronic) entries are volunteer batch-transcriptions from data
  loggers**, not an institutional real-time feed. They arrive in bursts. See
  [model-results.md](model-results.md) for why conditioning on entry type does
  not help despite the data-quality difference being real.

## Traps in the API (v5)

- **`entries_recent` takes the lookback as a path segment**: `/entries_recent/180`.
  Every *wrong* shape tried — `?minutes=180`, `?count=5`, and others — returns
  `200 OK` with `{"status":"success","entries":[]}` rather than an error. An
  empty success is indistinguishable from "nothing erupted recently", so it is
  very easy to conclude the endpoint is dead when the URL is merely wrong.
- **Anubis challenges browser-like user agents and passes plain ones.**
  GeyserTimes runs [Anubis](https://github.com/TecharoHQ/anubis) to keep scrapers
  off the site. A `Mozilla/...` UA gets challenged; an honest `geyser-ai/0.1` UA
  is served normally. Identifying honestly is both the right thing to do and the
  thing that works — the effect is arguably the opposite of what is intended.
- **`predictions_latest` is the only predictions route, and it is present-tense
  only.** It returns predictions that are open *right now*. There is no
  date-ranged predictions endpoint — `/predictions/{from}/{to}` and
  `/predictions_recent/{minutes}` both 404 — and the nightly archive contains
  eruptions and notes only.
  **There is no historical prediction data available at any price.** Any
  comparison against NPS or Geysers.net must be accumulated prospectively from
  the day you start logging. This is the single biggest constraint on the
  scoreboard feature.
- **The docs are slightly stale**: the response field is `futureEruptionNumber`,
  not `eruptionForecastNumber` as documented.
- **Datetimes** are Unix epoch by default; add `?iso=1` for ISO 8601. Fields that
  are genuinely absent come back as empty strings, not null.

## Politeness, which is a contract not a courtesy

GeyserTimes runs on donated hardware and their usage policy calls polling the
same URL more than once a minute abusive. What this project sends:

- **One** archive snapshot download ever, cached, never automatically re-fetched.
  The deployed service never downloads the archive at all — it reads a copy built
  on a developer's laptop and published to R2.
- **Two API calls per five-minute cycle**: one `entries_recent/{minutes}`, one
  `predictions_latest`. 24 requests an hour, against a permitted 60 per URL.
- Exactly **one** instance, enforced by `max_instances: 1` in the Cloudflare
  config, specifically so this cannot quietly become N copies of the polling loop.
- An identifying `geyser-ai` User-Agent, never a spoofed browser one.

These properties are **asserted in the test suite** (`tests/test_sync.py`,
`tests/test_scoreboard.py`) rather than trusted, because they are a promise made
to a nonprofit in the README.
