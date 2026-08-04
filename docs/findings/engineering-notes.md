# Engineering notes: running this on Cloudflare

Deploying a Python + DuckDB + SciPy + lifelines stack on Cloudflare Containers.
Everything here cost real time to find. Architecture rationale lives in the
README's Deployment section; this is the list of things that surprised us.

## The big one: container CPU is ~30–40× slower than you expect

A `/api/predictions` call takes **0.68 s** in a local Docker container pinned to
an *enforced* 0.5 CPU. The identical image on Cloudflare `standard-1` (also 0.5
vCPU) takes **20–40 s**.

Things that were measured and did **not** explain it:

- **Not CPU share.** `basic` (1/4 vCPU) and `standard-1` (1/2 vCPU) performed the
  same. Doubling the vCPU bought nothing.
- **Not memory.** `standard-1` has 4 GiB against `basic`'s 1 GiB. No change. Peak
  RSS serving predictions is ~450 MB.
- **Not I/O.** `/api/stats` (DuckDB aggregates) runs in 0.5–0.9 s; the gap is
  specific to the scalar NumPy/SciPy work in the models.
- **Not BLAS thread oversubscription.** Pinning `OMP_NUM_THREADS`,
  `OPENBLAS_NUM_THREADS`, `MKL_NUM_THREADS` etc. to 1 made it *worse* in the one
  measurement taken (23 s → 36 s), so the pinning was removed.

**Conclusion:** Cloudflare's container CPU is simply much slower for
single-threaded scientific Python than a modern laptop core, well beyond the
nominal vCPU fraction. Plan for it.

**What we did instead of fighting it:** the Worker caches responses in the
container's Durable Object storage and recomputes off the request path. Readers
get **~150 ms**; the 20–40 s recompute happens on a cron. This is the single most
important architectural consequence of the finding.

**Verify with:** `/api/stats` should be fast and `/api/predictions` slow. If both
are slow, it is something else.

## `ctx.waitUntil()` is cancelled — you cannot use it for slow background work

The obvious stale-while-revalidate implementation — serve the cached response,
refresh in `ctx.waitUntil()` — **does not work** when the refresh is slow.
Cloudflare logs:

```
waitUntil() tasks did not complete within the allowed time after invocation
end and have been cancelled.
```

A 20 s container fetch exceeds the post-response budget, so the cache never
refreshed and its age grew without bound. Symptom: `x-geyser-cache-age` climbing
past the freshness window forever.

**Fix:** a **cron trigger** does the recompute. Cron handlers get a far more
generous wall-clock budget.

## Durable Object writes are not free on a hot read path

Recording "somebody asked for this endpoint" with a `storage.put()` on **every**
`readCache` took cached responses from **under 100 ms to 3–13 s**. A durable
write queues behind whatever else the DO is doing — and this DO is also running a
20–40 s container fetch on the cron.

**Fix:** throttle the write to at most once per minute per key. Reads stay pure
reads in the common case. Back to ~150 ms.

**General rule:** on a DO that also owns a container, treat any storage *write*
on the request path as expensive.

## Cache API does not work on `workers.dev`

`caches.default` is a no-op unless the Worker is on a custom domain. The docs say
this in passing and it is easy to miss. We use Durable Object storage instead.
Don't write cache code that silently does nothing.

## Uploads over ~50 MB fail

Two separate symptoms, same root cause:

- `wrangler r2 object put` on a 208 MB file → `fetch failed` after ~3 s.
  **5 MB objects succeed every time; 50 MB and up never do.**
- `wrangler deploy` image push → `use of closed network connection` mid-blob.

Both are network-path failures on large request bodies, not Cloudflare API
errors, and both are intermittent across days.

**Fix for R2:** `deploy/publish-snapshot.sh` splits the snapshot into **8 MB
parts**, uploads each with its own retry, and writes the **manifest last** — so a
publish that dies halfway never leaves a container assembling a half-written
database, and re-running the script resumes it. The container reads the manifest,
streams the parts in order, and **verifies the assembled byte count against the
manifest before renaming**. It falls back to the whole-object key if no manifest
exists.

**Fix for image pushes:** retry. Layers already in the registry are skipped, so
retries get cheaper. Budget several attempts.

## `ghcr.io` is a transient dependency of every deploy

The Dockerfile pulls `uv` from `ghcr.io/astral-sh/uv`. Deploys have failed with:

```
dialing ghcr.io:443 ... connect: network is unreachable
```

Nothing to fix — retry. But know that a failed deploy is often not your code.

## Container startup: you get ~20 s to bind a port

The snapshot takes longer than that to download, so `deploy/entrypoint.py`
**starts uvicorn immediately and downloads on a background thread**. Until the
file lands the API returns its existing "no database" 503, the Worker holds the
request through the container's `onStart` hook (bounded at 25 s, because
`blockConcurrencyWhile` cannot be held indefinitely), and anything still early
gets a self-refreshing warming page.

Measured: snapshot pull ~6 s, port listening ~10 s from cold at 1/4 vCPU.

## `outboundByHost` must be assigned *after* the class body

```ts
// WRONG — a static class field shadows the base class's accessor,
// the setter never runs, and the handler is silently never registered.
class GeyserContainer extends Container { static outboundByHost = {...} }

// RIGHT
GeyserContainer.outboundByHost = {...};
```

`Container.outboundByHost` is a static **accessor** that registers handlers in a
module-level registry. With `useDefineForClassFields` (TypeScript targeting
ES2022, the default) a `static` field creates an own data property and bypasses
the setter. Everything type-checks; the handler simply never fires and the
container's request goes to the real internet instead.

## Outbound interception is how a container reaches bindings

The container holds **no R2 credentials**. It fetches
`http://geyser-snapshot.r2/...` and the Worker intercepts that hostname with an
`outboundByHost` handler that answers from its R2 binding, inside the Workers
runtime. Writes are refused outside a `ledger/` prefix so an application bug
cannot overwrite the 200 MB eruption archive underneath itself.

Only **plain HTTP** is intercepted. `interceptHttps` stays `false`, so the
container's HTTPS calls to geysertimes.org go straight out with the app's own
User-Agent — no CA trust dance, and the politeness guarantees are untouched.

## Rolling deploys mean two versions run at once

Container deploys roll gradually. For 1–2 minutes after `wrangler deploy`, some
requests hit the previous image. Observed consequences:

- New Worker code calling a DO RPC method the **old** container class does not
  have → transient 502s.
- New API query parameters → transient 422s.

Both self-resolve. When verifying a deploy, poll for a marker that only the new
version produces rather than assuming the first 200 means it landed. We used the
Castle valid-interval count (16,327 → 16,856) — it took **~5 minutes**.

## The image must be slim, and `uv`'s cache will bloat it

First build: **3.63 GB**, over `basic`'s 4 GB instance disk. Two causes:

1. `uv`'s cache landing in an image layer. Fix: `UV_CACHE_DIR` on a BuildKit
   cache mount, so it never enters a layer.
2. `chown -R` on the venv duplicating ~900 MB into a new layer. Fix: create the
   user first and build as that user; multi-stage, copying only `/app/.venv` and
   `/app/src` into the runtime image.

Final: **1.38 GB**. Note `uv sync` installs the project **editable**, so the
runtime stage must copy `src/` as well as the venv.

## Snapshot changes are not live until the snapshot is republished

The container reads `intervals` from the DuckDB snapshot in R2. A change to the
ingest SQL — the per-regime validity filter, for instance — does **nothing** in
production until `./deploy/publish-snapshot.sh` has run **and** a container has
cold-started onto it. This is easy to forget and produces a very confusing "I
deployed it and nothing changed".

## Cost

At `basic` (1/4 vCPU, 1 GiB, 4 GB disk) with a 5-minute cron running around the
clock, so the container never sleeps: **≈ $8/month** on top of the $5 Workers
Paid plan — roughly $6.35 memory, $0.85 CPU, $0.69 disk, after the included
25 GiB-hours, 375 vCPU-minutes and 200 GB-hours.

Memory and disk are billed on **provisioned** resources for as long as the
container is running; CPU is billed on **active use**. So keeping it awake is
mostly a memory bill, and halving the cron interval would roughly double only the
CPU line.
