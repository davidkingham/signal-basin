# DRAFT — Outreach email to GeyserTimes (do not send without David's review)

**To:** support@geysertimes.org
**From:** David Kingham <david@exploringexposure.com>
**Subject:** Proposal: an open-source AI/analysis layer for GeyserTimes data

Hi GeyserTimes team,

My name is David Kingham. I'm a photographer and longtime admirer of the work your
organization and the gazer community have put into building the most complete geyser
record anywhere — and of the chat.geysertimes.org dashboard that so many of us keep
open all day.

I've started building an open-source project that adds a modern analysis layer on top
of the GeyserTimes database, and I'd love to do it *with* you rather than merely on
top of your public API. The near-term pieces:

1. **Probabilistic predictions** — survival-analysis models that produce full
   probability curves (not just mean ± window) for the classic predictable geysers,
   backtested against history with published calibration scores so anyone can verify
   whether they beat existing methods.
2. **A modern, mobile-first companion dashboard** — keeping the spirit of the current
   one, adding live probability curves and smarter alerts for gazers in the park.
3. **Longer term:** computer-vision eruption logging from the public webcam (24/7
   winter/night coverage and archive backfill), and LLM-assisted extraction of
   structured observations from historical records.

I want to be respectful of your infrastructure and your community's culture: I'm
mirroring the archive dumps rather than hitting the API heavily, everything will be
open source, and I have no commercial intent for this data.

Questions for you:
- Is there interest in this becoming an official companion project under the
  GeyserTimes umbrella?
- Any guidance on data use, attribution, or infrastructure you'd like me to follow?
- Would historical chat logs ever be available for research use (observation mining)?

Happy to share the calibration results as soon as the first models are validated.
Thank you for everything you've built.

Best,
David Kingham
david@exploringexposure.com
