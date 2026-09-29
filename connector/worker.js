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
  {
    name: "list_unsummarized",
    title: "未要約の論文を取り出す",
    description:
      "List papers in the user's paper log that do not yet have a Japanese summary, with their key, title, " +
      "authors, year and abstract. Use it when the user asks to summarize the papers in their log. " +
      "Then write the summaries and call save_summaries.",
    inputSchema: {
      type: "object",
      properties: {
        limit: { type: "integer", minimum: 1, maximum: 15, description: "How many papers to return (default 8)." },
        include_summarized: { type: "boolean", description: "Also return papers that already have a summary, to rewrite them." },
      },
    },
    annotations: { readOnlyHint: true, openWorldHint: false },
  },
  {
    name: "save_summaries",
    title: "要約を保存",
    description:
      "Save Japanese summaries for papers in the user's paper log. For each paper write summary_ja " +
      "(2 to 3 plain Japanese sentences: the problem, the method, the main result) and novelty " +
      "(2 to 4 short Japanese points on what is new compared with prior work). Do not use em dashes. " +
      "Base them on the abstract. If the abstract is missing, you may use your own knowledge of the paper, " +
      "but set basis to \"knowledge\" so the site marks it for checking; never guess about a paper you do not know.",
    inputSchema: {
      type: "object",
      properties: {
        items: {
          type: "array",
          minItems: 1,
          maxItems: 15,
          items: {
            type: "object",
            properties: {
              key: { type: "string", description: "The paper key returned by list_unsummarized." },
              summary_ja: { type: "string" },
              novelty: { type: "array", items: { type: "string" }, minItems: 1, maxItems: 5 },
              basis: { type: "string", enum: ["abstract", "knowledge"] },
            },
            required: ["key", "summary_ja", "novelty", "basis"],
          },
        },
      },
      required: ["items"],
    },
    annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: true, openWorldHint: false },
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

// ---------- リポジトリ内の JSON を読み書きする ----------

const b64decode = (b64) => new TextDecoder().decode(Uint8Array.from(atob(b64.replace(/\n/g, "")), (c) => c.charCodeAt(0)));
function b64encode(str) {
  const bytes = new TextEncoder().encode(str);
  let bin = "";
  for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000)); // 大きな配列で落ちないよう分割
  return btoa(bin);
}

async function readJson(env, path, fallback) {
  try {
    const f = await (await github(env, `/contents/${path}`)).json();
    return { sha: f.sha, data: JSON.parse(b64decode(f.content)) };
  } catch (e) {
    if (String(e.message).includes(" 404")) return { sha: null, data: fallback };
    throw e;
  }
}

async function loadPapers(env) {
  const res = await github(env, "/contents/data/papers.json", { headers: { Accept: "application/vnd.github.raw+json" } });
  return (await res.json()).papers || [];
}

async function listUnsummarized(env, args) {
  const [papers, { data: sums }] = await Promise.all([loadPapers(env), readJson(env, "data/summaries.json", {})]);
  const todo = papers.filter((p) => p.verified && (args.include_summarized || !sums[p.key]));
  if (!todo.length) return toolText("要約のない論文はありません。");
  const limit = Math.min(Math.max(args.limit || 8, 1), 15);
  const out = todo.slice(0, limit).map((p) => ({
    key: p.key,
    title: p.title,
    authors: (p.authors || []).slice(0, 6),
    year: p.year,
    venue: p.venue,
    abstract: p.abstract || p.tldr || null,
  }));
  return toolText(`未要約 ${todo.length} 本のうち ${out.length} 本:\n` + JSON.stringify(out, null, 1));
}

async function saveSummaries(env, args) {
  const clip = (s, n) => String(s ?? "").trim().slice(0, n);
  const items = (Array.isArray(args.items) ? args.items : []).slice(0, 15).filter((x) => x.key && x.summary_ja);
  if (!items.length) return toolText("保存できる項目がありません。", true);
  const papers = await loadPapers(env);
  const known = new Set(papers.map((p) => p.key));
  const unknown = items.filter((x) => !known.has(x.key)).map((x) => x.key);
  const ok = items.filter((x) => known.has(x.key));
  if (!ok.length) return toolText(`どの key も記録にありません: ${unknown.join(", ")}`, true);
  for (let attempt = 0; attempt < 3; attempt++) {
    const { sha, data } = await readJson(env, "data/summaries.json", {});
    for (const x of ok) {
      data[x.key] = {
        summary_ja: clip(x.summary_ja, 600),
        novelty: (Array.isArray(x.novelty) ? x.novelty : []).slice(0, 5).map((n) => clip(n, 200)).filter(Boolean),
        basis: x.basis === "knowledge" ? "knowledge" : "abstract",
        at: new Date().toISOString().replace(/\.\d{3}Z$/, "Z"),
      };
    }
    try {
      await github(env, "/contents/data/summaries.json", {
        method: "PUT",
        body: JSON.stringify({ message: `summaries: ${ok.length} 本の要約を保存`, content: b64encode(JSON.stringify(data, null, 1)), ...(sha ? { sha } : {}) }),
      });
      const note = unknown.length ? `\n記録にない key は飛ばしました: ${unknown.join(", ")}` : "";
      return toolText(`${ok.length} 本の要約を保存しました。数分後にサイトへ反映されます。${note}`);
    } catch (e) {
      if (!/ (409|422)/.test(e.message) || attempt === 2) throw e; // 同時更新で競合したら読み直して再試行
    }
  }
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
  const [papers, { data: sums }] = await Promise.all([loadPapers(env), readJson(env, "data/summaries.json", {})]);
  const words = String(args.query || "").toLowerCase().split(/\s+/).filter(Boolean);
  const hits = papers.filter((p) => {
    const sm = sums[p.key] || {};
    const hay = [p.title, (p.authors || []).join(" "), p.venue, p.abstract, p.tldr, sm.summary_ja, (sm.novelty || []).join(" "),
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
      sums[p.key] ? `概要: ${sums[p.key].summary_ja}` : p.tldr ? `要点: ${p.tldr}` : "",
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
        if (params.name === "list_unsummarized") return rpcResult(id, await listUnsummarized(env, args));
        if (params.name === "save_summaries") return rpcResult(id, await saveSummaries(env, args));
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
    const secret = (env.MCP_SECRET || "").trim(); // 貼り付け時の空白や改行を無視
    if (!secret || url.pathname.replace(/\/$/, "") !== `/mcp/${secret}`) return new Response("Not found", { status: 404 });
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
