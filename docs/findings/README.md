# Findings

The durable record of what this project has learned, so nobody has to dig it out
of a git log or a chat transcript twice.

Everything here is written for a future maintainer or a collaborator who did not
live through it. Two rules the whole set follows:

1. **Every claim carries its numbers.** "Improved the prediction" is not a
   finding; "CRPS 110.5 → 77.2, honest 90% coverage 60.5% → 73.2%" is.
2. **Negative results are documented as prominently as wins**, and are not
   quietly deleted when they stop being interesting. Several of the most useful
   things in here are things that *did not work* — the Turban lattice, the
   covariate survival model, earth tides, rainfall, entry-type conditioning.
   Knowing they were tried and failed, with the numbers, is worth as much as the
   things that worked, because it stops the next person spending a week on them.

| Document | What is in it |
|---|---|
| [data-quality.md](data-quality.md) | The GeyserTimes archive and API as they actually behave: the four generations of the interval validity filter, the traps in the data, the API quirks |
| [model-results.md](model-results.md) | Every model tried, what won, what lost, why production serves what it serves, and the leakage bug that made a model look good |
| [external-forcings.md](external-forcings.md) | Whether weather, tides, rainfall, earthquakes and hydrology move eruption intervals. Mostly: no. Literature synthesis plus our own in-database interaction work |
| [engineering-notes.md](engineering-notes.md) | Deploying a Python/DuckDB/SciPy stack on Cloudflare Containers, and the things that cost hours |

## The one-paragraph summary

Cleaning the data has beaten modelling at every single step. Four successive
fixes to the interval validity filter each moved the headline metrics more than
any model change ever has, and the largest single improvement available at any
point came from noticing the filter was *deleting* real data. The best covariate
in the project — the `minor` flag — was already in the archive, recorded by
volunteers. The most sophisticated model, a survival regression with covariates,
finished in the bottom half on all seven geysers. Of the external forcings the
literature proposes, exactly one is real and large enough to matter for
prediction (wind on Daisy), and we have not implemented it yet.

## Conventions

- **CRPS** is in minutes, lower is better. It scores the whole predicted
  distribution, not just the point estimate, which is the only fair way to
  compare a distribution against a point-plus-window.
- **Coverage** is the share of eruptions that landed inside a nominal interval.
  A well-calibrated 90% interval catches 90% — *more* is not better, it means
  the model is under-confident.
- **"Honest coverage"** scores against *every* interval including the ones the
  validity filter rejects. It is always worse than the headline, and it is the
  number that describes what a gazer on a boardwalk actually experiences.
- CRPS is **not comparable across filter generations**, because changing the
  filter changes which intervals are in the evaluation set. Coverage is the
  meaningful cross-version signal. This trips people up; see
  [data-quality.md](data-quality.md).
