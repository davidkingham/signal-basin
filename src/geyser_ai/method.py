"""Why the numbers are what they are: the served methodology, in one place.

The dashboard shows a prediction. This module explains how that prediction was
arrived at, in enough detail that a gazer or a researcher can find the holes in
it. Two kinds of thing live here, deliberately kept apart:

* **Prose** — curated, versioned with the code, written for somebody who did
  not live through the work. Every claim traces back to `docs/findings/`.
* **Numbers** — never typed by hand. They are read from `calibration.json`,
  which `geyser-ai backtest` writes straight out of the walk-forward harness,
  and every derived fact (which model actually serves, how far behind the
  leaderboard winner it is, whether a nominal interval is miscalibrated) is
  computed here from that artifact and from `models.BEST_MODEL_BY_GEYSER`.
  Nothing on the method page can drift from what production serves without a
  test failing.

`calibration.json` sits inside the package rather than in `reports/` because
the deployed image copies `src/` and nothing else (see the Dockerfile). A
methodology that could not be served in production is a methodology nobody
reads.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from .config import PHASE_WINDOW_CYCLES, TARGET_GEYSERS
from .models import BEST_MODEL_BY_GEYSER, default_model_name

CALIBRATION_PATH = Path(__file__).parent / "calibration.json"

REPO_URL = "https://github.com/davidkingham/signal-basin"
ISSUES_URL = f"{REPO_URL}/issues"
FINDINGS_URL = f"{REPO_URL}/blob/main/docs/findings"
REPORT_URL = f"{REPO_URL}/blob/main/reports/calibration_report.md"

# A margin this small is run-to-run noise on a 3-year walk-forward, not
# evidence. It is the rule that decides whether a geyser gets a pinned model or
# keeps the default, and the page states it rather than leaving the reader to
# infer why an apparent winner is not being served.
NOISE_MARGIN_PCT = 6.0

# ─────────────────────────────────────────────────────────────────────────────
# Scoreboard methodology. Lives here with the rest of the prose; `service.py`
# imports it so the scoreboard endpoint and the method page cannot disagree.
# ─────────────────────────────────────────────────────────────────────────────
METHODOLOGY = (
    "GeyserTimes publishes only the predictions that are open right now -- there is no "
    "historical predictions endpoint and none in the nightly archive -- so every number here "
    "was accumulated prospectively, from the moment logging started. "
    "Each source is scored against the window it states itself: the National Park Service and "
    "Geysers.net publish an explicit window with every prediction, and this project's stated "
    "window is its nominal 90% interval. In-window rate is therefore only meaningful beside the "
    "median window width, which is why both are always shown. "
    "When a source re-predicts, only the last prediction issued before the eruption is scored; "
    "the ones it replaced are discarded rather than counted as misses. "
    "Coverage is the share of scored eruptions for which this source had a prediction open, out "
    "of the eruptions any source predicted. "
    "Eruptions that land more than three window widths past a prediction are dropped for every "
    "source alike: in crowd-sourced data that usually means an eruption went unlogged in "
    "between, and charging that to the forecaster would be measuring the observers instead."
)

# ─────────────────────────────────────────────────────────────────────────────
# The model roster
# ─────────────────────────────────────────────────────────────────────────────
MODEL_ROSTER: dict[str, dict[str, str]] = {
    "rolling_normal": {
        "label": "rolling normal",
        "what": (
            "Rolling mean ± window over recent intervals, read as a normal distribution. "
            "This is essentially what the existing community dashboards show, and it is "
            "carried as the baseline every other model has to beat."
        ),
    },
    "lognormal": {
        "label": "lognormal",
        "what": "Maximum-likelihood lognormal fit over a rolling window of recent intervals.",
    },
    "weibull": {
        "label": "Weibull",
        "what": "The same rolling-window MLE fit, with a Weibull instead of a lognormal.",
    },
    "best_parametric": {
        "label": "best parametric",
        "what": (
            "Refits both the lognormal and the Weibull at every prediction and picks between "
            "them by held-out likelihood, so the choice of family is made by the data rather "
            "than once, by hand, forever. The default across the roster."
        ),
    },
    "adaptive_lognormal": {
        "label": "adaptive lognormal",
        "what": (
            "Detects the most recent changepoint in the interval series and selects the "
            "training-window length by held-out likelihood. Built for geysers whose cycle has "
            "drifted, where a long window trains on a regime that no longer exists."
        ),
    },
    "minor_conditional": {
        "label": "minor-conditional",
        "what": (
            "Branches on whether the previous eruption was logged as a minor and fits each "
            "branch separately. The interval after a minor is a physically different process, "
            "not a noisy draw from the same one."
        ),
    },
    "series_conditional": {
        "label": "series-conditional",
        "what": (
            "Branches on the anchor's series-initial flag; each branch is a two-component "
            "lognormal mixture (in-series interval vs series gap) weighted by that branch's "
            "measured continue rate. On a window that is not clearly bimodal it degrades to "
            "the pooled fit, so it is safe to carry anywhere."
        ),
    },
    "duration_lognormal": {
        "label": "duration-conditional",
        "what": (
            "Old Faithful's classic short/long split on the preceding eruption's duration. "
            "Carried for comparison: the observer-set minor flag beats it, because duration "
            "data is often missing while the flag is not."
        ),
    },
    "entry_conditional": {
        "label": "entry-type-conditional",
        "what": (
            "Conditions on whether the anchor came from an electronic logger or a human. The "
            "data-quality difference behind it is real and measured; the model does not help. "
            "Kept in the roster and reported rather than quietly dropped."
        ),
    },
    "weibull_aft": {
        "label": "Weibull AFT",
        "what": (
            "A lifelines accelerated-failure-time survival regression with covariates — "
            "previous interval, hour of day, day of year, entry flags — refit periodically. "
            "The most sophisticated model here and, on almost every geyser, one of the worst."
        ),
    },
}

# ─────────────────────────────────────────────────────────────────────────────
# The overall write-up. Ordered as the page reads.
# ─────────────────────────────────────────────────────────────────────────────
OVERVIEW: list[str] = [
    "Signal Basin predicts Yellowstone geyser eruptions as **probability distributions** "
    "rather than a time with a window bolted on, using the eruption record that volunteer "
    "observers have built through GeyserTimes since 1871. This page is the working behind "
    "every card: how a prediction is made, how the models were tested, what they actually "
    "score, and — at greater length than is comfortable — where they are wrong.",
    "It is written to be argued with. Every number below comes from a walk-forward backtest "
    "whose code is public, the negative results are recorded at the same length as the wins, "
    "and each geyser's section ends with the gaps in its own forecast. If you can see "
    "something here that is wrong, that is the point of publishing it.",
]

SECTIONS: list[dict[str, Any]] = [
    {
        "id": "pipeline",
        "eyebrow": "the path",
        "title": "How one prediction is made",
        "lede": (
            "Every card on the dashboard is the end of the same seven-step path. Nothing is "
            "hand-tuned per geyser except where this page says so explicitly."
        ),
        "steps": [
            {
                "title": "Find the anchor",
                "text": (
                    "The most recent logged eruption of that geyser, taken from the archive "
                    "snapshot unioned with everything the five-minute GeyserTimes sync has "
                    "pulled since. Its flags — minor, major, series-initial, webcam, "
                    "electronic, approximate — come along, because several models condition "
                    "on them and all of them are known at prediction time."
                ),
            },
            {
                "title": "Load the history",
                "text": (
                    "Every interval that geyser has ever recorded which passed the validity "
                    "filter, in order. Nothing is normalised across geysers and no other "
                    "geyser's data is used, except where a neighbour signal is named below."
                ),
            },
            {
                "title": "Fit the model that serves that geyser",
                "text": (
                    "Each geyser is served by a specific model, chosen by walk-forward "
                    "backtest and recorded in code. It is refit at every prediction on that "
                    "geyser's own recent history — nothing is trained once and frozen."
                ),
            },
            {
                "title": "Produce a distribution, not a time",
                "text": (
                    "The model returns a full probability distribution over the next "
                    "interval. The median becomes the headline time and the 50% and 90% "
                    "credible intervals become the two bands. The width of the smear is the "
                    "answer as much as its centre is."
                ),
            },
            {
                "title": "Allow for eruptions nobody saw",
                "text": (
                    "The forecast is then re-weighted as a renewal process, because a silent "
                    "stretch in crowd-sourced data usually means nobody was watching rather "
                    "than that the geyser is overdue. See “Missed eruptions” below — this is "
                    "the step most likely to surprise a gazer reading a card at 3 a.m."
                ),
            },
            {
                "title": "Let a live neighbour signal take over, if one is running",
                "text": (
                    "Beehive's Indicator is the only signal that has earned this. When it is "
                    "running, a nowcast mixture replaces the interval model's timing; when it "
                    "is not, nothing changes."
                ),
            },
            {
                "title": "Log the prediction before the eruption happens",
                "text": (
                    "Every prediction is written to a ledger the moment it is made, and "
                    "scored later against what actually happened, beside the NPS and "
                    "Geysers.net over the identical window. Nothing on the scoreboard is "
                    "computed after the fact. The cards that make no clock-time claim — "
                    "planning cards, and Steamboat — log nothing, because scoring them "
                    "would score a claim never made."
                ),
            },
        ],
    },
    {
        "id": "data",
        "eyebrow": "the data",
        "title": "The data, and the one thing wrong with it",
        "body": [
            "Everything here rests on the GeyserTimes archive — about 1.53 million eruption "
            "entries, recorded by volunteer observers over more than a century, plus a "
            "five-minute top-up from the public API. This project adds no observations of "
            "its own. It is an analysis layer, and the community's logging is the whole "
            "foundation under it.",
            "The single fact that governs every modelling decision below: **the archive "
            "records when somebody logged an eruption, not when an eruption happened.** "
            "Nobody is watching Riverside at 3 a.m. in February, so the gap between two "
            "consecutive entries is frequently two or three eruption cycles rather than one.",
            "This is visible rather than theoretical. Interval histograms show sharp "
            "secondary peaks at almost exactly 2× and 3× the median — Riverside clusters "
            "near 390, 780 and 1150 minutes; Great Fountain near 686 and 1400. Those are not "
            "long intervals. They are one and two eruptions nobody logged.",
            "Community-flagged questionable entries are excluded. Entries within 60 seconds "
            "of each other are collapsed, because two observers logging the same eruption is "
            "common and would otherwise read as an impossibly short interval.",
        ],
    },
    {
        "id": "filter",
        "eyebrow": "cleaning",
        "title": "The validity filter, and its six generations",
        "lede": (
            "An interval counts as valid only when it is plausible for that geyser: between "
            "0.5× and 1.75× a local, regime-specific baseline. The upper bound drops missed "
            "eruptions, the lower bound drops duplicate entries. Getting that baseline right "
            "took six attempts, and each one moved the headline numbers more than any model "
            "change ever has. If you have a day to spend improving these predictions, spend "
            "it here rather than on models."
        ),
        "generations": [
            {
                "n": 1,
                "title": "A 3× ceiling sits exactly on the harmonics",
                "text": (
                    "The first version allowed anything up to 3× the all-time median, which "
                    "admits precisely the doubled and tripled intervals it existed to remove. "
                    "Tightening to 1.75× cut best-model error by 20–87% per geyser "
                    "(Riverside 106.6 → 14.3 CRPS). Look at the histogram before choosing a "
                    "threshold."
                ),
            },
            {
                "n": 2,
                "title": "The baseline has to be local, because intervals drift",
                "text": (
                    "One median across an 1871-present record is wrong: Daisy ran a "
                    "142-minute median in 2019 and 111 in 2026, so doubles of the modern "
                    "interval slid under a ceiling set by the old one. Daisy's nominal 50% "
                    "interval was covering 87% of eruptions. A rolling local baseline fixed "
                    "it — CRPS 12.4 → 3.2, 50% coverage 87% → 50%."
                ),
            },
            {
                "n": 3,
                "title": "A local median validates its own contamination",
                "text": (
                    "Where observation is thin, missed eruptions are the majority of recorded "
                    "gaps, so a local median drifts up to the doubled value and then blesses "
                    "it: Great Fountain's ran as high as 1361 minutes against a true interval "
                    "near 690, and its CRPS went 47.8 → 145. The fix exploits an asymmetry — "
                    "a missed eruption only ever adds time — so a low quantile is robust "
                    "where the median is not. A rolling 25th percentile anchors the true "
                    "mode, then the median is recomputed over only the gaps near that anchor."
                ),
            },
            {
                "n": 4,
                "title": "One baseline cannot serve two processes",
                "text": (
                    "Castle's post-minor interval is a physically different, much shorter "
                    "process. Pooled, the baseline tracked the ~1000-minute post-major mode, "
                    "so the 0.5× floor landed above the entire short mode and deleted it as "
                    "duplicate entries: 103 post-minor intervals under 400 minutes, none of "
                    "them surviving. The training data was asserting that a minor is followed "
                    "by a longer wait than a major — the exact opposite of the truth. "
                    "Computing the baseline per regime took Castle's post-minor median from "
                    "1000 to 375 minutes and its model's two branches from an "
                    "indistinguishable 1028-vs-1078 to 371-vs-1081."
                ),
            },
            {
                "n": 5,
                "title": "A second mode the regime split cannot reach",
                "text": (
                    "Lion erupts in series — ~83-minute intervals while a series runs, ~10 "
                    "hours between — and no previous-eruption flag makes either side "
                    "unimodal, so the filter deleted all 7,410 of its series gaps. A second "
                    "acceptance band around the local long mode restored 5,742 of them. It "
                    "engages only when the long mode is at least 3.5× the short one, so a "
                    "phantom mode sitting at 2× or 3× — which is what missed eruptions look "
                    "like — can never qualify."
                ),
            },
            {
                "n": 6,
                "title": "Backcountry data breaks three assumptions at once",
                "text": (
                    "Lone Star and Till needed three more rules. Their minors are not cycle "
                    "events (Lone Star's precede the major by ~37 minutes; Till's are "
                    "afterplay) and are excluded from the interval chain. Where singles are a "
                    "minority of gaps the 25th-percentile anchor sits on a harmonic, so those "
                    "geysers anchor at the 10th instead. And the second-mode band stays off "
                    "for them entirely, because with half the eruptions unlogged there is no "
                    "trustworthy long mode by construction. Till went from an apparent "
                    "“12-minute median at log-sd 2.16” to a 12-hour cycle at log-sd 0.072 — "
                    "the tightest long-interval geyser in the project, hidden entirely inside "
                    "a data-cleaning assumption."
                ),
            },
        ],
        "closing": (
            "Two consequences worth carrying: the filter is preprocessing, not prediction — "
            "its windows are centred rather than trailing, and it is smooth enough to carry "
            "no information about any individual interval — and **CRPS is not comparable "
            "across filter generations**, because changing the filter changes which intervals "
            "are in the exam. Coverage against all intervals is the meaningful cross-version "
            "signal."
        ),
    },
    {
        "id": "backtest",
        "eyebrow": "testing",
        "title": "How the models are scored",
        "body": [
            "Walk-forward over the last three years. At every evaluated eruption a model sees "
            "**only** intervals strictly earlier than the one it is predicting: no refitting "
            "on the future, no global normalisation, no peeking. Every model is scored on the "
            "same set of target eruptions, so none can improve its average by silently "
            "skipping the hard ones.",
            "There is a rule that came out of getting this wrong. The covariate model's early "
            "apparent win on Riverside was leakage — covariates were being read off the "
            "eruption being predicted rather than off the anchor, so it was effectively using "
            "the next interval to spot missed-eruption doubles. Predicting an interval means "
            "standing at the previous eruption: only that eruption's clock time and flags are "
            "knowable, which is why the intervals table exposes `prev_hour_local`, "
            "`prev_minor` and so on, and never the target's own.",
        ],
        "metrics": [
            {
                "name": "CRPS",
                "unit": "minutes, lower is better",
                "text": (
                    "A proper scoring rule over the whole predicted distribution rather than "
                    "just its centre. It is the only fair way to compare a distribution "
                    "against a point-plus-window, and it cannot be gamed by widening: a "
                    "model that hedges is charged for the hedge."
                ),
            },
            {
                "name": "MAE",
                "unit": "minutes",
                "text": "Absolute error of the predicted median — the number a gazer feels.",
            },
            {
                "name": "Coverage",
                "unit": "% of eruptions inside a nominal interval",
                "text": (
                    "A well-calibrated 90% interval catches 90%. **More is not better** — it "
                    "means the model is under-confident and the band is wider than it needed "
                    "to be. Coverage far from nominal says the predicted distribution is the "
                    "wrong shape, which is a different failure from being merely imprecise."
                ),
            },
        ],
    },
    {
        "id": "choice",
        "eyebrow": "model choice",
        "title": "How the served model is chosen",
        "body": [
            "The walk-forward winner is not automatically the model that serves. On most "
            "geysers the leaderboard is separated by a couple of percent, which is run-to-run "
            "noise on a three-year window, and pinning a production choice on that would be "
            "overfitting the leaderboard rather than improving the forecast. Those geysers "
            "keep the default, `best_parametric`, and this page shows both numbers so you can "
            "see exactly what that decision costs.",
            "Three geysers are pinned to something else, because their process has real state "
            "and the margin is decisive rather than noisy: Old Faithful and Castle to the "
            "minor-conditional model, Lion to the series model. Till is pinned to the "
            "adaptive model because its cycle has drifted and long-window fits train on a "
            "regime that no longer exists.",
            "This distinction is load-bearing and was once got wrong in production: the "
            "serving path defaulted every geyser to `best_parametric` while the published "
            "table claimed otherwise, which quietly discarded a 47% improvement on Old "
            "Faithful and 55% on Castle. The live scoreboard caught it; the backtest never "
            "could have.",
        ],
    },
    {
        "id": "missed",
        "eyebrow": "unlogged eruptions",
        "title": "Missed eruptions at prediction time",
        "body": [
            "The naive forecast conditions on survival — *it has not erupted, so it is "
            "overdue* — which is only sound if we would certainly have seen it. In "
            "crowd-sourced data that fails constantly.",
            "So the geyser is treated as a **renewal process** from the last *logged* "
            "eruption, with each eruption logged independently with some probability. A path "
            "on which k eruptions fell inside the silent window is consistent with the "
            "evidence only if all k went unlogged, so it carries weight (1 − p)^k. Weighting "
            "simulated paths that way interpolates between the two regimes on its own: with "
            "fresh data it reduces to ordinary survival conditioning, and with stale data the "
            "weight shifts onto the missed-eruption hypotheses and the forecast correctly "
            "becomes *“it already went; the next one is roughly one interval from whenever "
            "that was.”*",
            "That logging probability is estimated from the data rather than tuned — it is "
            "the recent share of gaps that came through the validity filter as single "
            "intervals — and it is evaluated **at the hour each missed eruption would have "
            "happened**, not at the present moment. Evaluating it now made a 12-hour "
            "overnight gap at Fountain look well-observed, because morning gazers were "
            "logging all over the basin, and the card called it overdue instead of concluding "
            "the 2 a.m. eruption went unlogged.",
            "This is what the “still in this cycle” versus “already erupted, unseen” split on "
            "each card is showing: the model's own weight on the two stories, not a rule of "
            "thumb.",
        ],
    },
    {
        "id": "honest",
        "eyebrow": "honest coverage",
        "title": "Honest coverage: scoring the intervals the filter throws away",
        "body": [
            "Every headline number is measured only on intervals that passed the validity "
            "filter — which excludes exactly the cases the filter exists to remove. A gazer "
            "on a boardwalk gets no such exemption.",
            "So a plain rolling lognormal is re-scored against **every** interval, including "
            "the rejected ones, still trained only on valid history. The gap between the two "
            "columns is the real-world cost of observation gaps, and it is large: treat the "
            "headline table as an upper bound on field reliability. Where the honest number "
            "is poor, the fix is almost never a better model — it is more eyes, or a logger.",
        ],
    },
    {
        "id": "neighbours",
        "eyebrow": "live signals",
        "title": "Neighbour geysers and live signals",
        "body": [
            "Signals are scored on a **nowcast** harness rather than the interval harness: "
            "decision times on a fixed 30-minute grid, each scored twice with conditioning on "
            "and off. “How long until the next eruption, standing here now” is both the "
            "question a gazer actually asks and the only unbiased way to score a conditional "
            "regime — scoring only the moments just before an eruption would be conditioning "
            "on the answer.",
            "**Beehive's Indicator is the single most valuable signal in this project.** The "
            "Indicator starts and Beehive follows about 13 minutes later (n=5,441 since 2000: "
            "median 13.0 min, p5–p95 3.0–20.6; a normal fits far better than a lognormal or "
            "gamma). Measured the other way round, 93.7% of Indicator entries are followed by "
            "Beehive within 25 minutes. It is implemented as a Bayesian mixture rather than a "
            "switch: the Indicator branch carries weight proportional to its reliability times "
            "the probability the lead time has not already elapsed, so if 30 minutes pass with "
            "no eruption the branch decays on its own. An early version hard-switched and "
            "produced 141 minutes of error by insisting “any second now” long after the "
            "Indicator had plainly failed.",
            "Everything else gazers watch is surfaced wearing its own measured rate, because "
            "the number is the honesty: a 7% signal has to say 7%.",
        ],
        "signals": [
            {
                "signal": "Beehive's Indicator",
                "target": "Beehive",
                "measured": "~13 min lead, 93.7% within 25 min",
                "role": "Full nowcast — replaces the interval model's timing while it runs",
            },
            {
                "signal": "Beehive's South Bubbler",
                "target": "Beehive",
                "measured": "63% within 3 h, median 38 min",
                "role": "Heads-up note on the card, rate stated",
            },
            {
                "signal": "Beehive's Close to Cone",
                "target": "Beehive",
                "measured": "36% within 2 h",
                "role": "Below the display floor — measured, not shown",
            },
            {
                "signal": "Turban / West Triplet",
                "target": "Grand",
                "measured": "79% / 60% within 2–4 h",
                "role": (
                    "Presence only. That rate is logging bias, not physics — people log "
                    "Turban when they are already sitting at Grand near due time — so the "
                    "card claims nothing more than “observers on station”."
                ),
            },
            {
                "signal": "Giant Hot Period",
                "target": "Giant",
                "measured": "7% within 6 h",
                "role": "Park-wide alert with its rate printed, because gazers sprint anyway",
            },
            {
                "signal": "Fan & Mortar event cycle",
                "target": "Fan & Mortar",
                "measured": "18% within 24 h, median ~8 h",
                "role": "Park-wide alert, rate stated",
            },
            {
                "signal": "Grotto Fountain",
                "target": "Grotto",
                "measured": "96% within minutes",
                "role": "Simultaneous rather than predictive — deliberately skipped",
            },
        ],
    },
    {
        "id": "negatives",
        "eyebrow": "negative results",
        "title": "What did not work",
        "lede": (
            "Recorded at the same length as the wins, and not deleted when they stopped being "
            "interesting. Knowing these were tried, with the numbers, is worth as much as the "
            "things that worked — it stops the next person spending a week on them."
        ),
        "items": [
            {
                "title": "The covariate survival model does not earn its complexity",
                "text": (
                    "`weibull_aft` — the most sophisticated model here — finishes in the "
                    "bottom half on essentially every unimodal geyser, and on Lion, its one "
                    "good showing, it still trails the purpose-built series model by 11%. "
                    "Simple rolling fits beat it nearly everywhere, and a plain rolling mean "
                    "wins outright on Beehive. Its early apparent win on Riverside was the "
                    "leakage bug described above."
                ),
            },
            {
                "title": "The Turban → Grand lattice: real structure, no predictive value",
                "text": (
                    "Grand starts *with* a Turban: only 0.1% of Grand starts fall 5–13 "
                    "minutes after one, against 24% in the first two minutes (n=3,710). "
                    "Gating Grand's density onto that lattice looks obviously right and does "
                    "nothing (+0.2% CRPS). Turban's own interval scatters — sd 4.2 min on a "
                    "19-minute period — so extrapolated phase decoheres within about one "
                    "cycle, and Grand's own uncertainty is ~100 minutes, five times the "
                    "Turban period, so the lattice is never consulted at a range where it "
                    "could discriminate."
                ),
            },
            {
                "title": "Rift and West Triplet: a real effect that still makes it worse",
                "text": (
                    "Both shift Grand's interval (+32 and +15 min) and both survive a "
                    "length-bias-safe significance test, and both worsen the predicted "
                    "distribution (+1.6% CRPS). One trap worth keeping: the naive “did Rift "
                    "erupt anywhere between the two Grand eruptions?” test gives +45 min, but "
                    "that is length-biased — longer intervals mechanically have more room for "
                    "a Rift. Measured in a fixed early window it is +32. Roughly 40% of the "
                    "apparent effect was an artifact of the question."
                ),
            },
            {
                "title": "Electronic loggers: a real data-quality difference that does not predict",
                "text": (
                    "At Great Fountain, consecutive logger entries are genuinely cleaner than "
                    "human-only stretches (median 669 vs 708 min, sd 95 vs 276) and it is "
                    "demonstrably not a timestamp offset — an offset would push the two mixed "
                    "pairs apart symmetrically, which is not the pattern. It is completeness: "
                    "a logger catches every eruption. The implied model does not help "
                    "(46.0 vs 45.6 for a plain lognormal), and it is kept in the roster and "
                    "reported rather than quietly dropped."
                ),
            },
            {
                "title": "Weather, tides, rain and earthquakes: almost all null",
                "text": (
                    "Wind on Daisy is the one external forcing large enough to matter, and it "
                    "is not implemented yet. Wind on Old Faithful is null (0 of 11 storms; "
                    "both the published ~2.7 min and our ~1.6 min amplitudes sit below the "
                    "error floor). Rainfall is a demonstrated null within 3 hours of events "
                    "≥7 mm/h. Earth tides were rebutted in print and should not be "
                    "resurrected — they are useful only as a negative control, where any "
                    "model that finds them important is telling you your procedure leaks. "
                    "Snowpack and hydrology are real at 2–11 month lags, which is ≈0.03 min "
                    "of drift per interval and already absorbed by the local baseline. "
                    "Earthquakes need >0.1 MPa dynamic stress; M9.1 Tōhoku did nothing here."
                ),
            },
            {
                "title": "Seismic detection of Steamboat: four rounds of measured failure",
                "text": (
                    "A single-station detector at Norris false-fires on wind, misses one "
                    "eruption in four, and loses days to winter telemetry gaps. The "
                    "textbook fix — a two-station amplitude ratio — also fails, because the "
                    "false positives are cultural noise inside the museum vault itself, which "
                    "is local to the station in exactly the way the eruption is, so no "
                    "station geometry can separate them. A third reference station failed the "
                    "same way. What survived is narrower and honest: a season-aware watch "
                    "with a night gate, a quiet-baseline gate, a sustained-amplitude "
                    "criterion and a regional-earthquake veto, which scored zero false "
                    "positives in 1,606 gate-hours and detected 4 of 13 eruptions — all "
                    "evening or overnight, exactly when human reports lag most. It is "
                    "suspended during the oversnow season, when snowcoaches and groomers park "
                    "on top of the seismometer, and it says so on the card."
                ),
            },
        ],
    },
    {
        "id": "scoreboard",
        "eyebrow": "the scoreboard",
        "title": "Scored against the NPS and Geysers.net",
        "body": [
            "A backtest only ever compares this project against itself. The question a gazer "
            "actually asks is whether it beats the prediction already printed on the visitor "
            "centre board, so every prediction from all three sources is logged before the "
            "eruption and scored against the same eruptions afterwards.",
            METHODOLOGY,
            "Coverage is partial and worth knowing before reading the table: in a typical "
            "snapshot both sources predict Old Faithful, Grand, Daisy, Castle and Riverside; "
            "only Geysers.net predicts Great Fountain; neither predicts Beehive, and neither "
            "predicts most of the rest of the roster here. Where a source is absent it is "
            "shown as absent rather than scored as a miss.",
        ],
    },
]

# ─────────────────────────────────────────────────────────────────────────────
# Per-geyser notes. Numbers in this block are the ones that do NOT come out of
# the backtest artifact -- distribution shapes, sample sizes from the findings
# work, physical description. Everything the backtest measures is attached
# from calibration.json instead, never repeated here.
# ─────────────────────────────────────────────────────────────────────────────
GEYSER_NOTES: dict[str, dict[str, Any]] = {
    "Old Faithful": {
        "summary": "The best-predicted geyser here, and the clearest covariate in the project.",
        "why": (
            "An eruption that does not run to full length is logged as a **minor**, and the "
            "interval after one is a different process: median 70 min (p95 83) against 102 min "
            "(p05 91) after a full eruption, with 22% of recent eruptions minors. The two "
            "distributions barely overlap, so branching on the observer's flag roughly halves "
            "the error. It beats the classic duration-based split because the flag is set when "
            "the eruption is logged, while duration data is often missing."
        ),
        "data": (
            "91% of valid intervals come from the webcam, which is why its observation record "
            "is among the cleanest in the set despite erupting many times a day."
        ),
        "gaps": [
            "Wind and air temperature do nothing measurable here — 0 of 11 storms in the "
            "published record produced a response, and both the literature's ~2.7 min and our "
            "own ~1.6 min amplitudes sit below the error floor. The contrast with Daisy is the "
            "physically expected one: a deep insulated conduit against an exposed pool.",
            "Preplay is a documented minutes-scale precursor and a seismometer sits 1.2 km "
            "away, but nothing here uses either. An Indicator-style nowcast for Old Faithful "
            "is unbuilt research, not a planned feature.",
        ],
    },
    "Grand": {
        "summary": "The most-watched of the big fountain geysers, and a flat leaderboard.",
        "why": (
            "No model separates from the pack. The walk-forward winner is inside the noise "
            "margin against the default, so Grand keeps the default rather than pinning a "
            "choice on a coin flip."
        ),
        "data": (
            "46% webcam, 16% logger. Well observed by the standards of this set, which is why "
            "its honest coverage holds up better than most."
        ),
        "gaps": [
            "Grand's base uncertainty is about 100 minutes and nothing has moved it. The two "
            "obvious neighbour signals — the Turban lattice and the Rift / West Triplet shifts "
            "— are real and both fail to improve the distribution; see “What did not work”.",
            "The live card's Turban and West Triplet notes claim only that observers are on "
            "station, deliberately. Their apparent 79% / 60% hit rates are logging bias: "
            "people log Turban when they are already sitting at Grand near due time.",
        ],
    },
    "Daisy": {
        "summary": "The tightest geyser served, and the one with a known, unbuilt improvement.",
        "why": (
            "Daisy's interval is so regular that model choice barely registers — the whole "
            "leaderboard sits within a few tenths of a minute — so it keeps the default. What "
            "matters here is the data cleaning: Daisy's median drifted from 142 minutes in "
            "2019 to 111 in 2026, and that drift is what generation 2 of the validity filter "
            "exists for."
        ),
        "data": "58% webcam, 24% logger — one of the better-observed geysers in the set.",
        "gaps": [
            "**Wind is real, large, and not implemented — the single biggest known gap in the "
            "project.** Published work found 8 of 11 wind storms pushed Daisy's interval past "
            "+1σ, modelling a swing from 135 min at 2 m/s to 180 min at 8 m/s, and the effect "
            "was independently replicated at Strokkur over ~650,000 eruptions. Our own data "
            "agrees: a 19.4-minute seasonal swing, longest in February and shortest in August, "
            "which is about 4.5× Daisy's current MAE. The data-source decision is already made "
            "(ERA5 for training, HRRR for live inference); the model is not built.",
        ],
    },
    "Riverside": {
        "summary": "Regular, and badly hurt by observation gaps.",
        "why": (
            "A flat leaderboard: the winner and the default are separated by less than the "
            "noise margin, so it keeps the default."
        ),
        "data": "62% webcam, 22% logged while in eruption.",
        "gaps": [
            "Riverside's **overflow** is a genuine precursor that gazers use and this project "
            "cannot see: it is recorded in GeyserTimes *notes*, and the public API exposes "
            "entries, predictions, geysers and users only. A notes endpoint would unlock a "
            "real nowcast here, and is the standing ask in the collaboration conversation with "
            "GeyserTimes.",
        ],
    },
    "Castle": {
        "summary": "The largest single gain in the project, and still the wrong distribution shape.",
        "why": (
            "Castle has two processes. An eruption that fails to reach the steam phase is "
            "logged as a minor, and the interval after it runs about 375 minutes against about "
            "1081 after a major — a threefold separation. Branching on the flag more than "
            "halves the error. Note that this gain only became visible after the validity "
            "filter was fixed to compute its baseline per regime; before that, the filter was "
            "deleting the entire short mode and the two branches looked identical."
        ),
        "data": "49% webcam, 20% logger.",
        "gaps": [
            "Nothing models the chain of behaviour beyond the single minor/major flag — a "
            "series of minors, or the recovery after one, is not represented.",
        ],
    },
    "Great Fountain": {
        "summary": "Well instrumented, poorly observed, and the honest numbers show it.",
        "why": (
            "The leaderboard is flat to within a rounding error, so it keeps the default. The "
            "one model built specifically for it — conditioning on whether the entry came from "
            "an electronic logger — does not help, and is reported rather than dropped."
        ),
        "data": (
            "63% of valid intervals are logger-recorded and none from the webcam. The loggers "
            "are genuinely more complete than human observation, with no timestamp offset; "
            "what they cannot fix is the stretches when no logger transcription happens."
        ),
        "gaps": [
            "Great Fountain's **overflow** is the other precursor living in GeyserTimes notes "
            "that the public API does not expose. Together with Riverside, these are the two "
            "signals most likely to produce another Indicator-grade nowcast.",
        ],
    },
    "Beehive": {
        "summary": "The worst interval model in the set, carrying the best live signal in the set.",
        "why": (
            "Beehive's interval genuinely is that uncertain: the dashboard-style rolling "
            "baseline is the leaderboard winner, and every more elaborate model is within a few "
            "percent of it. What changes the answer here is not the interval model at all — it "
            "is the Indicator."
        ),
        "data": (
            "48% webcam, and only 8% of raw gaps are rejected by the validity filter — the "
            "cleanest observation record of the frontcountry geysers, because Beehive is "
            "watched."
        ),
        "gaps": [
            "Outside the Indicator window nothing helps: conditioning is measured at ±0.0% "
            "there, which is the property that makes it safe to ship but also means a Beehive "
            "card hours out is honestly just a wide distribution.",
            "Residual error inside the Indicator window is dominated by cycles where the "
            "Beehive eruption itself was never logged (~6% of Indicator entries), not by the "
            "model.",
            "Neither the NPS nor Geysers.net predicts Beehive, so there is no third-party "
            "comparison to be had on the scoreboard.",
        ],
    },
    "Fountain": {
        "summary": "Added by a sweep of all 491 logged geysers; nobody else predicts it.",
        "why": (
            "Selected on interval tightness and logging density — log-sd 0.207 on a 305-minute "
            "median, the same tier as Castle and Beehive — with a unimodal distribution and no "
            "drift. The leaderboard winner is ahead of the default but inside the noise margin, "
            "so it keeps the default."
        ),
        "data": "56% logger-recorded, no webcam coverage.",
        "gaps": [
            "**Morning is not modelled.** When Morning is active the two geysers interact and "
            "Fountain's intervals shift; the current models will simply see wider scatter. "
            "Nobody should be surprised if a future Morning active phase makes Fountain's live "
            "numbers sag until that conditioning is added.",
            "Overnight logging at Fountain Paint Pots is sparse, which is why its honest "
            "coverage is the worst in the set: a gazer scoring this card against every raw "
            "interval will see misses at nearly Great Fountain rates. More overnight entries "
            "would move this number; no model change will.",
        ],
    },
    "Lion": {
        "summary": "The series geyser: two modes, and a distribution shape that is still wrong.",
        "why": (
            "Lion erupts in series — an initial eruption flagged by the observer, one to five "
            "more at ~83-minute spacing, then 7.8–14.7 hours of quiet. The served model "
            "branches on that initial flag and fits each branch as a two-component mixture "
            "weighted by the branch's measured continue rate: about 82% continue after an "
            "initial, about 40% after a later eruption. It is decisively ahead of the default, "
            "so it is pinned."
        ),
        "data": (
            "70% webcam. Only 11% of raw gaps are rejected — second cleanest in the set — but "
            "only because the validity filter was taught to recognise series gaps as real. "
            "Before that fix it was deleting all 7,410 of them."
        ),
        "gaps": [
            "The nominal 50% interval is far too wide, and the honest 50% is poor for a "
            "structural reason rather than a fixable calibration one: with a bimodal spread the "
            "central band often straddles the empty valley between the two modes. A better "
            "answer here is a different shape of interval, not a wider or narrower one.",
            "The unconditional models cover only ~33% of their nominal 50% here, which is worth "
            "seeing on the leaderboard below: a unimodal fit cannot cover a bimodal reality no "
            "matter how far it stretches.",
        ],
    },
    "Artemisia": {
        "summary": "One eruption a day, logger-backed, and the default wins outright.",
        "why": (
            "The default is the leaderboard winner here, with the next four models within a few "
            "percent, so there is nothing to pin. Artemisia was selected on log-sd 0.208 across "
            "a ~22-hour median, cleanly unimodal in log space."
        ),
        "data": (
            "36% logger-recorded, which keeps its share of valid intervals high around the "
            "clock — observationally better than Fountain despite erupting once a day."
        ),
        "gaps": [
            "Neither the NPS nor Geysers.net predicts Artemisia, and one eruption a day means "
            "the scoreboard accumulates slowly. Be patient before judging its live numbers.",
        ],
    },
    "Lone Star": {
        "summary": "More regular than most of what is served here, and usually unpredictable anyway.",
        "why": (
            "The geyser is not the problem: tight-mode log-sd 0.147 on a 164-minute median. The "
            "**observation channel** is. Lone Star is backcountry with no cell coverage, so only "
            "26% of entries reach GeyserTimes within 30 minutes and the median entry latency is "
            "2.7 hours — one full interval. Sampled at random moments, the newest logged "
            "eruption is a median of 206 hours old."
        ),
        "data": (
            "No webcam, no logger, 16% of entries flagged approximate. Its minors are precursors "
            "~37 minutes before the major and are excluded from the interval chain."
        ),
        "gaps": [
            "A late report still carries a correct eruption time, and it still does not save the "
            "countdown: measured against pure phase extrapolation the 90% band is ±45 min one "
            "interval out, ±69 at two, and indistinguishable from no information at three. Phase "
            "decoheres faster than reports arrive.",
            "**One electronic logger would fix this geyser completely.** Latency goes to zero "
            "and the card is simply live all the time.",
        ],
    },
    "Till": {
        "summary": "The tightest long-interval geyser here, and it was hiding inside a filter bug.",
        "why": (
            "Till is pinned to the adaptive model because its cycle has drifted across the "
            "record — 613 minutes all-time against about 729 now — and the long-window "
            "parametric fits train on the old regime, which costs more than half its accuracy. "
            "Its 12-hour cycle at log-sd 0.072 only appeared once afterplay minors, which make "
            "up 297 of its 477 recent entries, were taken out of the interval chain."
        ),
        "data": (
            "59% logger-recorded, and reports arrive in real time (median latency ~1 minute). "
            "Its phase carries far enough — ±78 min at one cycle, ±183 at eight — that the card "
            "stays live for about four days after any visit."
        ),
        "gaps": [
            "The card is live only because somebody walked past recently. In a quiet week it "
            "falls back to planning information, and that is a statement about visitation "
            "rather than about the geyser, which is unusually regular.",
            "Only 69 evaluations sit behind its backtest numbers. Treat them as provisional.",
        ],
    },
    "Little Squirt": {
        "summary": "A ~58-hour cycle everybody had overlooked, with the cleanest record in the set.",
        "why": (
            "No special machinery was needed. The leaderboard winner is ahead of the default by "
            "less than the noise margin, so it keeps the default."
        ),
        "data": (
            "91% of raw gaps are true single intervals — the cleanest observation record here — "
            "with 53% of entries from the Old Faithful webcam and an anchor inside its window "
            "94% of the time."
        ),
        "gaps": [
            "Its all-time median is about 76 hours against ~58 now, so the cycle has drifted "
            "across the record and the training window matters more than the model does.",
            "Roughly a dozen scored eruptions a month. The scoreboard will take a season to say "
            "anything about it.",
        ],
    },
    "Steamboat": {
        "mode": "context",
        "summary": "The world's tallest active geyser, and the one card here that names no time.",
        "why": (
            "Steamboat gives no reliable warning. Its majors run weeks to months apart with no "
            "precursor anyone can act on, so the card shows what is actually known — days since "
            "the last major and the recent interval range — and states plainly that nobody can "
            "honestly predict it. That statement is the product. It is never logged to the "
            "scoreboard ledger, because there is no claim to score."
        ),
        "data": (
            "Eruption times come from GeyserTimes as for every other geyser. The seismic watch "
            "reads WY.YNM at the Norris Museum from EarthScope's open FDSN service."
        ),
        "gaps": [
            "The seismic watch is season-aware and deliberately narrow: full-day in the shoulder "
            "seasons, night-gated in summer, and **suspended during the oversnow season**, when "
            "snowcoaches, groomers and the warming hut put vehicles on top of the seismometer "
            "and produce signals that overlap real eruptions completely.",
            "It detected 4 of 13 eruptions in validation — n=4, and the station itself was dark "
            "at 4 of those 13 eruption times — so **silence means no information**, never "
            "“nothing happened”. A detection is reported as “consistent with a major eruption, "
            "awaiting observer confirmation”, never as certainty.",
            "Winter coverage needs spectral discrimination rather than amplitude. That is a real "
            "research project with a labeled dataset behind it, not a threshold tweak.",
        ],
    },
}

# ─────────────────────────────────────────────────────────────────────────────
# Where the work is, in priority order. This is the section the whole page
# exists for: the things a gazer or a researcher could actually move.
# ─────────────────────────────────────────────────────────────────────────────
OPEN_GAPS: list[dict[str, str]] = [
    {
        "title": "Implement the Daisy wind model",
        "who": "Anyone comfortable with gridded weather data",
        "text": (
            "The only external forcing that clears the bar, at roughly 4.5× Daisy's current "
            "MAE, replicated independently at Strokkur. The data-source decision is already "
            "made — ERA5 for training and backtesting, HRRR at 15-minute resolution for live "
            "inference — chosen on archive depth rather than forecast skill."
        ),
    },
    {
        "title": "A notes endpoint on the GeyserTimes API",
        "who": "GeyserTimes, and anyone who wants to make that case with us",
        "text": (
            "Riverside and Great Fountain **overflow** are the two signals most likely to "
            "produce another Indicator-grade nowcast, and both live in GeyserTimes notes, which "
            "the public API does not expose. Everything else needed is already built."
        ),
    },
    {
        "title": "Fix the shape of the Castle and Lion distributions",
        "who": "Statisticians",
        "text": (
            "Both serve conditional models that halve the error and still cover far more than "
            "their nominal 50%. That is the wrong distribution shape rather than the wrong "
            "width, and on Lion it is structural: a central band across a bimodal spread lands "
            "in the empty valley between the modes. Highest-density regions instead of central "
            "intervals is the obvious first thing to try, and nobody has tried it."
        ),
    },
    {
        "title": "More eyes, or a logger, beats a better model",
        "who": "Gazers, and anyone placing instruments",
        "text": (
            "Where honest coverage is poor the cause is observation, not modelling. One "
            "electronic logger at Lone Star would take its report latency from 2.7 hours to "
            "zero and make its card live permanently. Overnight logging at Fountain Paint Pots "
            "is the difference between a 48% honest band and an 88% one. Logging a geyser "
            "nobody is watching is worth more to this project than any model change on the "
            "backlog."
        ),
    },
    {
        "title": "Steamboat in winter",
        "who": "Volcano seismologists",
        "text": (
            "Amplitude-based detection is a measured dead end in the oversnow season, because "
            "the noise source is co-located with the station. Spectral discrimination against a "
            "labeled dataset is the only path anyone has identified, and the labeled dataset "
            "does not exist yet."
        ),
    },
    {
        "title": "Sawmill, and the regime-switching family",
        "who": "Modellers",
        "text": (
            "Sawmill in its cycling regime is steadier than several geysers served here — "
            "tight-mode log-sd 0.251 at a 165-minute cycle — but only 41% of its gaps sit in "
            "that regime, and much of the rest is real behaviour (deep drains, marathon phases) "
            "rather than missed logging. It needs a regime detector feeding a conditional "
            "interval model: Lion's family, one rung harder."
        ),
    },
    {
        "title": "Add earth tides as a negative control",
        "who": "Anyone touching model selection here",
        "text": (
            "The tidal claim was rebutted in print and is superseded, which makes a tidal series "
            "an almost free leakage detector: feed it into any model-selection procedure, and if "
            "the procedure assigns it non-trivial importance, that is evidence of overfitting in "
            "the procedure rather than a discovery."
        ),
    },
    {
        "title": "Tell us where the cards are wrong",
        "who": "Gazers, above all",
        "text": (
            "The most valuable report is a card that was confidently wrong when you were "
            "standing there — which geyser, what it said, what actually happened. The live "
            "scoreboard has already caught three serving bugs the backtest could never have "
            "found, and every one of them started as a number that looked wrong to somebody "
            "watching. **There is a form at the bottom of this page**, and it needs no account "
            "of any kind."
        ),
    },
]

LINKS: list[dict[str, str]] = [
    {
        "label": "Source code",
        "url": REPO_URL,
        "text": "MIT-licensed. The models, the filter SQL, and this page's own text.",
    },
    {
        "label": "Full calibration report",
        "url": REPORT_URL,
        "text": "Every model on every geyser, with figures. Regenerate it with `geyser-ai backtest`.",
    },
    {
        "label": "Findings",
        "url": f"{FINDINGS_URL}/README.md",
        "text": "The durable write-up: data quality, model results, external forcings, seismic.",
    },
    {
        "label": "Issue tracker",
        "url": ISSUES_URL,
        "text": (
            "For anyone who works this way. If you do not have a GitHub account — which is "
            "most gazers — use the form at the bottom of this page instead; it goes to the "
            "same person."
        ),
    },
    {
        "label": "GeyserTimes",
        "url": "https://geysertimes.org",
        "text": "Every eruption time here was recorded by a volunteer through GeyserTimes.",
    },
]


@lru_cache(maxsize=1)
def load_calibration() -> dict[str, Any]:
    """The backtest artifact, or an empty shell if it is missing.

    A missing artifact must degrade to prose rather than break the page: the
    method endpoint is served by the same container as the predictions.
    """
    try:
        return json.loads(CALIBRATION_PATH.read_text())
    except Exception:
        return {"geysers": {}, "nowcast": {}}


def _winner_ahead_pct(served: float, best: float) -> float | None:
    """How much CRPS the leaderboard winner would save, as the docs state it.

    Expressed against the served model's own score -- "the winner is 5.6% ahead
    of what we serve" -- which is the framing `docs/findings/model-results.md`
    uses and the one the noise rule is written in.
    """
    if not served:
        return None
    return round(100.0 * (served - best) / served, 1)


def _beats_baseline_pct(served: float, baseline: float) -> int | None:
    """Improvement over the dashboard-style rolling baseline, positive is better.

    Whole percent deliberately: it is derived from published one-decimal CRPS
    values, and a tenth of a point here would be false precision.
    """
    if not baseline:
        return None
    return round(100.0 * (baseline - served) / baseline)


def _calibration_flags(row: dict[str, Any]) -> list[str]:
    """Miscalibration worth stating, by the same rule the report uses."""
    out = []
    if abs(row["cov50"] - 0.50) > 0.10:
        direction = "far too wide" if row["cov50"] > 0.50 else "overconfident"
        out.append(
            f"The nominal 50% interval actually covers {row['cov50']:.0%}, which makes it "
            f"{direction}: the predicted distribution is the wrong shape, not merely the "
            "wrong width."
        )
    if abs(row["cov90"] - 0.90) > 0.07:
        direction = "wider than it claims" if row["cov90"] > 0.90 else "overconfident"
        out.append(
            f"The nominal 90% interval covers {row['cov90']:.0%} — {direction} at the level "
            "the dashboard's outer band is drawn from."
        )
    return out


def _geyser_block(name: str, cal: dict[str, Any]) -> dict[str, Any]:
    """One geyser's complete working: curated prose plus measured numbers."""
    notes = GEYSER_NOTES.get(name, {})
    data = cal.get("geysers", {}).get(name, {})
    board = sorted(data.get("models", []), key=lambda r: r["crps"])

    mode = notes.get("mode")
    if mode is None:
        mode = "phase-limited" if name in PHASE_WINDOW_CYCLES else "live"

    block: dict[str, Any] = {
        "geyser": name,
        "slug": name.lower().replace(" ", "-").replace("'", ""),
        "mode": mode,
        "summary": notes.get("summary", ""),
        "why": notes.get("why", ""),
        "data": notes.get("data", ""),
        "leaderboard": board,
        "honest": data.get("honest"),
        "entry_mix": data.get("entry_mix"),
        "nowcast": cal.get("nowcast", {}).get(name),
        "gaps": list(notes.get("gaps", [])),
    }

    if mode == "phase-limited":
        block["phase_window_cycles"] = PHASE_WINDOW_CYCLES[name]

    if not board:
        # Steamboat, and any geyser added before its first backtest.
        block["served"] = None
        return block

    served_name = default_model_name(name)
    served = next((r for r in board if r["model"] == served_name), None)
    best = board[0]
    baseline = next((r for r in board if r["model"] == "rolling_normal"), None)

    block["served"] = {
        "model": served_name,
        "label": MODEL_ROSTER.get(served_name, {}).get("label", served_name),
        "what": MODEL_ROSTER.get(served_name, {}).get("what", ""),
        "pinned": name in BEST_MODEL_BY_GEYSER,
        "rank": board.index(served) + 1 if served else None,
        "of": len(board),
        "metrics": served,
    }
    block["best"] = {"model": best["model"], "crps": best["crps"]}
    # What the noise rule costs, stated rather than implied: how much CRPS the
    # served model gives up against the leaderboard winner, and how it stands
    # against the dashboard-style baseline gazers already have.
    if served:
        block["served"]["winner_ahead_pct"] = _winner_ahead_pct(served["crps"], best["crps"])
        if baseline:
            block["served"]["beats_baseline_pct"] = _beats_baseline_pct(
                served["crps"], baseline["crps"]
            )
        block["gaps"] = _calibration_flags(served) + block["gaps"]
        honest = block["honest"]
        filtered_ref = next((r for r in board if r["model"] == "lognormal"), None)
        if honest and filtered_ref:
            block["honest"] = {
                **honest,
                "cov90_filtered": filtered_ref["cov90"],
                "cost_points": round(100 * (filtered_ref["cov90"] - honest["cov90"]), 1),
            }
    return block


def get_method(geyser: str | None = None) -> dict[str, Any]:
    """The whole methodology, or one geyser's slice of it.

    Static by construction: no database, no clock, no network. It describes how
    the served models were built and how they scored, which changes when the
    backtest is regenerated and not before.
    """
    cal = load_calibration()
    names = [geyser] if geyser else [*TARGET_GEYSERS, "Steamboat"]
    blocks = [_geyser_block(n, cal) for n in names]
    if geyser:
        return {"geyser": blocks[0], "calibration": _cal_meta(cal)}
    return {
        "calibration": _cal_meta(cal),
        "overview": OVERVIEW,
        "sections": SECTIONS,
        "models": MODEL_ROSTER,
        "geysers": blocks,
        "open_gaps": OPEN_GAPS,
        "links": LINKS,
        "noise_margin_pct": NOISE_MARGIN_PCT,
    }


def _cal_meta(cal: dict[str, Any]) -> dict[str, Any]:
    return {
        "generated": cal.get("generated"),
        "backtest_years": cal.get("backtest_years"),
        "intervals": cal.get("intervals"),
        "report_url": REPORT_URL,
    }
