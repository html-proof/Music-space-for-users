/**
 * GaanaPy — Cloudflare Worker Proxy
 *
 * Forwards every request to the Render backend, preserving method, headers,
 * path, and query-string. Adds permissive CORS headers so the Flutter app
 * (and future web clients) can reach the API without mixed-content issues or
 * browser CORS rejections.
 *
 * Environment variable (set in wrangler.toml / Cloudflare dashboard):
 *   BACKEND_URL  — e.g. https://music-space-for-users.onrender.com
 */

const CORS_HEADERS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, PUT, PATCH, DELETE, OPTIONS",
  "Access-Control-Allow-Headers":
    "Authorization, Content-Type, Accept, X-Requested-With",
  "Access-Control-Max-Age": "86400",
};

export default {
  async fetch(request, env) {
    // ── Pre-flight (OPTIONS) ──────────────────────────────────────────────────
    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: CORS_HEADERS });
    }

    const backendUrl = env.BACKEND_URL || "https://music-space-for-users.onrender.com";

    // ── Build the proxied URL ─────────────────────────────────────────────────
    const url = new URL(request.url);
    const targetUrl = `${backendUrl}${url.pathname}${url.search}`;

    // ── Forward the request ───────────────────────────────────────────────────
    // Clone headers and strip Host so the backend sees its own hostname.
    const forwardHeaders = new Headers(request.headers);
    forwardHeaders.delete("host");

    let proxiedRequest;
    if (["GET", "HEAD", "OPTIONS"].includes(request.method)) {
      proxiedRequest = new Request(targetUrl, {
        method: request.method,
        headers: forwardHeaders,
      });
    } else {
      proxiedRequest = new Request(targetUrl, {
        method: request.method,
        headers: forwardHeaders,
        body: request.body,
      });
    }

    // ── Fetch from Render ─────────────────────────────────────────────────────
    let response;
    try {
      response = await fetch(proxiedRequest);
    } catch (err) {
      return new Response(
        JSON.stringify({ success: false, error: { code: "PROXY_ERROR", message: err.message } }),
        {
          status: 502,
          headers: { "Content-Type": "application/json", ...CORS_HEADERS },
        }
      );
    }

    // ── Return response with CORS headers ─────────────────────────────────────
    const responseHeaders = new Headers(response.headers);
    for (const [k, v] of Object.entries(CORS_HEADERS)) {
      responseHeaders.set(k, v);
    }

    return new Response(response.body, {
      status: response.status,
      statusText: response.statusText,
      headers: responseHeaders,
    });
  },
};
