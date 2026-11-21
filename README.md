# gemini-orchestration

Claude Code（オーケストレーター）から Gemini CLI（サブエージェント）を呼ぶ仕組み一式。
参考: https://zenn.dev/motowo/articles/claude-code-gemini-cli-orchestration-guide

## 中身
- `install.sh` … Gemini CLI / nanobanana / analytics-mcp / gask / 設定ファイルをまとめて入れる（何度実行しても安全）
- `gask/gask.py` … Gemini を安全に呼ぶラッパー（下の「デメリット対策」）
- `gask/policy.toml` … Gemini にシェル実行・ファイル書き込みをさせないポリシー
- `config/` … `~/.gemini/settings.json` と `~/.claude/CLAUDE.md` の元

## デメリット対策（gask）
| デメリット | 対策 |
|---|---|
| 伝言ゲームでずれる | 依頼者の原文を `-r` でそのまま同梱。回答は決まった JSON 形式を強制し、仮定・不明点も書かせる |
| 要約の間違いに気づけない | Gemini が使ったツールの生データを全部ログに保存し、回答の数字が生データに実在するか自動照合（⚠️ 表示） |
| お金がかかる | 1日のトークン上限・画像枚数上限で自動停止。使用量を台帳に記録（`gask --usage`） |
| 待ち時間 | 同じ依頼は24時間キャッシュで即答・費用0。タイムアウトあり |
| 安全性 | 専用ポリシーで Gemini のシェル実行・ファイル書き込みを禁止 |

## 使い方
```
gask analyze   -r "ユーザーの原文" -t "作業指示"
gask summarize -r "ユーザーの原文" -t "何を抜き出すか" -f 資料.txt
gask image     -r "ユーザーの原文" -t "/generate ..."
gask --usage
```
設定（上限・料金単価・モデル）は `~/.gemini-orch/config.json`。

## インストール

### デスクトップ（Mac / Linux / Windows の WSL）
Node.js 20 以上・Python 3.10 以上・git が必要。
```bash
git clone https://github.com/vpgkotaro-jpg/gemini-orchestration ~/gemini-orchestration && bash ~/gemini-orchestration/install.sh
```
そのあと Mac なら `bash ~/gemini-orchestration/set-key.sh` で Gemini API キーを貼る（キーチェーンに保存され、`~/.zshrc` から自動で読み込む）。Linux / WSL は `~/.bashrc` などに `export GEMINI_API_KEY=...`。
既存の `~/.claude/CLAUDE.md`・`~/.claude/settings.json`・`~/.gemini/settings.json` は上書きせず、必要な部分だけ追記する。
更新するときは `cd ~/gemini-orchestration && git pull && bash install.sh`。

### Claude Code のクラウド環境（セットアップスクリプト）
```bash
[ -d /opt/gemini-orchestration ] || git clone --depth 1 https://github.com/vpgkotaro-jpg/gemini-orchestration /opt/gemini-orchestration
bash /opt/gemini-orchestration/install.sh || echo "gemini-orchestration: install failed"
```
環境のネットワークで github.com・generativelanguage.googleapis.com（GA を使うなら analyticsadmin / analyticsdata.googleapis.com も）を許可し、パッケージマネージャーを許可しておく。

## API キー
キーはファイルに書かない。Claude Code のクラウド環境なら Network secrets に
`generativelanguage.googleapis.com` / ヘッダー `x-goog-api-key` で登録する（通信時に自動で付く）。
