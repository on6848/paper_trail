#!/usr/bin/env bash
# 手元から論文を送る。使い方: ./scripts/add_paper.sh <URL・arXiv ID・DOI・タイトル> [メモ] [source]
# 事前に GH_TOKEN（このリポジトリの Contents: Read and write だけを持つトークン）と PAPER_REPO=owner/repo を設定
set -euo pipefail
: "${GH_TOKEN:?GH_TOKEN を設定してください}"
: "${PAPER_REPO:?PAPER_REPO=owner/repo を設定してください}"
[ $# -ge 1 ] || { echo "使い方: $0 <URL> [メモ] [source]" >&2; exit 1; }

payload=$(python3 - "$@" <<'PY'
import json, sys
a = sys.argv[1:] + ["", ""]
print(json.dumps({"event_type": "add-paper",
                  "client_payload": {"url": a[0], "context": a[1], "source": a[2] or "manual"}}))
PY
)
curl -fsS -X POST \
  -H "Authorization: Bearer $GH_TOKEN" \
  -H "Accept: application/vnd.github+json" \
  "https://api.github.com/repos/$PAPER_REPO/dispatches" \
  -d "$payload"
echo "送信しました: $1"
