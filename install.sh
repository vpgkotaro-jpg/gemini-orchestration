#!/bin/bash
# Claude Code × Gemini CLI オーケストレーション一式をインストールする。
# クラウド環境・Mac・Linux・WSL で動く。何度実行しても安全で、既存の設定は上書きせずに追記・統合する。
set -e
cd "$(dirname "$0")"
REPO_DIR="$(pwd)"
export PATH="$HOME/.local/bin:$PATH"

# Claude Code のクラウド環境かどうか（クラウドではキーをプロキシが付けるのでダミーを置く）
IS_CLOUD=0
[ -d /root/.ccr ] && IS_CLOUD=1
[ -n "$GO_FORCE_LOCAL" ] && IS_CLOUD=0  # テスト用: デスクトップ扱いにする
if [ "$IS_CLOUD" = 1 ]; then
  export NO_PROXY=localhost,127.0.0.1 no_proxy=localhost,127.0.0.1 npm_config_noproxy=localhost
fi

need() { command -v "$1" >/dev/null 2>&1; }
need node || { echo "Node.js 20 以上が必要です（https://nodejs.org）"; exit 1; }
need python3 || { echo "Python 3.10 以上が必要です"; exit 1; }
need git || { echo "git が必要です"; exit 1; }

# 1. Gemini CLI
need gemini || npm install -g @google/gemini-cli

# 2. pipx → analytics-mcp
if ! need pipx; then
  if need uv; then uv tool install pipx
  elif need brew; then brew install pipx
  else python3 -m pip install --user pipx
  fi
fi
[ -x "$HOME/.local/bin/analytics-mcp" ] || need analytics-mcp || pipx install analytics-mcp
AMCP="$(command -v analytics-mcp || echo "$HOME/.local/bin/analytics-mcp")"

# 3. nanobanana 拡張（MCP サーバーをビルド）
EXT="$HOME/.gemini/extensions/nanobanana"
if [ ! -f "$EXT/mcp-server/dist/index.js" ]; then
  rm -rf "$EXT"; mkdir -p "$HOME/.gemini/extensions"
  git clone --depth 1 https://github.com/gemini-cli-extensions/nanobanana "$EXT"
  (cd "$EXT/mcp-server" && npm install)
fi

# 4. gask 本体
mkdir -p "$HOME/.gemini-orch" "$HOME/.local/bin" "$HOME/.claude"
cp gask/gask.py gask/policy.toml "$HOME/.gemini-orch/"
chmod +x "$HOME/.gemini-orch/gask.py"
ln -sf "$HOME/.gemini-orch/gask.py" "$HOME/.local/bin/gask"

# 5. 設定ファイルを統合（既存の設定は残す）
python3 - "$HOME" "$AMCP" "$IS_CLOUD" <<'PY'
import json, sys, pathlib, re
home, amcp, is_cloud = pathlib.Path(sys.argv[1]), sys.argv[2], sys.argv[3] == "1"

def load(p):
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}

# ~/.gemini/settings.json
gs = home / ".gemini" / "settings.json"
s = load(gs)
s.setdefault("security", {}).setdefault("auth", {})["selectedType"] = "gemini-api-key"  # 古いログイン方式が残っていても API キー方式にそろえる
mcp = s.setdefault("mcpServers", {})
amcp_env = {"GOOGLE_PROJECT_ID": "$GOOGLE_PROJECT_ID",
            "GOOGLE_APPLICATION_CREDENTIALS": str(home / ".config/gcloud/application_default_credentials.json")}
if is_cloud:
    amcp_env.update({"NO_PROXY": "localhost,127.0.0.1", "no_proxy": "localhost,127.0.0.1"})
mcp["analytics-mcp"] = {"command": amcp, "args": [], "env": amcp_env}
mcp["nanobanana"] = {"command": "node",
                     "args": [str(home / ".gemini/extensions/nanobanana/mcp-server/dist/index.js")],
                     "env": {"NANOBANANA_GEMINI_API_KEY": "$GEMINI_API_KEY"}}
allowed = s.setdefault("mcp", {}).setdefault("allowed", [])
for n in ("analytics-mcp", "nanobanana"):
    if n not in allowed:
        allowed.append(n)
gs.parent.mkdir(parents=True, exist_ok=True)
gs.write_text(json.dumps(s, ensure_ascii=False, indent=2) + "\n")

# ~/.claude/settings.json（env に足りない項目だけ足す）
cs = home / ".claude" / "settings.json"
c = load(cs)
env = c.setdefault("env", {})
env.setdefault("BASH_DEFAULT_TIMEOUT_MS", "300000")
if is_cloud:
    env.setdefault("GEMINI_API_KEY", "proxy-injected-dummy-key")
cs.write_text(json.dumps(c, ensure_ascii=False, indent=2) + "\n")

# ~/.claude/CLAUDE.md（目印で囲んだ区画だけ入れ替える。ほかの記述は残す）
cm = home / ".claude" / "CLAUDE.md"
block = "<!-- gemini-orchestration:start -->\n" + pathlib.Path("config/CLAUDE.md").read_text().strip() + "\n<!-- gemini-orchestration:end -->"
old = cm.read_text() if cm.exists() else ""
old = re.sub(r"<!-- gemini-orchestration:start -->.*?<!-- gemini-orchestration:end -->", "", old, flags=re.S)
# 以前の版が目印なしで書いた区画を取り除く
old = re.sub(r"## Gemini CLI 連携.*?(?=\n## (?!#)|\Z)", "", old, flags=re.S).strip()
cm.write_text((old + "\n\n" if old else "") + block + "\n")
PY

# 6. クラウドではシェルにもダミーキー（本物はプロキシが付ける）
if [ "$IS_CLOUD" = 1 ]; then
  grep -q GEMINI_API_KEY "$HOME/.bashrc" 2>/dev/null || echo 'export GEMINI_API_KEY="${GEMINI_API_KEY:-proxy-injected-dummy-key}"' >> "$HOME/.bashrc"
fi

case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) echo "※ $HOME/.local/bin を PATH に追加してください" ;; esac
if [ "$IS_CLOUD" = 0 ] && [ -z "$GEMINI_API_KEY" ]; then
  echo "※ Gemini API キーを環境変数 GEMINI_API_KEY に設定してください（例: ~/.zshrc に export GEMINI_API_KEY=...）"
fi
echo "gemini-orchestration: install done"
