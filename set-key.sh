#!/bin/bash
# Gemini API キーを Mac のキーチェーンに保存し、ターミナル起動時に自動で読み込むようにする。
# キーは画面に表示されず、ファイルにも書かれない（キーチェーンにだけ入る）。
set -e
read -rsp "Gemini API キーを貼り付けて Enter: " KEY; echo
[ -n "$KEY" ] || { echo "キーが空です"; exit 1; }
security add-generic-password -U -a "$USER" -s gemini-api-key -w "$KEY"
LINE='export GEMINI_API_KEY="$(security find-generic-password -a "$USER" -s gemini-api-key -w 2>/dev/null)"'
grep -qF 'gemini-api-key -w' ~/.zshrc 2>/dev/null || printf '\n# Gemini API キー（キーチェーンから読み込み）\n%s\n' "$LINE" >> ~/.zshrc
echo "保存しました。新しいターミナルから使えます。"
