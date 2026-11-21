#!/usr/bin/env python3
"""gask - Claude Code から Gemini CLI を安全に呼ぶためのラッパー。

連携のデメリットをそれぞれ潰す:
  1. 伝言ゲーム   -> 依頼者の原文をそのまま同梱し、決まった回答フォーマット(JSON)を強制
  2. 要約の誤り   -> Gemini が使ったツールの生データを全部ログに残し、
                     回答中の数字が生データ(または渡した資料)に実在するか自動照合
  3. お金         -> 1日あたりのトークン上限・画像枚数上限。超えたら止まる。使用量を台帳に記録
  4. 待ち時間     -> 同じ依頼は24時間キャッシュで即答。タイムアウトあり
  5. 安全         -> 専用ポリシーで Gemini のシェル実行・ファイル書き込みを禁止
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

HOME = Path(os.environ.get("GASK_HOME", Path.home() / ".gemini-orch"))
CONFIG = HOME / "config.json"
LEDGER = HOME / "ledger.jsonl"
CACHE = HOME / "cache"
LOGS = HOME / "logs"
POLICY = HOME / "policy.toml"

DEFAULT_CONFIG = {
    "daily_token_limit": 2_000_000,
    "daily_image_limit": 10,
    "timeout_sec": 300,
    "cache_hours": 24,
    "models": {"analyze": None, "summarize": None, "image": None},
    "price_per_million_tokens_jpy": None,
    "price_per_image_jpy": None,
}

KIND_HELP = {
    "analyze": "GA などのデータを MCP で取得して分析",
    "summarize": "--file で渡した資料を要約",
    "image": "nanobanana で画像・図を生成/編集",
}

CONTRACT = """\
# 回答フォーマット（厳守）
回答は次の JSON だけを ```json ブロックで1つ出力すること。前後に文章を書かない。
{
  "answer": "依頼への答え。日本語で簡潔に。表が必要ならMarkdown表をこの文字列に入れる",
  "key_facts": [
    {"claim": "主張", "value": "数値や固有名をそのまま", "source": "どのツール結果/資料のどこから取ったか"}
  ],
  "unknowns": ["取得できなかった・判断できなかったこと"],
  "assumptions": ["置いた仮定（期間の解釈など）"],
  "files": ["生成・編集したファイルのパス（画像のとき）"]
}
# ルール
- 数字は必ずツール結果または資料からそのまま写す。計算した値は claim に「計算値」と明記し、元の数字も key_facts に入れる
- 推測で埋めない。分からないことは unknowns に書く
- 依頼者の原文と作業指示が食い違うと感じたら、原文を優先し assumptions にその旨を書く
"""


def load_config():
    HOME.mkdir(parents=True, exist_ok=True)
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG.exists():
        try:
            cfg.update(json.loads(CONFIG.read_text()))
        except json.JSONDecodeError as e:
            sys.exit(f"gask: config.json が壊れています: {e}")
    else:
        CONFIG.write_text(json.dumps(DEFAULT_CONFIG, ensure_ascii=False, indent=2) + "\n")
    return cfg


def today_usage():
    tokens = images = calls = 0
    today = dt.date.today().isoformat()
    if LEDGER.exists():
        for line in LEDGER.read_text().splitlines():
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("date") == today and not r.get("cached"):
                tokens += r.get("total_tokens", 0)
                images += r.get("images", 0)
                calls += 1
    return {"tokens": tokens, "images": images, "calls": calls}


def yen(cfg, tokens, images):
    parts = []
    if cfg.get("price_per_million_tokens_jpy") is not None:
        parts.append(tokens / 1e6 * cfg["price_per_million_tokens_jpy"])
    if cfg.get("price_per_image_jpy") is not None:
        parts.append(images * cfg["price_per_image_jpy"])
    return None if not parts else round(sum(parts), 1)


def build_prompt(args, file_texts):
    out = []
    if args.kind == "image":
        out.append(args.task.strip())
        out.append("\n生成後、出力ファイルのパスを必ず明記すること。")
        out.append(f"\n（依頼者の原文: 「{args.request.strip()}」）")
        return "\n".join(out)
    out.append("# 依頼者の原文（要約・言い換えせずにそのまま渡しています）")
    out.append(args.request.strip())
    out.append("\n# 作業指示（オーケストレーターより）")
    out.append(args.task.strip())
    if args.kind == "analyze":
        out.append("必要なデータは MCP ツールで実際に取得すること。取得していない数字は書かない。")
    for name, text in file_texts:
        out.append(f"\n# 資料: {name}\n<<<資料ここから>>>\n{text}\n<<<資料ここまで>>>")
    out.append("\n" + CONTRACT)
    return "\n".join(out)


def cache_key(args, prompt, model):
    h = hashlib.sha256()
    h.update(json.dumps([args.kind, model, prompt], ensure_ascii=False).encode())
    return h.hexdigest()[:24]


def run_gemini(prompt, model, timeout, workdir):
    gem = os.environ.get("GASK_GEMINI_BIN", "gemini")
    cmd = [gem, "-o", "stream-json", "--approval-mode", "yolo", "--skip-trust"]
    if POLICY.exists():
        cmd += ["--policy", str(POLICY)]
    if model:
        cmd += ["-m", model]
    cmd += ["-p", prompt]
    env = dict(os.environ)
    env.setdefault("GEMINI_API_KEY", "proxy-injected-dummy-key")
    t0 = time.time()
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=workdir, env=env)
    except subprocess.TimeoutExpired as e:
        return {"error": f"タイムアウト（{timeout}秒）。依頼を小さく分けるか --timeout を延ばしてください",
                "stdout": e.stdout or "", "stderr": e.stderr or "", "sec": timeout}
    return {"stdout": p.stdout, "stderr": p.stderr, "code": p.returncode, "sec": round(time.time() - t0, 1)}


def parse_stream(stdout):
    text, tools, results, stats, errors, model = [], {}, [], {}, [], None
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        t = ev.get("type")
        if t == "init":
            model = ev.get("model")
        elif t == "message" and ev.get("role") == "assistant":
            text.append(ev.get("content", ""))
        elif t == "tool_use":
            tools[ev.get("tool_id")] = {"tool": ev.get("tool_name"), "params": ev.get("parameters")}
        elif t == "tool_result":
            info = tools.get(ev.get("tool_id"), {})
            results.append({**info, "status": ev.get("status"), "output": ev.get("output", ""),
                            "error": ev.get("error")})
        elif t == "error":
            errors.append(ev.get("message"))
        elif t == "result":
            stats = ev.get("stats") or {}
            if ev.get("status") == "error":
                errors.append((ev.get("error") or {}).get("message"))
    return {"text": "".join(text), "tool_results": results, "stats": stats,
            "errors": [e for e in errors if e], "model": model}


def extract_json(text):
    m = re.findall(r"```json\s*(\{.*?\})\s*```", text, re.S)
    cands = m or re.findall(r"(\{.*\})", text, re.S)
    for c in reversed(cands):
        try:
            return json.loads(c)
        except json.JSONDecodeError:
            continue
    return None


NUM = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


def norm_nums(s):
    out = set()
    for n in NUM.findall(str(s)):
        n = n.replace(",", "")
        if n in ("", "-"):
            continue
        out.add(n)
        if "." in n:
            out.add(n.rstrip("0").rstrip("."))
    return out


def verify(answer, evidence_text):
    """key_facts と answer 中の数字が、生データ/資料に実在するかを照合する。"""
    pool = norm_nums(evidence_text)
    checks = []
    for f in answer.get("key_facts", []) or []:
        nums = norm_nums(f.get("value", ""))
        if not nums:
            checks.append({**f, "check": "数字なし"})
            continue
        if "計算値" in str(f.get("claim", "")):
            checks.append({**f, "check": "計算値(元データで要確認)"})
            continue
        missing = sorted(n for n in nums if n not in pool)
        checks.append({**f, "check": "OK" if not missing else f"未確認: {', '.join(missing)}"})
    stray = sorted(n for n in norm_nums(answer.get("answer", "")) if n not in pool and len(n.replace(".", "")) >= 2)
    return checks, stray


def count_new_images(before, workdir):
    outdir = Path(workdir) / "nanobanana-output"
    after = set(outdir.glob("*")) if outdir.exists() else set()
    return sorted(str(p) for p in after - before)


def cmd_usage(cfg):
    u = today_usage()
    y = yen(cfg, u["tokens"], u["images"])
    print(f"今日の使用量: {u['calls']}回 / {u['tokens']:,} トークン (上限 {cfg['daily_token_limit']:,}) / "
          f"画像 {u['images']}枚 (上限 {cfg['daily_image_limit']})" + (f" / 概算 ¥{y}" if y is not None else ""))


def main():
    ap = argparse.ArgumentParser(description="Gemini CLI を安全に呼ぶラッパー",
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog="\n".join(f"  {k:10s} {v}" for k, v in KIND_HELP.items()))
    ap.add_argument("kind", nargs="?", choices=list(KIND_HELP), help="依頼の種類")
    ap.add_argument("--request", "-r", help="依頼者(ユーザー)の原文。言い換えずにそのまま")
    ap.add_argument("--task", "-t", help="Gemini への具体的な作業指示")
    ap.add_argument("--file", "-f", action="append", default=[], help="要約させる資料（複数可）")
    ap.add_argument("--model", "-m")
    ap.add_argument("--timeout", type=int)
    ap.add_argument("--workdir", default=os.getcwd(), help="画像の出力先の基準ディレクトリ")
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--force", action="store_true", help="1日の上限を超えても実行する（ユーザーの許可がある時だけ）")
    ap.add_argument("--dry-run", action="store_true", help="Gemini に送る内容だけ表示して終了")
    ap.add_argument("--usage", action="store_true", help="今日の使用量を表示")
    args = ap.parse_args()
    cfg = load_config()
    for d in (CACHE, LOGS):
        d.mkdir(parents=True, exist_ok=True)

    if args.usage:
        return cmd_usage(cfg)
    if not args.kind or not args.request or not args.task:
        ap.error("kind / --request / --task は必須です（--request には依頼者の原文をそのまま入れる）")
    if args.kind == "summarize" and not args.file:
        ap.error("summarize には --file が必要です")

    file_texts = []
    for f in args.file:
        p = Path(f)
        if not p.is_file():
            sys.exit(f"gask: 資料が見つかりません: {f}")
        file_texts.append((p.name, p.read_text(errors="replace")))

    model = args.model or (cfg.get("models") or {}).get(args.kind)
    prompt = build_prompt(args, file_texts)
    if args.dry_run:
        print(prompt)
        return

    key = cache_key(args, prompt, model)
    cfile = CACHE / f"{key}.json"
    if not args.no_cache and args.kind != "image" and cfile.exists():
        age_h = (time.time() - cfile.stat().st_mtime) / 3600
        if age_h < cfg["cache_hours"]:
            rec = json.loads(cfile.read_text())
            with LEDGER.open("a") as fp:
                fp.write(json.dumps({"date": dt.date.today().isoformat(), "kind": args.kind, "cached": True}) + "\n")
            print(f"[gask] キャッシュから回答（{age_h:.1f}時間前の結果・費用0）")
            print(rec["report"])
            return

    u = today_usage()
    if not args.force:
        if u["tokens"] >= cfg["daily_token_limit"]:
            sys.exit(f"gask: 今日のトークン上限 {cfg['daily_token_limit']:,} に達しました。"
                     "ユーザーに確認してから --force で実行してください")
        if args.kind == "image" and u["images"] >= cfg["daily_image_limit"]:
            sys.exit(f"gask: 今日の画像上限 {cfg['daily_image_limit']} 枚に達しました。"
                     "ユーザーに確認してから --force で実行してください")

    outdir = Path(args.workdir) / "nanobanana-output"
    before = set(outdir.glob("*")) if outdir.exists() else set()
    res = run_gemini(prompt, model, args.timeout or cfg["timeout_sec"], args.workdir)
    parsed = parse_stream(res.get("stdout", ""))
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    log = LOGS / f"{stamp}-{args.kind}-{key[:8]}.json"
    log.write_text(json.dumps({"prompt": prompt, "model": model, "run": res, "parsed": parsed},
                              ensure_ascii=False, indent=1))

    stats = parsed["stats"] or {}
    new_imgs = count_new_images(before, args.workdir) if args.kind == "image" else []
    entry = {"date": dt.date.today().isoformat(), "time": stamp, "kind": args.kind,
             "model": parsed["model"] or model, "total_tokens": stats.get("total_tokens", 0),
             "input_tokens": stats.get("input_tokens", 0), "output_tokens": stats.get("output_tokens", 0),
             "images": len(new_imgs), "sec": res.get("sec"), "log": str(log)}
    with LEDGER.open("a") as fp:
        fp.write(json.dumps(entry, ensure_ascii=False) + "\n")

    lines = []
    if res.get("error") or parsed["errors"] or (res.get("code") not in (0, None)):
        lines.append("❌ Gemini の実行に失敗しました")
        for e in ([res.get("error")] if res.get("error") else []) + parsed["errors"]:
            lines.append(f"  - {str(e)[:300]}")
        if not parsed["errors"] and res.get("stderr"):
            lines.append("  stderr: " + res["stderr"].strip().splitlines()[-1][:300])
        lines.append(f"詳細ログ: {log}")
        print("\n".join(lines))
        sys.exit(1)

    if args.kind == "image":
        lines.append("## 画像生成の結果")
        lines.append(parsed["text"].strip()[:1500])
        lines.append("\n## 確認")
        lines.append(f"- 新しくできたファイル: {', '.join(new_imgs) if new_imgs else 'なし（⚠ 生成されていない可能性）'}")
    else:
        ans = extract_json(parsed["text"])
        if ans is None:
            lines.append("⚠ 決められた形式で回答が返りませんでした。内容は未検証です。")
            lines.append(parsed["text"].strip()[:3000])
        else:
            evidence = "\n".join(str(r.get("output", "")) for r in parsed["tool_results"])
            evidence += "\n".join(t for _, t in file_texts)
            checks, stray = verify(ans, evidence)
            ok = sum(1 for c in checks if c["check"] in ("OK", "数字なし"))
            lines.append("## 回答")
            lines.append(str(ans.get("answer", "")).strip())
            lines.append(f"\n## 裏付けチェック（{ok}/{len(checks)} 件が生データと一致）")
            for c in checks:
                mark = "✅" if c["check"] in ("OK", "数字なし") else "⚠️"
                lines.append(f"- {mark} {c.get('claim')}: {c.get('value')} — {c['check']}（出典: {c.get('source')}）")
            if stray:
                lines.append(f"- ⚠️ 回答本文にあるが生データに見つからない数字: {', '.join(stray[:15])}")
            if args.kind == "analyze" and not parsed["tool_results"]:
                lines.append("- ⚠️ ツールを1回も使っていません。数字は Gemini の推測の可能性があります")
            if ans.get("unknowns"):
                lines.append("\n## 分からなかったこと\n" + "\n".join(f"- {x}" for x in ans["unknowns"]))
            if ans.get("assumptions"):
                lines.append("\n## 置いた仮定\n" + "\n".join(f"- {x}" for x in ans["assumptions"]))
    y = yen(cfg, entry["total_tokens"], entry["images"])
    u2 = today_usage()
    lines.append(f"\n[gask] {entry['sec']}秒 / {entry['total_tokens']:,} トークン"
                 + (f" / 概算 ¥{y}" if y is not None else "")
                 + f" / 今日の合計 {u2['tokens']:,} トークン・画像{u2['images']}枚"
                 + f" / 生データ: {log}")
    report = "\n".join(lines)
    if args.kind != "image":
        cfile.write_text(json.dumps({"report": report}, ensure_ascii=False))
    print(report)


if __name__ == "__main__":
    main()
