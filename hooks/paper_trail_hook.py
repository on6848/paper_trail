#!/usr/bin/env python3
"""Claude Code のフック。作業中に参照した論文を「論文の足あと」リポジトリへ送る。
どんなエラーでも exit 0 で終わり、Claude Code の作業は止めない。

動作確認: python3 paper_trail_hook.py --test https://arxiv.org/abs/2106.15928
"""
import json
import os
import re
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HOME = Path.home()
CONF = HOME / ".config" / "paper-trail" / "config.json"
STATE = HOME / ".cache" / "paper-trail"
QUEUE = STATE / "queue.jsonl"
LOG = STATE / "hook.log"
END = r"[A-Za-z0-9._~:/?#@!$&*+,;=%\-]+"  # 日本語の句読点や括弧で URL を切る

# 論文ページとみなす URL（ブログやドキュメントは拾わない）
URL_PATTERNS = [
    r"https?://(?:www\.|export\.)?arxiv\.org/(?:abs|pdf|html)/" + END,
    r"https?://(?:dx\.)?doi\.org/10\.\d{4,9}/" + END,
    r"https?://aclanthology\.org/" + END,
    r"https?://(?:www\.)?semanticscholar\.org/paper/" + END,
    r"https?://openreview\.net/(?:forum|pdf)\?id=[\w\-]+",
    r"https?://dl\.acm\.org/doi/(?:abs/|pdf/|full/|epdf/)?10\.\d{4,9}/" + END,
    r"https?://ieeexplore\.ieee\.org/(?:abstract/)?document/\d+",
    r"https?://link\.springer\.com/(?:article|chapter)/10\.\d{4,9}/" + END,
    r"https?://(?:www\.)?sciencedirect\.com/science/article/(?:abs/)?pii/\w+",
    r"https?://onlinelibrary\.wiley\.com/doi/(?:abs/|full/|pdf/)?10\.\d{4,9}/" + END,
    r"https?://(?:www\.)?nature\.com/articles/[\w.\-]+",
    r"https?://proceedings\.(?:neurips\.cc|mlr\.press)/" + END,
    r"https?://(?:www\.)?(?:bio|med)rxiv\.org/content/10\.\d{4,9}/" + END,
    r"https?://(?:www\.)?pubmed\.ncbi\.nlm\.nih\.gov/\d+",
]
# 本文中の裸の ID（arXiv:2106.15928 や doi:10.1145/...）
BARE_PATTERNS = [
    (r"\barXiv:\s?(\d{4}\.\d{4,5})(?:v\d+)?", "https://arxiv.org/abs/{}"),
    (r"\bdoi:\s?(10\.\d{4,9}/" + END + ")", "https://doi.org/{}"),
]


def log(msg):
    try:
        STATE.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as f:
            f.write(f"{datetime.now().isoformat(timespec='seconds')} {msg}\n")
    except OSError:
        pass


def clean(url):
    url = url.rstrip(".,;:!?*_")  # 文末の句読点や Markdown 記号を落とす
    return re.sub(r"v\d+$", "", url) if "arxiv.org" in url else url


def find_papers(text):
    found = []
    for p in URL_PATTERNS:
        found += [clean(m) for m in re.findall(p, text or "", re.I)]
    for p, tmpl in BARE_PATTERNS:
        found += [tmpl.format(clean(m)) for m in re.findall(p, text or "", re.I)]
    return list(dict.fromkeys(found))  # 順序を保って重複除去


def project_name(cwd):
    p = Path(cwd or ".").resolve()
    for d in [p, *p.parents]:
        if (d / ".git").exists():
            return d.name
    return p.name


def extract(ev):
    """イベントから (URLリスト, メモ) を取り出す"""
    name = ev.get("hook_event_name")
    tool = ev.get("tool_name")
    ti = ev.get("tool_input") or {}
    if name == "PostToolUse" and tool == "WebFetch":
        url = ti.get("url", "")
        papers = find_papers(url)
        return papers, (ti.get("prompt") or "ページを参照")[:140]
    if name == "PostToolUse" and tool == "Bash":
        return find_papers(ti.get("command", "")), "コマンドで取得"
    if name == "PostToolUse" and tool == "Read":
        path = ti.get("file_path", "")
        m = re.search(r"(\d{4}\.\d{4,5})(?:v\d+)?\.pdf$", path, re.I)  # arXiv ID 名の PDF だけ拾う
        return ([f"https://arxiv.org/abs/{m.group(1)}"] if m else []), "PDF を読み込み"
    if name in ("Stop", "SubagentStop"):
        return find_papers(ev.get("last_assistant_message", "")), "回答の中で言及"
    return [], ""


def paper_key(url):
    """同じ論文の PDF 版と abs 版などを同一とみなすための鍵"""
    m = re.search(r"arxiv\.org/(?:abs|pdf|html)/(\d{4}\.\d{4,5}|[a-z\-]+(?:\.[A-Z]{2})?/\d{7})", url, re.I)
    if m:
        return "arxiv:" + m.group(1)
    m = re.search(r"(10\.\d{4,9}/[^\s?#]+)", url)
    if m:
        return "doi:" + re.sub(r"(\.pdf|/full|/abstract)$", "", m.group(1)).lower()
    return url.lower()


def seen_filter(session, urls):
    """同じセッションで同じ論文を二度送らない"""
    d = STATE / "seen"
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{re.sub(r'[^\w-]', '_', session or 'nosession')}.txt"
    seen = set(f.read_text(encoding="utf-8").split()) if f.exists() else set()
    new, keys = [], []
    for u in urls:
        k = paper_key(u)
        if k not in seen and k not in keys:
            new.append(u)
            keys.append(k)
    if keys:
        with f.open("a", encoding="utf-8") as fh:
            fh.write("".join(k + "\n" for k in keys))
    for old in d.glob("*.txt"):  # 2週間より古い記録は掃除
        if time.time() - old.stat().st_mtime > 14 * 86400:
            old.unlink(missing_ok=True)
    return new


def config():
    c = {}
    if CONF.exists():
        c = json.loads(CONF.read_text(encoding="utf-8"))
    return {
        "repo": os.environ.get("PAPER_TRAIL_REPO") or c.get("repo"),
        "token": os.environ.get("PAPER_TRAIL_TOKEN") or c.get("token"),
    }


def send(items):
    """送信に失敗したら queue.jsonl に貯め、次回まとめて再送する"""
    STATE.mkdir(parents=True, exist_ok=True)
    pending = []
    if QUEUE.exists():
        tmp = QUEUE.with_suffix(f".{os.getpid()}")
        try:
            QUEUE.rename(tmp)  # 他のフック実行と取り合わないよう先に確保
            pending = [json.loads(l) for l in tmp.read_text(encoding="utf-8").splitlines() if l.strip()]
            tmp.unlink()
        except OSError:
            pending = []
    batch = pending + items
    if not batch:
        return
    if os.environ.get("PAPER_TRAIL_DRYRUN"):
        print(json.dumps(batch, ensure_ascii=False, indent=1))
        return
    c = config()
    try:
        if not c["repo"] or not c["token"]:
            raise RuntimeError(f"repo か token が未設定です（{CONF}）")
        for i in range(0, len(batch), 20):  # 1回の送信は20件まで
            body = json.dumps({"event_type": "add-paper", "client_payload": {"items": batch[i:i + 20]}}).encode()
            req = urllib.request.Request(
                f"https://api.github.com/repos/{c['repo']}/dispatches", data=body, method="POST",
                headers={"Authorization": f"Bearer {c['token']}", "Accept": "application/vnd.github+json",
                         "User-Agent": "paper-trail-hook"})
            urllib.request.urlopen(req, timeout=10).close()
        log(f"送信 {len(batch)} 件: " + ", ".join(x["url"] for x in batch))
    except Exception as e:
        with QUEUE.open("a", encoding="utf-8") as f:
            f.write("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in batch))
        log(f"送信失敗（{e}）。{len(batch)} 件を queue に保存")


def main():
    if os.environ.get("PAPER_TRAIL_DISABLE"):
        return
    if len(sys.argv) >= 3 and sys.argv[1] == "--test":
        item = {"url": sys.argv[2], "source": "claude-code", "project": "hook-test",
                "context": "フックの動作確認", "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        send([item])
        print(f"送信しました。数分後にサイトに出なければ {LOG} を確認してください")
        return
    ev = json.load(sys.stdin)
    urls, context = extract(ev)
    urls = seen_filter(ev.get("session_id"), urls)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    items = [{"url": u, "source": "claude-code", "project": project_name(ev.get("cwd")),
              "context": context, "at": now} for u in urls]
    send(items)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # フックの失敗で作業を止めない
        log(f"エラー: {e!r}")
    sys.exit(0)
