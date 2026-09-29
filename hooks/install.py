#!/usr/bin/env python3
"""フックを ~/.claude/hooks に置き、~/.claude/settings.json に登録する（元の設定はバックアップ）。"""
import getpass
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

HOME = Path.home()
SRC = Path(__file__).resolve().parent / "paper_trail_hook.py"
DEST = HOME / ".claude" / "hooks" / "paper_trail_hook.py"
SETTINGS = HOME / ".claude" / "settings.json"
CONF = HOME / ".config" / "paper-trail" / "config.json"
PY = "python" if os.name == "nt" else "python3"
CMD = f'{PY} "{DEST.as_posix()}"'

# 開いたページ、コマンドでの取得、PDF の読み込み、回答での言及を拾う
HOOKS = {
    "PostToolUse": {"matcher": "WebFetch|Bash|Read", "hooks": [{"type": "command", "command": CMD, "async": True}]},
    "Stop": {"hooks": [{"type": "command", "command": CMD, "async": True}]},
    "SubagentStop": {"hooks": [{"type": "command", "command": CMD, "async": True}]},
}


def main():
    DEST.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SRC, DEST)
    print(f"フックを配置: {DEST}")

    conf = json.loads(CONF.read_text(encoding="utf-8")) if CONF.exists() else {}
    repo = input(f"リポジトリ（owner/repo）[{conf.get('repo', '')}]: ").strip() or conf.get("repo")
    token = getpass.getpass("GitHub トークン（Enter で既存のまま）: ").strip() or conf.get("token")
    if not repo or not token:
        sys.exit("リポジトリとトークンは必須です")
    CONF.parent.mkdir(parents=True, exist_ok=True)
    CONF.write_text(json.dumps({"repo": repo, "token": token}, indent=1), encoding="utf-8")
    if os.name != "nt":
        CONF.chmod(0o600)  # トークンを自分以外が読めないように
    print(f"設定を保存: {CONF}")

    settings = json.loads(SETTINGS.read_text(encoding="utf-8")) if SETTINGS.exists() else {}
    if SETTINGS.exists():
        backup = SETTINGS.with_name(f"settings.backup-{datetime.now():%Y%m%d%H%M%S}.json")
        shutil.copy2(SETTINGS, backup)
        print(f"元の設定をバックアップ: {backup}")
    hooks = settings.setdefault("hooks", {})
    for event, group in HOOKS.items():
        groups = hooks.setdefault(event, [])
        groups[:] = [g for g in groups if not any("paper_trail_hook" in h.get("command", "") for h in g.get("hooks", []))]
        groups.append(group)  # 古い登録を消してから入れ直すので、何度実行しても重複しない
    SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"フックを登録: {SETTINGS}")
    print(f"\n動作確認: {PY} \"{DEST}\" --test https://arxiv.org/abs/2106.15928")


if __name__ == "__main__":
    main()
