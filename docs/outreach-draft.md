# DRAFT — Outreach email to GeyserTimes (do not send without David's review)

**To:** support@geysertimes.org
**From:** David Kingham <david@exploringexposure.com>
**Subject:** Open-source prediction layer for GeyserTimes data — plus a few data-quality findings

Hi GeyserTimes team,

My name is David Kingham. I'm a photographer and a longtime admirer of what your
organization and the gazer community have built — and of the chat.geysertimes.org
dashboard that a lot of us keep open all day.

I've been building an open-source analysis layer on top of the public archive:
probabilistic eruption predictions, backtested and scored so anyone can check
whether they actually beat existing methods. Before I ask you anything, here are
the things I found that seem useful to *you*, whatever happens with the rest.

## 1. Findings in the archive that may be worth your attention

**Missed eruptions show up as clean harmonics.** Plotting interval histograms,
several geysers have sharp secondary peaks at almost exactly 2× and 3× the
median — Riverside clusters near 390, 780 and 1150 minutes; Great Fountain near
686 and 1400. Those aren't long intervals, they're one and two eruptions nobody
logged. Anyone computing statistics straight from consecutive entries is
silently mixing them in. Filtering them out improved my prediction error by
20–87% depending on the geyser — far more than any modelling change I made.

**Interval drift breaks fixed thresholds.** Daisy's median interval has moved
from about 142 minutes in 2019 to 111 in 2026. Any validity rule based on a
single all-time median is therefore calibrated to the old era, and lets doubles
of the *current* interval through. A rolling local baseline fixes it.

**But a local median can validate its own contamination.** Where observing is
thin, missed eruptions are the *majority* of recorded gaps, so a local median
drifts up to the doubled value and then blesses it — Great Fountain's ran to
1361 against a true interval near 690. What works is a low quantile (I use a
rolling 25th percentile) as the anchor, because a missed eruption only ever
*adds* time, never subtracts it.

**Castle needs two baselines, not one — and this one bit me hard.** Every rule
above still assumes a geyser has a single interval distribution. Castle doesn't:
the interval after a **minor** is a physically different, much shorter process.
With one pooled baseline the threshold gets set by the ~1000-minute post-major
mode, and the lower bound then lands *above the entire short mode* and deletes
it as if it were two observers logging the same eruption. In my data that was
103 post-minor intervals under 400 minutes, and **not one of them survived**. The
filter had quietly taught my model that a minor is followed by a *longer* wait
than a major — the exact opposite of the truth — and it cost me two badly wrong
public predictions before I found it.

Computing the baseline separately for post-minor and post-major anchors fixed
it: Castle's typical error fell from 152 to 101 minutes, and its real-world 90%
coverage went from 61% to 73%. The general lesson, which may matter to anyone
computing statistics off the archive: **a validity threshold assumes unimodality,
and the `minor` flag tells you when that assumption is false.**

**Two small things for anyone writing a client:**
- `associated_primaryID` is self-referential for primary eruptions rather than
  NULL, so the intuitive `IS NULL` filter returns zero rows. Cost me a while.
- Eruption epochs are negative for historical records (the archive reaches back
  well before 1970), so `epoch > 0` silently drops them.

**One possible API bug:** on `/api/v5/entries_recent`, the lookback is a path
segment (`/entries_recent/180`). Every *wrong* shape I tried — query parameters
like `?minutes=180`, `?count=5`, and others — returns `200 OK` with
`{"status":"success","entries":[]}` rather than an error. An empty success looks
exactly like "no eruptions recently," so it's easy to conclude the endpoint is
dead when the URL is just wrong. A 400 would save people time.

**A gap I'd love to see closed: predictions are never archived.** `predictions_latest`
returns what is open right now, there's no date-ranged predictions route, and the
nightly archive holds eruptions and notes only. So the record of *what was
predicted* — by the Park Service, by Geysers.net, by anyone — evaporates the
moment it expires, while the record of what actually happened is kept forever.

That means nobody can retrospectively ask "how accurate have the visitor centre
predictions been?" — a question I'd expect gazers, and the Park Service, to find
genuinely interesting. It also means the only way to answer it is to start
logging today and wait, which is what I'm now doing (see below). If a nightly
`geysertimes_predictions_complete_*.tsv.gz` alongside the existing dumps is
cheap for you, it would turn a question that currently takes years into one
anybody could answer this afternoon. I'd happily do the work if it helps.

## 2. What the models do — and where they don't help

Walk-forward backtest over the last three years, each prediction using only data
from before the eruption it predicts:

| Geyser | Typical error | 90% window holds | …scored on *all* intervals |
|---|---:|---:|---:|
| Old Faithful | 6 min | 93% | 76% |
| Daisy | 4 min | 89% | 70% |
| Riverside | 18 min | 91% | 58% |
| Grand | 54 min | 91% | 74% |
| Great Fountain | 63 min | 91% | 52% |
| Castle | 101 min | 87% | 73% |
| Beehive | 167 min | 87% | 80% |

Three honest caveats, because I'd rather you hear them from me:

- **The fancy model lost.** A survival model with covariates (previous interval,
  time of day, season, entry flags) ranked in the bottom half on every geyser
  tested. Simple rolling lognormal/Weibull fits beat it nearly everywhere, and
  a plain rolling mean ± window — essentially what the current dashboard shows —
  wins outright on Beehive. The gains here came from cleaning the data, not from
  clever statistics.
- **The most useful signal was already in your data.** The `minor` flag your
  observers record splits the next interval almost perfectly for Old Faithful
  (median 70 min after a minor, 102 after a full eruption), and once I stopped
  filtering Castle's short mode away it turned out to matter even more there —
  371 minutes after a minor against 1081 after a major. Conditioning on it
  roughly halves the error on both. That is your community's careful logging
  doing the work, not my statistics; every model I wrote that ignored the flag
  is worse than one that uses it.
- **That last column is the number that matters in the field.** The middle
  column scores only intervals that pass my quality filter. The right-hand
  column scores *every* interval, including the stretches where an eruption went
  unlogged — which a gazer on a boardwalk doesn't get to skip. Real-world
  reliability is meaningfully worse than the headline, and I'd rather publish
  both than just the flattering one.

## 3. What I could show you

Everything is open source, and it is now running live:

- A **mobile-first dashboard** — per-geyser probability curves on a shared time
  axis, so you can see at a glance which eruption is next and how confident it
  is. It keeps the spirit of the current dashboard; the width of each curve is
  the uncertainty, made deliberately hard to ignore.
- A small **JSON API**, and an **MCP server** so an AI assistant can answer
  "what's erupting soon?" against live data.
- The full **calibration report** — every model, every geyser, every score,
  including the ones that lost.

Happy to do a screen-share, or just send the repo and report. I'd genuinely
welcome you telling me something on it is wrong.

## 4. A scoreboard — and something I want to be upfront about

The obvious question about any prediction is "is it better than what's already
on the board?", so the live site now scores three predictors against the same
eruptions: mine, the **National Park Service** predictions that reach you from
the NPS/CartoDB system, and **Geysers.net**.

I want to flag this plainly rather than have you find it: I am comparing other
people's published predictions, on your platform, in public. So I have tried to
make it scrupulously fair, and I'd rather you tell me now if any of it is
unwelcome or unfair.

- Each source is scored **in the window it states itself**. The Park Service
  claims roughly ±12 minutes on Old Faithful and over two hours on Grand; mine
  states a 90% interval. Comparing "in-window rate" alone would simply reward
  whoever claims the widest window, so the median window width is shown beside
  the rate everywhere it appears.
- When a source re-predicts, only its **last prediction before the eruption** is
  scored. Superseded predictions are discarded, never counted as misses.
- Predictions for the eruption *after* next are excluded.
- Where an eruption lands implausibly late — almost always an unlogged eruption
  in between — the pairing is **dropped for every source alike**, because that
  measures observing coverage, not forecasting.
- `n` is displayed everywhere and is currently tiny, and anything under five
  eruptions is marked provisional. Nothing on that page is a result yet.

I am not trying to run a league table against the Park Service. The honest
finding so far is that on the geysers where a simple average interval works,
their predictions and Geysers.net's are **better than mine**, and my main
advantage is that I publish a distribution rather than a point. If that stays
true I'll say so just as loudly.

## Questions for you

- Is there interest in this becoming an official companion project under the
  GeyserTimes umbrella? I'd much rather build it *with* you than merely on top
  of you.
- Any guidance on data use, attribution, or infrastructure you'd like followed?
- Would the harmonics/drift/minor-mode findings be useful to whoever maintains
  the data quality side? I'm glad to write them up properly, or contribute the
  filter.
- Is the prediction scoreboard something you'd want, something you'd want
  changed, or something you'd rather I didn't publish? I'll follow your lead.
- Any chance of archiving predictions nightly, as in §1? It's the one piece of
  data that currently can't be recovered after the fact.
- Longer term: would you have any interest in computer-vision eruption logging
  from the public webcam (night and winter coverage), or LLM-assisted extraction
  of structured observations from historical records?

## On being a good guest

Exactly what I send you, so you can hold me to it:

- **One** archive snapshot download, cached and never re-fetched automatically.
  The deployed service never downloads the archive at all — it reads a copy I
  built on my laptop, so no amount of traffic to my site touches that endpoint.
- **Two API calls per five-minute cycle, around the clock**: one
  `entries_recent/{minutes}` for new eruptions, and one `predictions_latest` for
  the scoreboard. That's 24 requests an hour total, against your guidance of no
  more than one per minute *per URL* — so roughly a twentieth of what you permit.
- The scoreboard is why this runs continuously rather than only when someone is
  looking: predictions can only be scored against eruptions that have already
  happened, so pausing overnight would bias the comparison toward daytime.
- Exactly **one** instance ever runs. The deployment is capped at one container
  specifically so this can't quietly become N copies of the same polling loop.
- No crawling, no bulk pagination, no scheduled fetches beyond those two.

If any of that is more than you'd like, I will happily slow it down — tell me a
number and I'll use it. And if it's ever a problem, blocking me is completely
fine; I'd just appreciate a note so I can fix it rather than guess.

One note in return: your Anubis install challenges browser-like user agents but
passes plain ones, so my client identifies itself honestly as `geyser-ai` rather
than impersonating Chrome — that seemed the right thing to do, though I suspect
the effect is the opposite of what's intended.

Everything is open source, GeyserTimes is credited throughout, and I have no
commercial intent for this data.

If you're ever considering API additions, one endpoint would unlock real
value for visitors: **read access to notes**. Overflow reports for Riverside
and Great Fountain live in notes, and overflow-to-eruption is regular enough
that gazer-style predictions could be computed from them the way the NPS does
in the visitor center — the eruption entries alone can't support that. No
urgency and no expectation; it's simply the one thing the current API can't
provide that observers' own practice shows would matter.

Thank you for what you've built. The record itself is the achievement here; I'm
just reading it carefully.

Best,
David Kingham
david@exploringexposure.com
