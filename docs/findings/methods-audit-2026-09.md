# Methods audit, September 2026: what the calculations were getting wrong

A deliberate pass over every calculation between the archive and the card,
made on 2026-09-13 with the site five weeks into its live record. The method:
read the code, re-run the walk-forward on the same evaluation sets the
calibration report uses, pull the live scoreboard, and rank what turned up by
minutes at stake rather than by how interesting it was. Everything below was
then either fixed and shipped (2026-09-13/14) or measured and rejected, and
the detailed write-up of each lives in the document it belongs to. This page
is the ledger of the audit itself, so the next one can start from what this
one found instead of rediscovering it.

Two things the audit got wrong are recorded here as prominently as what it
got right, because an audit that only reports its hits teaches the next
auditor to trust headline numbers.

## Findings, ranked, with outcomes

| # | finding | measured before | outcome | shipped |
|---|---|---|---|---|
| 1 | The scoreboard scored its own censoring rule: the "three window widths" drop had a fixed 6-hour floor that never bound on a tight geyser, so an unlogged eruption in between scored as a full-cycle miss | Old Faithful live MAE 25.2 → 7.5 on clean rows (NPS 5.3); Daisy 16.7 → 6.2 (NPS 5.4); Great Fountain 326 → 75 | Floor is now half the geyser's cycle; duplicate entries within 15 min collapsed | `37bf686` |
| 2 | Production trained on the archive snapshot only — 41 days stale — while the live sync fed nothing but the anchor | Daisy +18% CRPS, Grand +9%, Beehive and Fountain +7% at 42 days | `chain.py` continues the interval chain through the sync with ingest's rules; `/api/health` reports `training_newest_utc` | `cc748fb` |
| 3 | The served tail component (w = 0.15, width from the pooled bimodal history) was ~7–10× heavier than the logger record supports; `TailMixture.ppf` used a linear grid that read Lion's median as 171 min against a true 90 | Served 90% bands covered 97% on four geysers at +2–3% CRPS | Weight measured per geyser from logger pairs (2–11%), width from the branch's own spread, geometric ppf grid; served CRPS within 0.4% of the raw fit | `12c8135` |
| 4 | "A margin under 6% is run-to-run noise" was asserted, never tested | Paired bootstrap on the identical eruptions: Fountain −2.0 min CI [−3.2, −0.8]; Beehive −7.9 [−11.6, −4.4]; on the full run also Grand −1.1 [−1.6, −0.7] and Daisy −0.08 [−0.12, −0.04] | Rule replaced by the interval; four geysers pinned; report, `calibration.json`, method page and tests carry the CI | `12c8135` |
| 5 | Daisy remembers its last interval (detrended lag-1 autocorrelation +0.43; no other geyser above +0.27) and nothing in the roster used it | — | `ar1_lognormal`: CRPS 3.17 → 2.90 (−8.5%, CI [−0.32, −0.21]), calibration intact; Daisy pinned | `12c8135` |
| 6 | Lion's point prediction after a mid-series eruption is the valley between its modes, where it never erupts; 36% of live rows landed on the wrong side | The one candidate signal (preceding in-series spacing) tested: −0.08 min, CI [−0.63, +0.46], not decisive | Card names both modes with the odds on each; the stated probability is Brier-scored on the ledger | `2640167` |
| 7 | The 1.75× validity ceiling deletes real long intervals on Fountain and Artemisia | Restricted to logger pairs: Fountain loses 33 of 3,018, Artemisia 4 of 689 | **Rejected.** Negligible, and the fat tails are already carried at serving time by the measured tail weight. Ceiling unchanged | `72bef3e` |

## What the audit got wrong

**Finding 7 was overstated by about ten times.** The audit's headline said
4.9% and 6.8% of Fountain's and Artemisia's logger-complete intervals sat at
1.5–2.5× the median. True, but most of that band is *inside* the filter; the
mass the ceiling actually removes is 1.1% and 0.6%. The histogram was read
against the wrong boundary. Lesson: state the cost of a threshold against
the threshold, not against a round number near it.

**Finding 4's headline rule was wrong in the other direction too.** The
audit tested four margins on 500 targets and called Grand's not decisive
(CI [−1.35, +0.33]). On the full 1,994-target evaluation it was
([−1.56, −0.67]). A subsample is fine for ranking findings; a pin has to be
decided on the full set. That is now written into the rule.

## What the audit surfaced without fixing

- The backtest scores each model's raw fit; production serves the widened,
  survival-conditioned renewal forecast. After finding 3 the two are within
  0.4% at anchor age zero, but the elapsed-time behaviour — the card moving
  as a geyser runs late — has no offline score outside the Beehive nowcast
  harness. Scoring the served distribution as its own leaderboard row is the
  natural next step.
- Branch flags are backtested as they exist in the archive today, after
  every later edit; live, the model sees the flag as logged. `entry_revisions`
  has recorded edits since 2026-09-07; the size of the optimism should be
  reported once a month of it exists.
- Till's 2017 logger campaign is mostly rejected by the filter against a
  baseline at half the true cycle (data-quality.md, generation 7). No served
  window reaches it.
- The scoreboard's already-scored rows keep the old horizon rule until they
  roll off the 30-day window; the record is append-only by design.

## Negative results from the audit's own experiments

Tried on the same evaluation sets and not worth pursuing, with the number
that says so:

- **Equal-weight ensembles** of the simple models: −1 to −5% on unimodal
  geysers, all of it the adaptive-window effect; +56% on Old Faithful, +66%
  on Castle, +12% on Lion, because the members are unconditional.
- **Parameter-uncertainty predictive** (log-t in place of lognormal): ±0.1%
  everywhere. The windows are large enough.
- **Preceding duration** as a covariate: Fountain −1.0% (CI crosses zero);
  Old Faithful post-major Spearman +0.35 but no CRPS gain over the minor
  flag; Grand, Beehive, Artemisia nothing.
- **Anchor hour of day**: Beehive −1.2% on top of the adaptive window (CI
  [−2.7, −0.1]), Castle −2.3% (not significant), everything else nothing.
  Real and small; the AFT model was right to lose.
- **Castle post-major short intervals**: 7 of 1,728 in five years. The live
  −500 min rows were anchor flags edited after logging, not a missing mode.
- **Lion series position, anchor duration, hour, month** as continue-rate
  predictors: all flat to ±0.05 on 4,986 anchors.
