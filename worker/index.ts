/**
 * Front door for the Signal Basin container.
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
 * The snapshot is published as numbered parts plus this manifest, because a
 * single ~200 MB PUT is unreliable over some networks. The container assembles
 * them; the whole-object key above stays as a fallback.
 */
const SNAPSHOT_MANIFEST_KEY = "snapshot/manifest.json";

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

/**
 * Beyond this the cron has evidently lost interest in the entry. The read
 * itself re-records that interest, so the next tick recomputes it; until then
 * the reader still gets the cached answer. A forecast is a set of absolute
 * times and a ten-minute-old one is far more useful than a twenty-second wait
 * that, landing on top of the cron's own recompute on a quarter of a vCPU, runs
 * past the page's 45 s timeout and shows an error instead. (Not a background
 * refresh: `waitUntil` is cancelled long before a container fetch returns.)
 */
const MAX_STALE_MS = 10 * 60_000;
/** Past this even a stale answer is withheld; the reader waits on a fresh one. */
const MAX_SERVABLE_MS = 60 * 60_000;

/**
 * Contact form.
 *
 * Most gazers do not have a GitHub account, and the whole point of publishing
 * the method is to be told where it is wrong -- so there is a form, and it
 * sends mail through Postmark. It lives in the Worker rather than in the
 * container for the same reason the R2 credentials do: the container image
 * holds no credentials, and a token baked into it would travel with every
 * image push. `POSTMARK_TOKEN` and `CONTACT_TO` are secrets (`wrangler secret
 * put`); the destination address is one of them so it never lands in a public
 * repository.
 *
 * The endpoint sends email on someone else's quota from an unauthenticated
 * request, so it is deliberately unexciting to abuse: a honeypot field, a
 * minimum dwell time, hard length caps, and a per-IP allowance kept in the
 * Durable Object storage that is already here.
 */
const CONTACT_PATH = "/api/contact";
const MAIL_FROM = "Signal Basin <no-reply@signalbasin.org>";
const POSTMARK_URL = "https://api.postmarkapp.com/email";
/** Postmark separates transactional from bulk; corrections are transactional. */
const POSTMARK_STREAM = "outbound";

const MSG_MIN = 10;
const MSG_MAX = 4000;
/** Nobody types a real report in under three seconds; a script does. */
const DWELL_MIN_MS = 3_000;
const BODY_MAX_BYTES = 16_000;
const PER_IP_HOUR = 3;
const PER_IP_DAY = 10;
/**
 * Whole-site backstop. Reaching it needs ~170 distinct addresses in a day,
 * which is an attack rather than a busy morning, and the failure it buys is a
 * temporarily closed form rather than a drained Postmark quota.
 */
const CONTACT_LOG_CAP = 500;
const CONTACT_LOG_KEY = "contact:log";

const HOUR_MS = 3_600_000;
const DAY_MS = 86_400_000;

interface ContactEntry {
  /** Truncated SHA-256 of the address -- enough to count, not enough to identify. */
  ip: string;
  t: number;
}

/**
 * Rate limiting needs to tell addresses apart, not to know them, so the raw
 * address never reaches storage. Unsalted and truncated on purpose: this is a
 * counting key with a 24-hour life, not an authentication secret.
 */
async function hashIp(ip: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(ip));
  return [...new Uint8Array(digest)]
    .slice(0, 8)
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

/** Single-line fields go into a mail header; newlines have no business in them. */
const oneLine = (s: unknown, max: number): string =>
  typeof s === "string" ? s.replace(/[\r\n]+/g, " ").trim().slice(0, max) : "";

/** Deliberately loose: this only decides whether Reply-To is worth setting. */
const looksLikeEmail = (s: string): boolean => /^[^@\s]+@[^@\s.]+\.[^@\s]+$/.test(s);

/**
 * The unconditional ledger tick. The host is arbitrary -- the container is
 * reached through the Durable Object, not DNS -- but the path is not: this
 * endpoint is what generates a forecast, logs it, pulls the third-party
 * predictions and scores whatever has erupted.
 *
 * The query string is the dashboard's, exactly, and the cache key is the one
 * the dashboard reads. The tick used to hit the bare path and cache under a
 * private key, which meant the container computed the same forecast every
 * five minutes under a name nobody read while the dashboard's own entry went
 * cold after ten quiet minutes -- and the next visitor then waited on a full
 * recompute, racing the cron for a quarter of a vCPU, past the page's 45 s
 * timeout. Warming the real key makes the first call of a cold visit a hit.
 */
const DASHBOARD_PATH = "/api/predictions?hours=12&points=140";
const LEDGER_TICK_URL = `http://geyser-ai.internal${DASHBOARD_PATH}`;
const LEDGER_TICK_KEY = `${CACHE_VERSION}:${DASHBOARD_PATH}`;

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
  // The method document is a static read of the committed calibration artifact:
  // it changes when a new backtest is deployed and at no other time, so it is
  // cached for as long as anything here is, and a reader who opens "how this is
  // modelled" never wakes the container to be told what the image already knows.
  if (pathname === "/api/method" || pathname.startsWith("/api/method/")) {
    return 3_600_000;
  }
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
    GEYSER_AI_SNAPSHOT_MANIFEST_URL: `http://${SNAPSHOT_HOST}/${SNAPSHOT_MANIFEST_KEY}`,
    GEYSER_AI_SNAPSHOT_URL: `http://${SNAPSHOT_HOST}/${SNAPSHOT_KEY}`,
    GEYSER_AI_LEDGER_URL: `http://${SNAPSHOT_HOST}/${LEDGER_KEY}`,
    // Seismic watch state rides the same writable prefix as the ledger.
    GEYSER_AI_SEISMIC_URL: `http://${SNAPSHOT_HOST}/ledger/seismic.json`,
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
   * Self-heal a dead container instead of erroring until a human redeploys.
   *
   * When the container instance dies (crash, OOM, host maintenance), the
   * containers library marks it stopped and every subsequent containerFetch
   * throws "The container is not running, consider calling start()" -- it
   * does NOT restart on its own, and the DO surfaces raw 500s while the
   * response cache quietly papers over the outage until it goes stale.
   * Observed live on 2026-08-09; the remedy had been a manual redeploy.
   * Instead: catch exactly that state, start the container, and retry once.
   * With the five-minute cron driving traffic, any crash now heals within
   * one tick.
   */
  override async fetch(request: Request): Promise<Response> {
    try {
      return await super.fetch(request);
    } catch (err) {
      if (!String(err).includes("not running")) throw err;
      console.warn(JSON.stringify({ event: "container_dead_restarting", error: String(err) }));
      await this.start();
      await this.onStart();
      return await super.fetch(request);
    }
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

  /**
   * Spend one contact-form allowance, or refuse.
   *
   * One pruned key rather than a key per sender: the log is bounded by the
   * limits themselves, drops anything older than a day on every write, and
   * leaves nothing behind to sweep up. Storage is the Durable Object's own, so
   * this never touches -- or wakes -- the container.
   *
   * The allowance is spent before the mail is sent, so a Postmark failure still
   * costs the sender an attempt. That is the intended direction: a form that
   * refunds on failure is a form that can be retried in a loop.
   */
  async spendContactAllowance(ipHash: string): Promise<{ ok: boolean; reason?: string }> {
    const now = Date.now();
    const log = (await this.ctx.storage.get<ContactEntry[]>(CONTACT_LOG_KEY)) ?? [];
    const recent = log.filter((e) => now - e.t < DAY_MS);

    if (recent.length >= CONTACT_LOG_CAP) return { ok: false, reason: "site" };
    const mine = recent.filter((e) => e.ip === ipHash);
    if (mine.filter((e) => now - e.t < HOUR_MS).length >= PER_IP_HOUR) {
      return { ok: false, reason: "hour" };
    }
    if (mine.length >= PER_IP_DAY) return { ok: false, reason: "day" };

    recent.push({ ip: ipHash, t: now });
    await this.ctx.storage.put(CONTACT_LOG_KEY, recent);
    return { ok: true };
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
<title>Signal Basin — warming up</title>
<style>
  :root { color-scheme: light dark; }
  body { margin:0; min-height:100vh; display:grid; place-items:center;
         font:16px/1.6 ui-monospace,SFMono-Regular,Menlo,monospace;
         background:#0d1b1e; color:#cfe3e4; padding:2rem; text-align:center; }
  h1 { font-size:1.1rem; letter-spacing:.14em; text-transform:uppercase; color:#5fbfb4; margin:0 0 .75rem; }
  p { margin:.25rem 0; color:#8fa9ab; }
</style></head>
<body><div>
  <h1>Signal Basin</h1>
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

/**
 * Take a correction from a reader and mail it on.
 *
 * Every rejection is a plain JSON reason the form can show, because the person
 * on the other end is a gazer who just typed out what they saw and deserves to
 * know whether it went anywhere.
 */
async function handleContact(request: Request, env: Env, stub: Stub): Promise<Response> {
  if (request.method !== "POST") {
    return Response.json({ error: "Send a POST." }, { status: 405 });
  }
  if (!env.POSTMARK_TOKEN || !env.CONTACT_TO) {
    console.error(JSON.stringify({ event: "contact_not_configured" }));
    return Response.json(
      { error: "The contact form is not set up yet. Please try again later." },
      { status: 503 },
    );
  }

  // Bounded before it is read: a body this endpoint would reject anyway must
  // not be buffered first.
  const declared = Number(request.headers.get("content-length") ?? "0");
  if (declared > BODY_MAX_BYTES) {
    return Response.json({ error: "That message is too long." }, { status: 413 });
  }

  let payload: Record<string, unknown>;
  try {
    const raw = (await request.text()).slice(0, BODY_MAX_BYTES);
    payload = JSON.parse(raw) as Record<string, unknown>;
  } catch {
    return Response.json({ error: "Could not read that message." }, { status: 400 });
  }

  // A hidden field no human can see and no human can fill in.
  if (oneLine(payload.website, 200)) {
    console.log(JSON.stringify({ event: "contact_honeypot" }));
    // Answer exactly as success does: a bot that learns which field betrayed it
    // simply stops filling it in.
    return Response.json({ ok: true });
  }

  const message = typeof payload.message === "string" ? payload.message.trim() : "";
  if (message.length < MSG_MIN) {
    return Response.json({ error: "Please say a little more than that." }, { status: 400 });
  }
  if (message.length > MSG_MAX) {
    return Response.json(
      { error: `Please keep it under ${MSG_MAX} characters.` },
      { status: 400 },
    );
  }

  const dwell = Number(payload.elapsed_ms);
  if (!Number.isFinite(dwell) || dwell < DWELL_MIN_MS) {
    return Response.json({ error: "That was too quick — try again." }, { status: 400 });
  }

  const email = oneLine(payload.email, 200);
  if (email && !looksLikeEmail(email)) {
    return Response.json(
      { error: "That email address does not look right. Leave it blank if you prefer." },
      { status: 400 },
    );
  }

  const subjectOf = oneLine(payload.geyser, 60) || "General";
  const page = oneLine(payload.page, 200);

  const ipHash = await hashIp(request.headers.get("cf-connecting-ip") ?? "unknown");
  const allowance = await stub.spendContactAllowance(ipHash);
  if (!allowance.ok) {
    const detail =
      allowance.reason === "site"
        ? "The form is taking a break — a lot has come in today. Please try tomorrow."
        : "That is a few messages in a short time. Please try again later today.";
    return Response.json({ error: detail }, { status: 429, headers: { "retry-after": "3600" } });
  }

  const country = (request as { cf?: { country?: string } }).cf?.country ?? "unknown";
  const body = [
    `Geyser:  ${subjectOf}`,
    `Page:    ${page || "(not given)"}`,
    `Reply:   ${email || "(no address given)"}`,
    `Sent:    ${new Date().toISOString()} (${country})`,
    "",
    message,
    "",
    "— sent from the contact form on signalbasin.org",
  ].join("\n");

  try {
    const sent = await fetch(POSTMARK_URL, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        accept: "application/json",
        "x-postmark-server-token": env.POSTMARK_TOKEN,
      },
      body: JSON.stringify({
        From: MAIL_FROM,
        To: env.CONTACT_TO,
        // Only set when it parses, so a typo cannot make the mail unrepliable.
        ...(email ? { ReplyTo: email } : {}),
        Subject: `[Signal Basin] ${subjectOf}`,
        TextBody: body,
        MessageStream: POSTMARK_STREAM,
      }),
    });

    if (!sent.ok) {
      // Postmark's errors are small JSON documents; cap anyway rather than trust.
      const detail = (await sent.text()).slice(0, 500);
      console.error(JSON.stringify({ event: "postmark_failed", status: sent.status, detail }));
      return Response.json(
        { error: "The message could not be sent just now. Please try again shortly." },
        { status: 502 },
      );
    }
  } catch (err) {
    console.error(JSON.stringify({ event: "postmark_error", error: String(err) }));
    return Response.json(
      { error: "The message could not be sent just now. Please try again shortly." },
      { status: 502 },
    );
  }

  console.log(JSON.stringify({ event: "contact_sent", geyser: subjectOf, replyable: !!email }));
  return Response.json({ ok: true });
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const stub = getContainer(env.GEYSER_CONTAINER, SINGLETON);
    const url = new URL(request.url);

    // Answered here, never proxied: the container knows nothing about mail and
    // holds no credentials to send it with.
    if (url.pathname === CONTACT_PATH) {
      try {
        return await handleContact(request, env, stub);
      } catch (err) {
        console.error(JSON.stringify({ event: "contact_failed", error: String(err) }));
        return Response.json(
          { error: "Something went wrong sending that. Please try again shortly." },
          { status: 500 },
        );
      }
    }

    const freshMs = request.method === "GET" ? freshnessFor(url.pathname) : null;

    try {
      if (freshMs === null) {
        return await proxy(stub, request);
      }

      const key = `${CACHE_VERSION}:${url.pathname}${url.search}`;
      const entry = await stub.readCache(key, url.toString());
      const age = entry ? Date.now() - entry.storedAt : Infinity;

      if (age < freshMs) return fromCache(entry!, "hit");
      // Recent enough that the cron is about to recompute it anyway.
      if (age < MAX_STALE_MS) return fromCache(entry!, "stale");
      // The cron had lost interest in this shape; readCache just renewed it,
      // so the next tick recomputes. Only a genuinely old entry, or none,
      // makes the reader wait.
      if (age < MAX_SERVABLE_MS) return fromCache(entry!, "stale");
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

    // The tick is the dashboard's own request, so when a visitor has been
    // reading it the two are one refresh, not two.
    const targets = [
      { key: LEDGER_TICK_KEY, url: LEDGER_TICK_URL },
      ...warm.filter((t) => t.key !== LEDGER_TICK_KEY),
    ];

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
