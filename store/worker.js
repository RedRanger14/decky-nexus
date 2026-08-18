/**
 * Cloudflare Worker: serve the Decky custom store index with CORS that
 * Decky's own fetch can actually use.
 *
 * Why this exists, measured rather than assumed:
 *
 * Decky requests the store with a custom header, X-Decky-Version. A custom
 * header forces the browser to send a CORS preflight (OPTIONS), and the
 * preflight only passes if the response includes Access-Control-Allow-Headers
 * naming that header. No free static host does:
 *
 *   GitHub Pages           405 Method Not Allowed
 *   raw.githubusercontent  403 Forbidden
 *   jsDelivr               200, no Allow-Headers
 *   Statically             204, no Allow-Headers
 *
 * So the fetch is blocked, pluginList stays null, and Decky's store page
 * shows a spinner forever with nothing in any log. That cost an afternoon to
 * find, because every layer looked fine on its own: the JSON is valid, the
 * URL returns 200 to curl, and the Deck's browser downloads it happily.
 *
 * Setup (once, free):
 *   1. dash.cloudflare.com -> Workers & Pages -> Create -> Worker
 *   2. Replace the default code with this file, Deploy
 *   3. The worker gets a URL like https://NAME.SUBDOMAIN.workers.dev
 *   4. Put that URL in Decky: settings -> General -> store Custom -> paste it
 *
 * It proxies the index from the repo, so publishing a release and running
 * tools/makestore.py is still all that is needed to ship an update.
 */

const INDEX =
  "https://raw.githubusercontent.com/RedRanger14/decky-nexus/main/store/plugins.json";

// Echo whatever the preflight asks for. This is a public read-only index; the
// risk of permissive CORS here is nil, and being strict is what broke it.
const cors = (request) => ({
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
  "Access-Control-Allow-Headers":
    request.headers.get("Access-Control-Request-Headers") || "*",
  "Access-Control-Max-Age": "86400",
});

export default {
  async fetch(request) {
    if (request.method === "OPTIONS") {
      // 204 with the headers above is the whole point of this worker.
      return new Response(null, { status: 204, headers: cors(request) });
    }

    // Decky appends ?sort_by=...&sort_direction=... The index is a single
    // plugin, so sorting is a no-op and the query is ignored deliberately
    // rather than passed upstream, where it would only invalidate caches.
    const upstream = await fetch(INDEX, { cf: { cacheTtl: 60 } });
    if (!upstream.ok) {
      return new Response(
        JSON.stringify({ error: `store index unavailable (${upstream.status})` }),
        { status: 502, headers: { "Content-Type": "application/json", ...cors(request) } },
      );
    }

    return new Response(await upstream.text(), {
      status: 200,
      headers: {
        "Content-Type": "application/json; charset=utf-8",
        "Cache-Control": "public, max-age=60",
        ...cors(request),
      },
    });
  },
};
