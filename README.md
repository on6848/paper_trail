# 論文の足あと

Claude との作業中に参照した論文を自動で集め、GitHub Pages で一覧できる個人用サイト。

## しくみ

```
Claude Code のフック ─┐
チャット用コネクタ    ─┼─▶ repository_dispatch ─▶ inbox/ に1件ずつ保存
手動（Actions / CLI） ─┘                              │
                         Semantic Scholar / arXiv / Crossref で存在確認・補完
                                                      ▼
                         data/papers.json 更新 ─▶ GitHub Pages に公開
```

- 論文データベースで見つからないものは「未確認」として点線で表示（Claude の記憶違い対策）
- タイトルだけで見つけた論文には「別の論文の可能性あり」の注記が付く
- API が混んでいて取れなかったものは inbox に残り、毎日 JST 3:00 に再試行

## セットアップ

1. GitHub で新しい **public** リポジトリを作り、このフォルダの中身を push する
   （private リポジトリで Pages を使うには有料プランが必要）
2. リポジトリの Settings → Pages → Build and deployment の Source を **GitHub Actions** にする
3. （推奨）Semantic Scholar の API キーを取得し、Settings → Secrets and variables → Actions に `S2_API_KEY` として登録する
   キーなしでも動くが、無料の共有枠なので混雑時に取得失敗が増える
4. Actions タブ → papers → Run workflow で論文の URL を入れて実行し、サイトに出れば完成

## Claude Code から自動で集める

### 拾うもの

| きっかけ | 例 | 参照の履歴に出るメモ |
|---|---|---|
| Claude がページを開いた（WebFetch） | arXiv や DOI のページ | Claude がそのページで調べたかった内容 |
| コマンドで取得した（Bash） | `curl` で arXiv を取得 | コマンドで取得 |
| arXiv ID 名の PDF を読んだ（Read） | `2106.15928.pdf` | PDF を読み込み |
| 回答の中でリンクや ID を挙げた（Stop） | `arXiv:2106.15928`、`doi:10.1145/...` | 回答の中で言及 |

プロジェクト名は作業フォルダの Git リポジトリ名が入る。検索結果に出ただけの論文（WebSearch）は、読んだとは言えないので拾わない。
**リンクも ID もなくタイトルだけで挙げた論文は拾えない**（誤検出を避けるため）。

### 入れ方

1. GitHub で Fine-grained token を作る。対象はこのリポジトリだけ、権限は Contents: Read and write
2. 次を実行し、リポジトリ名とトークンを入力する

   ```bash
   python3 hooks/install.py
   ```

   `~/.claude/hooks/` にフック本体を置き、`~/.claude/settings.json` に登録する。元の設定は自動でバックアップされ、何度実行しても登録は重複しない。トークンは `~/.config/paper-trail/config.json`（本人のみ読み取り可）に保存される
3. 動作確認

   ```bash
   python3 ~/.claude/hooks/paper_trail_hook.py --test https://arxiv.org/abs/2106.15928
   ```

   数分後にサイトの「Claude Code」に1本出れば完了。Claude Code で `/hooks` を開くと登録内容を確認できる

### 仕様

- フックはバックグラウンド（async）で動くので、Claude Code の作業は待たされない
- 同じセッションで同じ論文は1回だけ送る（PDF版と abs 版も同一扱い）
- 送信に失敗したら `~/.cache/paper-trail/queue.jsonl` に貯め、次のフック実行時にまとめて再送
- どんなエラーでも Claude Code は止めない。記録は `~/.cache/paper-trail/hook.log`
- 一時的に止めたいときは環境変数 `PAPER_TRAIL_DISABLE=1`

## チャットから集める（カスタムコネクタ）

claude.ai に自作のコネクタを追加し、Claude が論文に触れたときに `add_papers` で記録する。スマホアプリのチャットでも動く。

| ツール | 役割 |
|---|---|
| `add_papers` | 会話に出た論文を記録（URL・ID がなければタイトルだけでも可） |
| `search_papers` | 記録済みの論文を検索（「前に読んだ交差数の論文どれ？」など） |
| `list_unsummarized` | 日本語要約のない論文とアブストラクトを取り出す |
| `save_summaries` | 日本語の概要と新規性のポイントを `data/summaries.json` に保存 |
| `list_tags` | 使われているタグの一覧と、タグのない論文を取り出す |
| `tag_papers` | 論文にタグを付ける・外す（`data/notes.json` に保存） |

チャットで「足あとの未要約の論文を要約して」と頼むと、Claude が上の2つを使って要約を書き、サイトに反映される。要約はアブストラクトが材料。アブストラクトがなく Claude の知識で書いたものには、サイト上で「本文で確認」の注記が付く。

### デプロイ（Cloudflare Workers の無料枠）

Node.js と Cloudflare アカウントが必要。

```bash
cd connector
# wrangler.toml の PAPER_REPO を自分のリポジトリに書き換えてから
npx wrangler login
npx wrangler secret put GITHUB_TOKEN        # フックと同じトークンでよい
python3 -c "import secrets; print(secrets.token_urlsafe(24))"   # 出てきた文字列を控える
npx wrangler secret put MCP_SECRET          # 控えた文字列を貼る
npx wrangler deploy                          # 表示された workers.dev の URL を控える
```

### claude.ai に登録

1. claude.ai のコネクタ設定で「カスタムコネクタを追加」を選ぶ
2. URL に `https://<表示されたURL>/mcp/<MCP_SECRET>` を入れる（OAuth の欄は空でよい）
3. 新しいチャットで、入力欄の「+」メニューからこのコネクタがオンになっているか確認する
4. 設定のプロフィール（Claude に知っておいてほしいこと）に次の一文を足すと、呼び忘れが減る

   > 会話で特定の論文に触れたら、paper-trail の add_papers で記録してください。

### 注意

- **URL がパスワード代わり**。知っている人は論文の追加と検索ができるので、人に見せない（論文リスト自体は Pages で公開されている）
- Claude が呼び忘れたら記録されない。取りこぼしは下のエクスポート取り込みで回収する

### 取りこぼしの回収（データエクスポート）

claude.ai の設定からデータをエクスポートし、中の `conversations.json` を渡す。Claude の回答に出てきた論文リンクと ID を拾って inbox に積む（自分の発言は対象外）。

```bash
python3 scripts/import_export.py ~/Downloads/conversations.json --since 2026-09-01
git add inbox && git commit -m "エクスポートから取り込み" && git push
```

同じエクスポートを何度取り込んでも、参照の履歴は重複しない。

## 手元から追加する

```bash
export GH_TOKEN=...            # Fine-grained token（このリポジトリのみ、Contents: Read and write）
export PAPER_REPO=yourname/paper-trail
./scripts/add_paper.sh https://arxiv.org/abs/2106.15928 "卒論のレイアウト比較" manual
./scripts/add_paper.sh "StoryFlow: Tracking the Evolution of Stories"   # タイトルだけでも可
```

`source` は `claude-code` / `chat` / `manual` のどれかにすると色分けされる。

## タグとメモ

チャットで「足あとの論文をタグ付けして」と頼むと、Claude が `list_tags` と `tag_papers` でタグを付ける。`add_papers` でも記録と同時にタグを付けられる。サイトではタグを押すと絞り込め（複数選ぶとすべてを満たす論文だけ）、URL の `#tag=...` で同じ絞り込みを共有できる。手で直す場合は下のとおり。

`data/notes.json` を GitHub 上で直接編集する。キーはサイトの論文の `key`（例 `arxiv:2106.15928`）。

```json
{
  "arxiv:2106.15928": { "tags": ["卒論", "レイアウト"], "note": "交差数の評価指標が参考になる" }
}
```

## 注意

GitHub Pages は誰でも見られる。未発表のアイデアに関わるメモは notes.json に書かないこと。

## ローカルで表示を確認

```bash
python3 scripts/process_inbox.py
mkdir -p _site/data && cp -r site/* _site/ && cp data/*.json _site/data/
python3 -m http.server -d _site 8000
```

## ファイル構成

| パス | 役割 |
|---|---|
| `inbox/` | 受信した参照ログ（処理後に削除） |
| `data/papers.json` | 論文データ本体（自動更新） |
| `data/notes.json` | 自分で書くタグとメモ |
| `data/summaries.json` | 日本語の概要と新規性（チャットから保存） |
| `scripts/process_inbox.py` | メタデータ補完と統合 |
| `scripts/enqueue.py` | 受信内容を inbox に書く（Actions 用） |
| `scripts/add_paper.sh` | 手元から送る CLI |
| `hooks/paper_trail_hook.py` | Claude Code のフック本体 |
| `hooks/install.py` | フックのインストーラ |
| `connector/worker.js` | チャット用コネクタ（MCP サーバー） |
| `connector/wrangler.toml` | コネクタのデプロイ設定 |
| `scripts/import_export.py` | データエクスポートからの回収 |
| `site/index.html` | サイト本体 |
| `.github/workflows/papers.yml` | 受信、処理、公開 |
