// 「論文の足あと」チャット用コネクタ（リモート MCP サーバー、依存ライブラリなし）
// URL: https://<worker>.workers.dev/mcp/<MCP_SECRET>
// 必要な設定: 変数 PAPER_REPO、シークレット GITHUB_TOKEN と MCP_SECRET

const SERVER = { name: "paper-trail", version: "1.0.0" };
const FALLBACK_VERSION = "2025-06-18";

const TOOLS = [
  {
    name: "add_papers",
    title: "論文を足あとに記録",
    description:
      "Record academic papers in the user's personal paper log. Call this whenever you cite, recommend, " +
      "summarize, or discuss a specific paper in your reply, including papers you mention from memory. " +
      "Send one item per paper, all papers in one call. Prefer a URL, arXiv ID, or DOI; if you only know " +
      "the title, send the exact title and leave url empty. Never invent URLs or DOIs you are not sure of: " +
      "a wrong identifier is worse than a title alone. Papers that cannot be verified are marked as " +
      "unverified on the site, so it is fine to record papers you are unsure exist.",
    inputSchema: {
      type: "object",
      properties: {
        items: {
          type: "array",
          minItems: 1,
          maxItems: 20,
          items: {
            type: "object",
            properties: {
              url: { type: "string", description: "Paper URL, arXiv ID (e.g. 2106.15928), or DOI. Empty if unknown." },
              title: { type: "string", description: "Exact paper title." },
              context: { type: "string", description: "Why the paper came up, in the user's language, under 80 characters." },
            },
            required: ["context"],
          },
        },
        project: { type: "string", description: "Optional project name if the user mentioned one." },
      },
      required: ["items"],
    },
    annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: false, openWorldHint: true },
  },
  {
    name: "search_papers",
    title: "足あとから論文を探す",
    description:
      "Search papers already recorded in the user's paper log by words in the title, authors, abstract, " +
      "or the notes of when they were referenced. Use it when the user asks about papers they read before, " +
      "or to check whether a paper is already recorded. Returns up to 10 papers, most recent first.",
    inputSchema: {
      type: "object",
      properties: {
        query: { type: "string", description: "Space-separated keywords. Empty returns the most recent papers." },
      },
    },
    annotations: { readOnlyHint: true, openWorldHint: false },
  },
];

const json = (body, status = 200, headers = {}) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json", ...headers } });
const rpcResult = (id, result) => json({ jsonrpc: "2.0", id, result });
const rpcError = (id, code, message) => json({ jsonrpc: "2.0", id, error: { code, message } });
const toolText = (text, isError = false) => ({ content: [{ type: "text", text }], isError });

async function github(env, path, init = {}) {
  const res = await fetch(`https://api.github.com/repos/${env.PAPER_REPO}${path}`, {
    ...init,
    headers: {
      Authorization: `Bearer ${env.GITHUB_TOKEN}`,
      Accept: "application/vnd.github+json",
      "User-Agent": "paper-trail-connector",
      ...(init.headers || {}),
    },
  });
  if (!res.ok) throw new Error(`GitHub API ${res.status}: ${(await res.text()).slice(0, 200)}`);
  return res;
}

async function addPapers(env, args) {
  const clip = (s, n) => String(s ?? "").trim().slice(0, n);
  const at = new Date().toISOString().replace(/\.\d{3}Z$/, "Z");
  const items = (Array.isArray(args.items) ? args.items : [])
    .slice(0, 20)
    .map((x) => ({
      url: clip(x.url, 500),
      title: clip(x.title, 300),
      context: clip(x.context, 140),
      project: clip(args.project, 80),
      source: "chat",
      at,
    }))
    .filter((x) => x.url || x.title);
  if (!items.length) return toolText("url か title のある項目がありません。", true);
  await github(env, "/dispatches", {
    method: "POST",
    body: JSON.stringify({ event_type: "add-paper", client_payload: { items } }),
  });
  const list = items.map((x) => `- ${x.title || x.url}`).join("\n");
  return toolText(`${items.length} 件を送りました。数分後にサイトへ反映されます。\n${list}`);
}

async function searchPapers(env, args) {
  const res = await github(env, "/contents/data/papers.json", { headers: { Accept: "application/vnd.github.raw+json" } });
  const papers = (await res.json()).papers || [];
  const words = String(args.query || "").toLowerCase().split(/\s+/).filter(Boolean);
  const hits = papers.filter((p) => {
    const hay = [p.title, (p.authors || []).join(" "), p.venue, p.abstract, p.tldr,
      ...(p.refs || []).map((r) => `${r.context || ""} ${r.project || ""}`)].join(" ").toLowerCase();
    return words.every((w) => hay.includes(w));
  });
  if (!hits.length) return toolText(words.length ? "該当する論文は記録にありません。" : "まだ論文が記録されていません。");
  const lines = hits.slice(0, 10).map((p) => {
    const who = (p.authors || []).slice(0, 3).join(", ") + ((p.authors || []).length > 3 ? " ほか" : "");
    const last = (p.refs || []).at(-1) || {};
    return [
      `■ ${p.title}${p.verified ? "" : "（未確認）"}`,
      [who, p.venue, p.year].filter(Boolean).join("、"),
      p.url || "",
      `参照 ${(p.refs || []).length} 回、最後は ${String(p.lastSeen || "").slice(0, 10)}${last.context ? `（${last.context}）` : ""}`,
      p.tldr ? `要点: ${p.tldr}` : "",
    ].filter(Boolean).join("\n");
  });
  return toolText(`${hits.length} 件中 ${lines.length} 件\n\n${lines.join("\n\n")}`);
}

async function handleRpc(env, msg) {
  const { id, method, params = {} } = msg;
  switch (method) {
    case "initialize": {
      const asked = params.protocolVersion;
      return rpcResult(id, {
        protocolVersion: /^\d{4}-\d{2}-\d{2}$/.test(asked || "") ? asked : FALLBACK_VERSION, // 最小構成なので要求された版に合わせる
        capabilities: { tools: { listChanged: false } },
        serverInfo: SERVER,
        instructions: "Use add_papers whenever a specific academic paper comes up in the conversation.",
      });
    }
    case "ping":
      return rpcResult(id, {});
    case "tools/list":
      return rpcResult(id, { tools: TOOLS });
    case "tools/call": {
      try {
        const args = params.arguments || {};
        if (params.name === "add_papers") return rpcResult(id, await addPapers(env, args));
        if (params.name === "search_papers") return rpcResult(id, await searchPapers(env, args));
        return rpcError(id, -32602, `Unknown tool: ${params.name}`);
      } catch (e) {
        return rpcResult(id, toolText(`失敗しました: ${e.message}`, true)); // ツールの失敗は Claude に文章で伝える
      }
    }
    default:
      return rpcError(id ?? null, -32601, `Method not found: ${method}`);
  }
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === "/") return new Response("paper-trail connector is running", { status: 200 });
    if (!env.MCP_SECRET || url.pathname !== `/mcp/${env.MCP_SECRET}`) return new Response("Not found", { status: 404 });
    if (request.method !== "POST") return new Response("Method not allowed", { status: 405, headers: { Allow: "POST" } });

    let msg;
    try {
      msg = await request.json();
    } catch {
      return rpcError(null, -32700, "Parse error");
    }
    if (Array.isArray(msg)) return rpcError(null, -32600, "Batch requests are not supported");
    if (msg.id === undefined) return new Response(null, { status: 202 }); // 通知には本文なしで応答
    return handleRpc(env, msg);
  },
};
