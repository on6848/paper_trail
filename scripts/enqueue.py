#!/usr/bin/env python3
"""workflow_dispatch / repository_dispatch の内容を inbox/ に1ファイルとして書き出す。
payload は1件（url など）でも複数件（items: [...]）でもよい。"""
import json
import os
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path

payload = json.loads(os.environ.get("PAYLOAD") or "{}") or {}
default_source = os.environ.get("SOURCE_DEFAULT") or "manual"
now = datetime.now(timezone.utc).isoformat(timespec="seconds")


def entry(p):
    return {
        "url": (p.get("url") or "").strip(),
        "title": (p.get("title") or "").strip(),
        "context": (p.get("context") or "").strip(),
        "project": (p.get("project") or "").strip(),
        "tags": [str(t).strip() for t in (p.get("tags") or []) if str(t).strip()][:8] if isinstance(p.get("tags"), list) else [],
        "source": (p.get("source") or default_source).strip(),
        "at": p.get("at") or now,
    }


raw = payload.get("items") if isinstance(payload.get("items"), list) else [payload]
entries = [e for e in map(entry, raw[:100]) if e["url"] or e["title"]]  # 1回100件まで
if not entries:
    sys.exit("url か title のある項目が1件もありません")

stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
out = Path(__file__).resolve().parent.parent / "inbox" / f"{stamp}-{secrets.token_hex(3)}.json"  # 同時実行でも名前が衝突しない
out.write_text(json.dumps(entries, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"inbox に {len(entries)} 件追加: {out.name}")
