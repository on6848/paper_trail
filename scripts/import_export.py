#!/usr/bin/env python3
"""claude.ai のデータエクスポート（conversations.json）から、Claude の回答に出てきた論文リンクや ID を拾って inbox に積む。
コネクタの呼び忘れを回収するための補助。使い方: python3 scripts/import_export.py path/to/conversations.json [--since 2026-09-01]
同じエクスポートを何度取り込んでも、参照の履歴は重複しない。"""
import argparse
import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("hook", ROOT / "hooks" / "paper_trail_hook.py")
hook = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hook)  # URL 判定と重複判定はフックと共通


def message_text(m):
    parts = [m.get("text") or ""]
    for c in m.get("content") or []:
        if isinstance(c, dict) and c.get("type") == "text":
            parts.append(c.get("text") or "")
    return "\n".join(parts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("export", help="エクスポートに含まれる conversations.json")
    ap.add_argument("--since", help="この日付（YYYY-MM-DD）以降の会話だけ取り込む")
    a = ap.parse_args()
    convs = json.loads(Path(a.export).read_text(encoding="utf-8"))
    items, keys = [], set()
    for conv in convs:
        name = (conv.get("name") or "無題の会話")[:60]
        for m in conv.get("chat_messages") or []:
            if m.get("sender") != "assistant":
                continue
            at = m.get("created_at") or conv.get("created_at") or ""
            if a.since and at[:10] < a.since:
                continue
            for url in hook.find_papers(message_text(m)):
                k = (hook.paper_key(url), conv.get("uuid"))
                if k in keys:
                    continue  # 同じ会話で同じ論文は1回だけ
                keys.add(k)
                items.append({"url": url, "source": "chat", "context": f"会話「{name}」で言及", "at": at})
    if not items:
        print("論文のリンクや ID は見つかりませんでした")
        return
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    out = ROOT / "inbox" / f"export-{stamp}.jsonl"
    out.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in items), encoding="utf-8")
    print(f"{len(items)} 件を {out.relative_to(ROOT)} に書き出しました。commit して push すると処理されます")


if __name__ == "__main__":
    main()
