#!/usr/bin/env python3
"""inbox/ に届いた参照ログを読み、論文メタデータを補完して data/papers.json に統合する。"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INBOX = ROOT / "inbox"
DATA = ROOT / "data" / "papers.json"
S2 = "https://api.semanticscholar.org/graph/v1/paper/"
S2_FIELDS = "paperId,title,authors,year,venue,abstract,tldr,externalIds,url,citationCount,openAccessPdf"
UA = "paper-trail/1.0 (personal paper log)"
ID_ORDER = ("arxiv", "doi", "acl", "s2", "openreview")


class NotFound(Exception):
    """論文が存在しない（または API が知らない）"""


class Temporary(Exception):
    """レート制限や通信エラー。次回に再試行する"""


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def http_get(url, headers=None, retries=3, notfound=(400, 404)):
    h = {"User-Agent": UA, **(headers or {})}
    for i in range(retries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=20) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code in notfound:
                raise NotFound(url)
            if e.code in (429, 500, 502, 503, 504) and i < retries - 1:
                time.sleep(4 * (i + 1))  # 429 は少し待てば通ることが多い
                continue
            raise Temporary(f"HTTP {e.code}: {url}")
        except (urllib.error.URLError, TimeoutError) as e:
            if i < retries - 1:
                time.sleep(4 * (i + 1))
                continue
            raise Temporary(f"{e}: {url}")


# ---------- URL から識別子を取り出す ----------

def extract_ids(s):
    s = (s or "").strip()
    ids = {}
    m = re.search(r"arxiv\.org/(?:abs|pdf|html)/(\d{4}\.\d{4,5}|[a-z\-]+(?:\.[A-Z]{2})?/\d{7})", s, re.I)
    if m:
        ids["arxiv"] = m.group(1)
    m = re.fullmatch(r"(?:arxiv:\s*)?(\d{4}\.\d{4,5})(?:v\d+)?", s, re.I)
    if m:
        ids["arxiv"] = m.group(1)
    m = re.search(r"(?<![\d.])(10\.\d{4,9}/[^\s?#\"<>]+)", s)
    if m and "arxiv" not in ids:
        doi = re.sub(r"(\.pdf|/full|/abstract|[).,;])+$", "", m.group(1)).lower()
        a = re.match(r"10\.48550/arxiv\.(.+)", doi)  # arXiv 発行の DOI は arXiv ID として扱う
        if a:
            ids["arxiv"] = a.group(1)
        else:
            ids["doi"] = doi
    m = re.search(r"aclanthology\.org/([A-Z]\d{2}-\d{4}|\d{4}\.[a-z0-9\-]+\.\d+)", s)
    if m:
        ids["acl"] = m.group(1)
    m = re.search(r"semanticscholar\.org/paper/(?:[^/]+/)?([0-9a-f]{40})", s)
    if m:
        ids["s2"] = m.group(1)
    m = re.search(r"openreview\.net/(?:forum|pdf)\?id=([\w\-]+)", s)
    if m:
        ids["openreview"] = m.group(1)
    for k in ("arxiv",):
        if k in ids:
            ids[k] = re.sub(r"v\d+$", "", ids[k])
    return ids


# ---------- メタデータ取得 ----------

def from_s2(d):
    ext = d.get("externalIds") or {}
    ids = {"s2": d["paperId"]}
    if ext.get("ArXiv"):
        ids["arxiv"] = ext["ArXiv"]
    if ext.get("DOI"):
        ids["doi"] = ext["DOI"].lower()
    if ext.get("ACL"):
        ids["acl"] = ext["ACL"]
    return {
        "title": d.get("title"),
        "authors": [a.get("name") for a in d.get("authors") or [] if a.get("name")],
        "year": d.get("year"),
        "venue": d.get("venue") or None,
        "abstract": d.get("abstract"),
        "tldr": (d.get("tldr") or {}).get("text"),
        "citationCount": d.get("citationCount"),
        "pdf": (d.get("openAccessPdf") or {}).get("url"),
        "url": d.get("url"),
        "ids": ids,
        "metaSource": "semanticscholar",
    }


def s2_headers():
    key = os.environ.get("S2_API_KEY")
    return {"x-api-key": key} if key else None


def s2(pid):
    url = S2 + urllib.parse.quote(pid, safe=":/") + "?fields=" + S2_FIELDS
    return from_s2(json.loads(http_get(url, s2_headers())))


def s2_match(title):
    q = urllib.parse.urlencode({"query": title, "fields": S2_FIELDS})
    data = json.loads(http_get(S2 + "search/match?" + q, s2_headers()))
    hits = data.get("data") or []
    if not hits:
        raise NotFound(title)
    meta = from_s2(hits[0])
    meta["matchedByTitle"] = True  # タイトル検索での一致は別論文の可能性が残る
    return meta


def arxiv(aid):
    xml = http_get("https://export.arxiv.org/api/query?id_list=" + urllib.parse.quote(aid))
    ns = {"a": "http://www.w3.org/2005/Atom", "x": "http://arxiv.org/schemas/atom"}
    entry = ET.fromstring(xml).find("a:entry", ns)
    if entry is None or "api/errors" in (entry.findtext("a:id", "", ns)):
        raise NotFound(aid)
    title = " ".join((entry.findtext("a:title", "", ns)).split())
    if not title:
        raise NotFound(aid)
    ids = {"arxiv": aid}
    doi = entry.findtext("x:doi", None, ns)
    if doi:
        ids["doi"] = doi.lower()
    return {
        "title": title,
        "authors": [a.findtext("a:name", "", ns) for a in entry.findall("a:author", ns)],
        "year": int(entry.findtext("a:published", "0000", ns)[:4]) or None,
        "venue": "arXiv",
        "abstract": " ".join((entry.findtext("a:summary", "", ns)).split()),
        "tldr": None,
        "citationCount": None,
        "pdf": f"https://arxiv.org/pdf/{aid}",
        "url": f"https://arxiv.org/abs/{aid}",
        "ids": ids,
        "metaSource": "arxiv",
    }


def crossref(doi):
    msg = json.loads(http_get("https://api.crossref.org/works/" + urllib.parse.quote(doi, safe="/")))["message"]
    title = (msg.get("title") or [None])[0]
    if not title:
        raise NotFound(doi)
    parts = (msg.get("issued") or {}).get("date-parts") or [[None]]
    abstract = msg.get("abstract")
    if abstract:
        abstract = " ".join(re.sub(r"<[^>]+>", " ", abstract).split())  # JATS タグを除去
    return {
        "title": title,
        "authors": [" ".join(x for x in (a.get("given"), a.get("family")) if x) for a in msg.get("author") or []],
        "year": parts[0][0],
        "venue": (msg.get("container-title") or [None])[0],
        "abstract": abstract,
        "tldr": None,
        "citationCount": msg.get("is-referenced-by-count"),
        "pdf": None,
        "url": msg.get("URL") or f"https://doi.org/{doi}",
        "ids": {"doi": doi},
        "metaSource": "crossref",
    }


def from_page(url):
    """出版社ページの citation_doi などのメタタグから識別子を読む"""
    try:
        html = http_get(url, {"Accept": "text/html"}, retries=2,
                        notfound=(400, 401, 403, 404, 410, 451)).decode("utf-8", "replace")[:500000]
    except Temporary:
        raise NotFound(url)  # ボット拒否などで読めないページは再試行しても無駄なことが多い
    meta = {}
    for tag in re.findall(r"<meta\s[^>]*>", html, re.I):
        attrs = dict((k.lower(), v) for k, v in re.findall(r'([\w:.\-]+)\s*=\s*["\']([^"\']*)["\']', tag))
        name = (attrs.get("name") or attrs.get("property") or "").lower()
        if name and "content" in attrs:
            meta.setdefault(name, attrs["content"].strip())
    doi = next((meta[k] for k in ("citation_doi", "prism.doi", "dc.identifier", "bepress_citation_doi")
                if re.search(r"10\.\d{4,9}/", meta.get(k, ""))), None)
    if meta.get("citation_arxiv_id"):
        return s2("arXiv:" + meta["citation_arxiv_id"])
    if doi:
        doi = re.search(r"10\.\d{4,9}/\S+", doi).group(0).lower()
        try:
            return s2("DOI:" + doi)
        except NotFound:
            return crossref(doi)
    if meta.get("citation_title"):
        return s2_match(meta["citation_title"])
    raise NotFound(url)


def resolve(e):
    """S2 を優先し、だめなら arXiv / Crossref にフォールバックする"""
    ids = extract_ids(e.get("url"))
    tries = []
    if "s2" in ids:
        tries.append(lambda: s2(ids["s2"]))
    if "arxiv" in ids:
        tries += [lambda: s2("arXiv:" + ids["arxiv"]), lambda: arxiv(ids["arxiv"])]
    if "doi" in ids:
        tries += [lambda: s2("DOI:" + ids["doi"]), lambda: crossref(ids["doi"])]
    if "acl" in ids:
        tries.append(lambda: s2("ACL:" + ids["acl"]))
    if not ids and e.get("url"):
        tries.append(lambda: s2("URL:" + e["url"]))  # ACM など S2 が URL で引けるサイト用
        tries.append(lambda: from_page(e["url"]))  # IEEE や Nature などはページ内のメタタグから
    if e.get("title"):
        tries.append(lambda: s2_match(e["title"]))
    temporary = False
    for t in tries:
        try:
            return t()
        except NotFound:
            continue
        except Temporary as err:
            print(f"  一時エラー: {err}", file=sys.stderr)
            temporary = True
    if temporary:
        raise Temporary(e.get("url") or e.get("title"))
    raise NotFound(e.get("url") or e.get("title"))


# ---------- papers.json への統合 ----------

def key_of(ids):
    for k in ID_ORDER:
        if ids.get(k):
            return f"{k}:{ids[k]}"
    if ids.get("url"):
        return "url:" + ids["url"]
    return "title:" + ids.get("title", "")


def find(papers, ids):
    for p in papers:
        if any(v and p["ids"].get(k) == v for k, v in ids.items()):
            return p
    return None


def make_ref(e):
    return {
        "at": e.get("at") or now(),
        "source": e.get("source") or "manual",
        "project": e.get("project") or None,
        "context": e.get("context") or None,
        "url": e.get("url") or None,
    }


def add_ref(p, ref):
    if ref not in p["refs"]:
        p["refs"].append(ref)
        p["refs"].sort(key=lambda r: r["at"])
    p["firstSeen"] = p["refs"][0]["at"]
    p["lastSeen"] = p["refs"][-1]["at"]


def merge(papers, e, meta):
    ref = make_ref(e)
    raw = {**extract_ids(e.get("url")), "url": e.get("url") or None}
    if meta:
        p = find(papers, meta["ids"])
        stale = find([q for q in papers if not q["verified"]], raw)  # 以前「未確認」だった同じURLを吸収
        if p is None:
            p = {"refs": []}
            papers.append(p)
        if stale is not None and stale is not p:
            for r in stale["refs"]:
                add_ref(p, r)
            papers.remove(stale)
        downgrade = p.get("metaSource") == "semanticscholar" and meta["metaSource"] != "semanticscholar"
        for k, v in meta.items():
            if k == "ids" or (downgrade and p.get(k) is not None):
                continue  # 情報の多い S2 のデータを簡易版で上書きしない
            if v is not None or k not in p:
                p[k] = v
        p["ids"] = {**p.get("ids", {}), **meta["ids"]}
        p["key"] = key_of(p["ids"])
        p["verified"] = True
    else:
        if not raw["url"]:
            raw = {"title": (e.get("title") or "").strip().lower()}
        p = find(papers, raw)
        if p is None:
            p = {
                "key": key_of(raw),
                "title": e.get("title") or e.get("url"),
                "authors": [], "year": None, "venue": None, "abstract": None, "tldr": None,
                "citationCount": None, "pdf": None, "url": e.get("url") or None,
                "ids": {k: v for k, v in raw.items() if v},
                "verified": False, "refs": [],
            }
            papers.append(p)
    add_ref(p, ref)
    return p


def load_entries(path):
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return []
    if path.suffix == ".jsonl":
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    data = json.loads(text)
    return data if isinstance(data, list) else [data]


def normalize(e):
    """URL 欄にタイトルや裸の ID が入っていても受け付ける"""
    e = dict(e)
    u = (e.get("url") or "").strip()
    if u and not u.lower().startswith("http") and not extract_ids(u):
        e["title"], e["url"] = e.get("title") or u, ""
    elif u and not u.lower().startswith("http"):
        ids = extract_ids(u)
        e["url"] = f"https://arxiv.org/abs/{ids['arxiv']}" if "arxiv" in ids else f"https://doi.org/{ids['doi']}"
    return e


def main():
    db = json.loads(DATA.read_text(encoding="utf-8")) if DATA.exists() else {"papers": []}
    papers = db.get("papers", [])
    files = sorted(p for p in INBOX.glob("*") if p.suffix in (".json", ".jsonl"))
    if not files:
        print("inbox は空です")
    added = unverified = kept = 0
    for f in files:
        done = True
        for e in load_entries(f):
            e = normalize(e)
            label = e.get("url") or e.get("title")
            if not label:
                continue
            try:
                meta = resolve(e)
                p = merge(papers, e, meta)
                added += 1
                print(f"✓ {p['title']}")
            except NotFound:
                merge(papers, e, None)
                unverified += 1
                print(f"? 未確認: {label}")
            except Temporary:
                done = False
                print(f"… 後で再試行: {label}")
            time.sleep(1.1)  # 無料枠のレート制限に配慮
        if done:
            f.unlink()
        else:
            kept += 1
    papers.sort(key=lambda p: p.get("lastSeen", ""), reverse=True)
    DATA.parent.mkdir(exist_ok=True)
    DATA.write_text(json.dumps({"updated": now(), "papers": papers}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"確認済み {added} 件、未確認 {unverified} 件、再試行待ちのファイル {kept} 件")


if __name__ == "__main__":
    main()
