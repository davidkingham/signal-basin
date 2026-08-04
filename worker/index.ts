/**
 * Front door for the Geyser AI container.
 *
 * The Python/FastAPI + DuckDB stack cannot run on the Workers runtime, so the
 * app runs in a Cloudflare Container and this Worker is a thin proxy in front
 * of it. It does three things:
 *
 *  1. Routes every request to a single named container instance, so there is
 *     never more than one process polling GeyserTimes.
 *  2. Serves the DuckDB snapshot to the container from R2 through an outbound
 *     handler, so no R2 credentials ever exist inside the container image.
 *  3. Holds requests while a cold container downloads that snapshot, and shows
 *     a self-refreshing "warming up" page if it is still not ready.
 *  4. Caches the expensive JSON endpoints in Durable Object storage and
 *     refreshes them in the background, so a reader never waits on the models.
 */
import { Container, ContainerProxy, getContainer } from "@cloudflare/containers";

export { ContainerProxy };

/**
 * Virtual hostname the container fetches the snapshot from. Not a real DNS
 * name: requests to it are intercepted by `outboundByHost` below and answered
 * from R2 inside the Workers runtime.
 */
const SNAPSHOT_HOST = "geyser-snapshot.r2";
const SNAPSHOT_KEY = "geysertimes.duckdb";

/**
 * The container may write only under this prefix. The scoreboard ledger has to
 * survive restarts and the container's disk does not, so it PUTs the ledger
 * back through the same virtual host it reads the snapshot from -- but a bug in
 * the app must not be able to overwrite the 200 MB eruption archive underneath
 * itself, so writes outside `ledger/` are refused.
 */
const WRITABLE_PREFIX = "ledger/";
const LEDGER_KEY = "ledger/predictions.json";

/** One instance, always. Multiple instances would multiply the GeyserTimes poll rate. */
const SINGLETON = "geyser-ai";

/** How long a cold start may block a request while the snapshot lands. */
const WARMUP_BUDGET_MS = 25_000;
const WARMUP_POLL_MS = 750;

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

/**
 * Response cache.
 *
 * A prediction request costs the container roughly twenty seconds: it refits
 * per-geyser interval models and simulates renewal paths on a quarter of a
 * vCPU. Nothing about that answer changes second to second -- the underlying
 * data only moves when the five-minute GeyserTimes sync brings in new entries --
 * so readers are served the last computed answer and the recompute happens off
 * the request path, on the cron trigger below.
 *
 * Storage is the container's own Durable Object. Reading it does not start the
 * container, so a cache hit costs nothing and leaves a sleeping instance
 * asleep.
 */
interface CacheEntry {
  body: string;
  contentType: string;
  storedAt: number;
}

/** Which cache keys the cron should keep warm, and when each was last wanted. */
type Activity = Record<string, { url: string; lastSeen: number }>;

const CACHE_VERSION = "v1";
const ACTIVITY_KEY = "cache:activity";
/** DO SQLite values top out well above this; skip anything unusually large. */
const MAX_CACHEABLE_BYTES = 1_000_000;

/**
 * How long the cron keeps recomputing after the last reader leaves. Once this
 * lapses the container stops being touched and `sleepAfter` puts it to sleep.
 */
const ACTIVE_WINDOW_MS = 10 * 60_000;
/** How often interest in an endpoint is re-recorded. Keeps reads off the write path. */
const ACTIVITY_WRITE_INTERVAL_MS = 60_000;
/**
 * Most endpoints the cron will refresh in one run, newest interest first. The
 * dashboard asks for four, so this leaves room for a detail view alongside.
 */
const MAX_REFRESH_TARGETS = 6;

/** Beyond this the cached answer is too old to serve; recompute and make them wait. */
const MAX_STALE_MS = 10 * 60_000;

/**
 * The unconditional ledger tick. The host is arbitrary -- the container is
 * reached through the Durable Object, not DNS -- but the path is not: this
 * endpoint is what generates a forecast, logs it, pulls the third-party
 * predictions and scores whatever has erupted.
 */
const LEDGER_TICK_URL = "http://geyser-ai.internal/api/predictions";
const LEDGER_TICK_KEY = `${CACHE_VERSION}:ledger-tick`;

function freshnessFor(pathname: string): number | null {
  if (pathname === "/api/predictions" || pathname.startsWith("/api/predictions/")) {
    return 60_000;
  }
  if (pathname === "/api/eruptions/recent") return 60_000;
  // The scoreboard only moves when an eruption is scored, which needs the
  // prediction endpoint to have run anyway.
  if (pathname === "/api/scoreboard" || pathname === "/api/comparisons/recent") {
    return 120_000;
  }
  if (pathname === "/api/stats") return 3_600_000;
  // /api/health stays uncached: it is the honest freshness probe, and it is cheap.
  return null;
}

export class GeyserContainer extends Container<Env> {
  defaultPort = 8080;

  /**
   * Long enough that the dashboard's five-minute refresh keeps a warm instance
   * alive during a browsing session, short enough that an idle day costs
   * nothing. A cold start costs one snapshot download.
   */
  sleepAfter = "30m";

  /** Needed for the `entries_recent` sync against the GeyserTimes REST API. */
  enableInternet = true;

  envVars = {
    GEYSER_AI_SNAPSHOT_URL: `http://${SNAPSHOT_HOST}/${SNAPSHOT_KEY}`,
    GEYSER_AI_LEDGER_URL: `http://${SNAPSHOT_HOST}/${LEDGER_KEY}`,
  };

  /**
   * Give the snapshot a chance to land before the first request is proxied.
   * Runs inside the Durable Object's startup lock, so concurrent requests
   * queue behind it rather than seeing a 503.
   */
  override async onStart(): Promise<void> {
    const deadline = Date.now() + WARMUP_BUDGET_MS;
    while (Date.now() < deadline) {
      try {
        const probe = await this.containerFetch("http://container/api/health");
        await probe.body?.cancel();
        if (probe.ok) {
          console.log(JSON.stringify({ event: "container_ready" }));
          return;
        }
      } catch (err) {
        console.log(JSON.stringify({ event: "warmup_probe_failed", error: String(err) }));
      }
      await sleep(WARMUP_POLL_MS);
    }
    console.warn(JSON.stringify({ event: "warmup_budget_exhausted", ms: WARMUP_BUDGET_MS }));
  }

  override onError(error: unknown): never {
    console.error(JSON.stringify({ event: "container_error", error: String(error) }));
    throw error;
  }

  /**
   * Cache read, plus a note that somebody wants this endpoint. Deliberately
   * does not touch the container, so serving from cache never wakes it.
   */
  async readCache(key: string, url: string): Promise<CacheEntry | undefined> {
    const [entry, activity] = await Promise.all([
      this.ctx.storage.get<CacheEntry>(key),
      this.ctx.storage.get<Activity>(ACTIVITY_KEY),
    ]);

    // Writing on every read costs seconds, not milliseconds: a durable write
    // queues behind whatever else this Durable Object is doing, and this one is
    // also running a twenty-second container recompute on the cron. The cron
    // only needs to know somebody was here recently, so recording it once a
    // minute is exactly as useful and leaves the read path a pure read.
    const now = Date.now();
    const seen = activity?.[key]?.lastSeen ?? 0;
    if (now - seen > ACTIVITY_WRITE_INTERVAL_MS) {
      const next: Activity = { ...(activity ?? {}), [key]: { url, lastSeen: now } };
      for (const [k, v] of Object.entries(next)) {
        if (now - v.lastSeen > ACTIVE_WINDOW_MS) delete next[k];
      }
      await this.ctx.storage.put(ACTIVITY_KEY, next);
    }

    return entry;
  }

  async writeCache(key: string, entry: CacheEntry): Promise<void> {
    await this.ctx.storage.put(key, entry);
  }

  /** Endpoints worth recomputing right now: the ones read recently, newest first. */
  async refreshTargets(): Promise<{ key: string; url: string }[]> {
    const activity = await this.ctx.storage.get<Activity>(ACTIVITY_KEY);
    if (!activity) return [];
    const now = Date.now();
    return Object.entries(activity)
      .filter(([, v]) => now - v.lastSeen <= ACTIVE_WINDOW_MS)
      .sort(([, a], [, b]) => b.lastSeen - a.lastSeen)
      .slice(0, MAX_REFRESH_TARGETS)
      .map(([key, v]) => ({ key, url: v.url }));
  }
}

/**
 * Serve the DuckDB snapshot to the container from R2.
 *
 * Assigned after the class body on purpose: `outboundByHost` is a static
 * accessor on the base class that registers handlers in a module-level
 * registry, and a `static` class *field* would shadow the setter instead of
 * calling it, silently leaving the handler unregistered.
 *
 * Only plain HTTP is intercepted (`interceptHttps` stays false), so the
 * container's HTTPS calls to geysertimes.org go straight out, carrying the
 * app's own identifying User-Agent on the same five-minute TTL it uses locally.
 */
GeyserContainer.outboundByHost = {
  [SNAPSHOT_HOST]: async (request: Request, env: Env): Promise<Response> => {
    const key = new URL(request.url).pathname.replace(/^\//, "") || SNAPSHOT_KEY;

    if (request.method === "PUT") {
      if (!key.startsWith(WRITABLE_PREFIX)) {
        console.error(JSON.stringify({ event: "r2_write_refused", key }));
        return new Response(`Writes are only allowed under ${WRITABLE_PREFIX}\n`, { status: 403 });
      }
      // Buffered rather than streamed: R2 wants a known length for a stream,
      // and the ledger is a small JSON document by construction.
      const body = await request.arrayBuffer();
      await env.SNAPSHOT.put(key, body, {
        httpMetadata: { contentType: "application/json" },
      });
      console.log(JSON.stringify({ event: "ledger_written", key, bytes: body.byteLength }));
      return new Response(null, { status: 204 });
    }

    if (request.method !== "GET" && request.method !== "HEAD") {
      return new Response("Method not allowed\n", { status: 405 });
    }

    const object = await env.SNAPSHOT.get(key);
    if (!object) {
      // A missing ledger is the normal state before the first flush; a missing
      // snapshot is not, so only the latter is worth shouting about.
      if (!key.startsWith(WRITABLE_PREFIX)) {
        console.error(JSON.stringify({ event: "snapshot_missing", key }));
      }
      return new Response(`No object ${key}\n`, { status: 404 });
    }
    const headers = new Headers({ "content-type": "application/octet-stream" });
    object.writeHttpMetadata(headers);
    headers.set("etag", object.httpEtag);
    return new Response(object.body, { headers });
  },
};

/** The app's own 503 while the snapshot is still downloading. */
const isWarmingBody = (body: string) => body.includes("No database");

const WARMING_PAGE = `<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="5">
<title>Geyser AI — warming up</title>
<style>
  :root { color-scheme: light dark; }
  body { margin:0; min-height:100vh; display:grid; place-items:center;
         font:16px/1.6 ui-monospace,SFMono-Regular,Menlo,monospace;
         background:#0d1b1e; color:#cfe3e4; padding:2rem; text-align:center; }
  h1 { font-size:1.1rem; letter-spacing:.14em; text-transform:uppercase; color:#5fbfb4; margin:0 0 .75rem; }
  p { margin:.25rem 0; color:#8fa9ab; }
</style></head>
<body><div>
  <h1>Geyser AI</h1>
  <p>Loading the eruption archive.</p>
  <p>This page refreshes itself in a few seconds.</p>
</div></body></html>`;

function warmingResponse(request: Request): Response {
  const wantsHtml = (request.headers.get("accept") ?? "").includes("text/html");
  if (wantsHtml) {
    return new Response(WARMING_PAGE, {
      status: 503,
      headers: { "content-type": "text/html; charset=utf-8", "retry-after": "5" },
    });
  }
  return Response.json(
    { status: "warming", detail: "Snapshot still loading. Retry in a few seconds." },
    { status: 503, headers: { "retry-after": "5" } },
  );
}

type Stub = DurableObjectStub<GeyserContainer>;

/** Proxy to the container, translating its "no database yet" 503 into a warming page. */
async function proxy(stub: Stub, request: Request): Promise<Response> {
  const response = await stub.fetch(request);
  if (response.status !== 503) {
    return response;
  }
  // Distinguish "still warming" from a genuine per-geyser 503. Reading the body
  // means the original headers cannot be reused verbatim (any content-encoding
  // has already been undone), so rebuild a clean one.
  const body = await response.text();
  if (isWarmingBody(body)) {
    return warmingResponse(request);
  }
  return new Response(body, {
    status: 503,
    headers: { "content-type": response.headers.get("content-type") ?? "application/json" },
  });
}

function fromCache(entry: CacheEntry, state: "hit" | "stale"): Response {
  return new Response(entry.body, {
    headers: {
      "content-type": entry.contentType,
      "x-geyser-cache": state,
      "x-geyser-cache-age": String(Math.round((Date.now() - entry.storedAt) / 1000)),
    },
  });
}

/** Recompute, store when the answer is storable, and return it. */
async function refresh(stub: Stub, request: Request, key: string): Promise<Response> {
  const response = await proxy(stub, request);
  if (!response.ok) return response;

  const body = await response.text();
  const contentType = response.headers.get("content-type") ?? "application/json";
  if (body.length <= MAX_CACHEABLE_BYTES) {
    await stub.writeCache(key, { body, contentType, storedAt: Date.now() });
  }
  return new Response(body, {
    headers: { "content-type": contentType, "x-geyser-cache": "miss" },
  });
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const stub = getContainer(env.GEYSER_CONTAINER, SINGLETON);
    const url = new URL(request.url);
    const freshMs = request.method === "GET" ? freshnessFor(url.pathname) : null;

    try {
      if (freshMs === null) {
        return await proxy(stub, request);
      }

      const key = `${CACHE_VERSION}:${url.pathname}${url.search}`;
      const entry = await stub.readCache(key, url.toString());
      const age = entry ? Date.now() - entry.storedAt : Infinity;

      if (age < freshMs) return fromCache(entry!, "hit");
      // Recent enough to serve while the cron recomputes it. Past MAX_STALE_MS
      // the cron is evidently not keeping up, so fall through and recompute
      // here rather than hand out an answer nobody should act on.
      if (age < MAX_STALE_MS) return fromCache(entry!, "stale");
      return await refresh(stub, request, key);
    } catch (err) {
      console.error(JSON.stringify({ event: "proxy_failed", error: String(err) }));
      return Response.json(
        { status: "error", detail: "Container unavailable. Try again shortly." },
        { status: 502, headers: { "retry-after": "10" } },
      );
    }
  },

  /**
   * Drive the scoreboard, and warm whatever readers have been asking for.
   *
   * The ledger tick is unconditional. Predictions can only be scored against
   * eruptions that have already happened, so a scoreboard that only advanced
   * while somebody was watching would have permanent holes exactly where the
   * park is quietest -- and the comparison against the NPS and Geysers.net
   * would be drawn from a biased sample of the day. One `/api/predictions` run
   * generates this project's forecast, logs it, pulls every open third-party
   * prediction and scores anything that has erupted since, so a single call
   * covers all of it. That keeps the container awake around the clock, which
   * is a deliberate cost trade rather than an accident.
   *
   * GeyserTimes sees no more traffic for this: both the eruption sync and the
   * predictions feed are behind their own five-minute TTLs, which is why this
   * runs on the same five-minute cadence rather than faster.
   *
   * Everything after the tick is response-cache warming, and that stays
   * visitor-gated -- there is no point recomputing a dashboard shape nobody
   * has asked for in ten minutes.
   */
  async scheduled(_controller: ScheduledController, env: Env): Promise<void> {
    const stub = getContainer(env.GEYSER_CONTAINER, SINGLETON);
    const warm = await stub.refreshTargets();

    // A visitor-driven refresh of the prediction endpoint already does the
    // ledger's work, so don't pay for it twice.
    const alreadyPredicts = warm.some((t) => {
      try {
        return new URL(t.url).pathname === "/api/predictions";
      } catch {
        return false;
      }
    });
    const targets = alreadyPredicts
      ? warm
      : [{ key: LEDGER_TICK_KEY, url: LEDGER_TICK_URL }, ...warm];

    for (const target of targets) {
      try {
        const response = await refresh(stub, new Request(target.url), target.key);
        console.log(
          JSON.stringify({ event: "cron_refresh", key: target.key, status: response.status }),
        );
      } catch (err) {
        console.error(
          JSON.stringify({ event: "cron_refresh_failed", key: target.key, error: String(err) }),
        );
      }
    }
  },
} satisfies ExportedHandler<Env>;
