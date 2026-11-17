// Cloudflare Worker: starts the dispatch build on GitHub.
// - POST from the page's "Update now" button
// - Cron trigger every 5 minutes (GitHub's own */5 schedule runs far less often than that)
// Secret GITHUB_TOKEN: fine-grained token, repo oneboss302/dispatch only, Actions: Read and write.
// Abuse guard: never starts a run while one is queued/running or within 60s of the last one.
const REPO = "oneboss302/dispatch";
const WORKFLOW = "update.yml";
const ORIGIN = "https://dispatch.castafero.com";

function gh(env, path, init = {}) {
  return fetch("https://api.github.com" + path, {
    ...init,
    headers: {
      Authorization: `Bearer ${env.GITHUB_TOKEN}`,
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "dispatch-update-worker",
    },
  });
}

async function trigger(env) {
  const r = await gh(env, `/repos/${REPO}/actions/workflows/${WORKFLOW}/runs?per_page=5`);
  if (!r.ok) return { status: "error", detail: `GitHub ${r.status}` };
  const runs = (await r.json()).workflow_runs || [];
  const busy = runs.find((x) => x.status !== "completed");
  if (busy) return { status: "running", since: busy.created_at };
  if (runs[0] && Date.now() - Date.parse(runs[0].created_at) < 60_000) return { status: "recent", since: runs[0].created_at };
  const d = await gh(env, `/repos/${REPO}/actions/workflows/${WORKFLOW}/dispatches`, {
    method: "POST",
    body: JSON.stringify({ ref: "main" }),
  });
  return d.status === 204 ? { status: "started" } : { status: "error", detail: `GitHub ${d.status}` };
}

export default {
  async fetch(req, env) {
    const headers = { "Access-Control-Allow-Origin": ORIGIN, "Access-Control-Allow-Methods": "POST, OPTIONS", "Content-Type": "application/json" };
    if (req.method === "OPTIONS") return new Response(null, { headers });
    if (req.method !== "POST" || req.headers.get("Origin") !== ORIGIN)
      return new Response('{"status":"forbidden"}', { status: 403, headers });
    const res = await trigger(env);
    return new Response(JSON.stringify(res), { status: res.status === "error" ? 502 : 200, headers });
  },
  async scheduled(_event, env, ctx) {
    ctx.waitUntil(trigger(env));
  },
};
