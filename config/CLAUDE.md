## Gemini CLI 連携（gask 経由で使う）
Claude Code はオーケストレーター、Gemini CLI はサブエージェント（GA データアナリスト＋nanobanana 画像生成＋大量資料の要約）。
`gemini -p` を直接呼ばず、必ず `gask` を使う（デメリット対策が入っている）。Bash の timeout は 300000〜600000。

### 使うとき・使わないとき
- 使う: 画像・図・アイコンの生成/編集、GA のデータ取得・分析、自分で読むと大量すぎる資料（目安: 数万字以上）の要約
- 使わない: 普通の文章・コード・ファイル操作・短い資料の要約（自分でやるほうが速く正確で無料）

### 呼び方
- 分析: `gask analyze -r "<ユーザーの原文をそのまま>" -t "<具体的な作業指示。期間・指標・出力形式>"`
- 要約: `gask summarize -r "<原文>" -t "<何を抜き出すか>" -f <資料パス>`
- 画像: `gask image -r "<原文>" -t "/generate ..."`（/diagram /edit <パス> <指示> /icon も可）。出力は ./nanobanana-output/
- `-r` には言い換えず、ユーザーの言葉をそのまま入れる（伝言ゲーム対策）
- 送る内容の確認だけなら `--dry-run`、今日の使用量は `gask --usage`

### 結果の扱い
- 「裏付けチェック」で ⚠️ が付いた数字は、そのまま使わない。ログ（末尾に出るパス）を grep して確かめるか、ユーザーに未確認と伝える
- 「ツールを1回も使っていません」と出たら、その数字は使わない
- 「分からなかったこと」「置いた仮定」はユーザーへの報告に含める
- 1日の上限で止まったら、勝手に --force せずユーザーに確認する
- 上限や料金単価は ~/.gemini-orch/config.json で変更できる

### よくあるエラー
| エラー | 対処 |
|---|---|
| API key not valid | 環境の Network secrets に generativelanguage.googleapis.com / x-goog-api-key でキーを登録 |
| MCP tool not available | ~/.gemini/settings.json の mcp.allowed と ~/.gemini-orch/policy.toml を確認 |
| Authentication failed（GA） | `gcloud auth application-default login` をやり直す |
| タイムアウト | 期間・指標を絞って分割する |
