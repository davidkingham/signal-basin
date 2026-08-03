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

## 2. What the models do — and where they don't help

Walk-forward backtest over the last three years, each prediction using only data
from before the eruption it predicts:

| Geyser | Typical error | 90% window holds | …scored on *all* intervals |
|---|---:|---:|---:|
| Old Faithful | 6 min | 94% | 76% |
| Daisy | 4 min | 87% | 69% |
| Riverside | 18 min | 91% | 59% |
| Grand | 54 min | 91% | 76% |
| Great Fountain | 63 min | 91% | 52% |
| Castle | 152 min | 88% | 61% |
| Beehive | 168 min | 87% | 80% |

Three honest caveats, because I'd rather you hear them from me:

- **The fancy model lost.** A survival model with covariates (previous interval,
  time of day, season, entry flags) ranked in the bottom half on all seven
  geysers. Simple rolling lognormal/Weibull fits beat it nearly everywhere, and
  a plain rolling mean ± window — essentially what the current dashboard shows —
  wins outright on Beehive. The gains here came from cleaning the data, not from
  clever statistics.
- **The most useful signal was already in your data.** For Old Faithful, the
  `minor` flag your observers record splits the next interval almost perfectly
  (median 70 min after a minor, 102 after a full eruption). Conditioning on it
  halved the error. That's your community's careful logging doing the work.
- **That last column is the number that matters in the field.** The middle
  column scores only intervals that pass my quality filter. The right-hand
  column scores *every* interval, including the stretches where an eruption went
  unlogged — which a gazer on a boardwalk doesn't get to skip. Real-world
  reliability is meaningfully worse than the headline, and I'd rather publish
  both than just the flattering one.

## 3. What I could show you

Everything is open source and runs locally today:

- A **mobile-first dashboard** — per-geyser probability curves on a shared time
  axis, so you can see at a glance which eruption is next and how confident it
  is. It keeps the spirit of the current dashboard; the width of each curve is
  the uncertainty, made deliberately hard to ignore.
- A small **JSON API**, and an **MCP server** so an AI assistant can answer
  "what's erupting soon?" against live data.
- The full **calibration report** — every model, every geyser, every score,
  including the ones that lost.

Happy to do a screen-share, or just send the repo and report.

## Questions for you

- Is there interest in this becoming an official companion project under the
  GeyserTimes umbrella? I'd much rather build it *with* you than merely on top
  of you.
- Any guidance on data use, attribution, or infrastructure you'd like followed?
- Would the harmonics/drift findings be useful to whoever maintains the data
  quality side? I'm glad to write them up properly, or contribute the filter.
- Longer term: would you have any interest in computer-vision eruption logging
  from the public webcam (night and winter coverage), or LLM-assisted extraction
  of structured observations from historical records?

## On being a good guest

I download one archive snapshot and cache it — never re-fetching automatically —
and top it up with a single `entries_recent` call on a five-minute cache, well
inside your once-a-minute guidance. No crawling, no bulk API pagination. One
note: your Anubis install challenges browser-like user agents but passes plain
ones, so my client identifies itself honestly as `geyser-ai` rather than
impersonating Chrome — that seemed the right thing to do, though I suspect the
effect is the opposite of what's intended.

Everything is open source, GeyserTimes is credited throughout, and I have no
commercial intent for this data.

Thank you for what you've built. The record itself is the achievement here; I'm
just reading it carefully.

Best,
David Kingham
david@exploringexposure.com
