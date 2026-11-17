#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把自选股日报镜像到深度研报仓库（zhshy/deep-research-reports）并发布。

- 复制 reports/<日期>/watchlist-<session>.md 与 .json
- 目标文件名带「午间」/「收盘」标记，deep 仓库的 build_index.py 靠它生成时段徽标
- 注入 `<!-- summary: ... -->`，让索引显示一句话结论而不是「—」
- 重建 deep 仓库的 README 索引与 index.html 落地页
- 通过 scripts/ghpush.py 走 Git Data API 推送

用法：python scripts/mirror_to_deep.py --session midday [--as-of 2026-11-17] [--no-push]
Token：$GH_TOKEN，否则 `gh auth token`。
"""

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
SHANGHAI = timezone(timedelta(hours=8))
DEEP_DIR = Path(r"F:\股票分析")
DEEP_REPO = "zhshy/deep-research-reports"
SLOT_FILE = {"midday": "午间", "close": "收盘"}


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", choices=sorted(SLOT_FILE), required=True)
    parser.add_argument("--as-of", help="Defaults to today in Asia/Shanghai")
    parser.add_argument("--reports-dir", default=str(REPO_ROOT / "reports"))
    parser.add_argument("--deep-dir", default=str(DEEP_DIR))
    parser.add_argument("--no-push", action="store_true")
    parser.add_argument("--message")
    return parser.parse_args()


def token():
    value = os.environ.get("GH_TOKEN", "").strip()
    if value:
        return value
    try:
        return subprocess.run(["gh", "auth", "token"], check=True, capture_output=True,
                              text=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def mirror(source_md, source_json, source_html, target_dir, slot, payload):
    target_dir.mkdir(parents=True, exist_ok=True)
    text = source_md.read_text(encoding="utf-8")
    # 标题去掉日期与时段后缀，日期时段由索引徽标承担
    text = re.sub(r"^# .*$",
                  f"# 自选股信号 · {slot}观察",
                  text, count=1, flags=re.M)
    # 索引摘要：一句话结论 + 状态计数
    summary = payload["summary"]
    text = text.replace(
        f"# 自选股信号 · {slot}观察",
        f"# 自选股信号 · {slot}观察\n\n"
        f"<!-- summary: {summary['headline']}（已触发 {summary['triggered']} / "
        f"等待 {summary['waiting']} / 过期不追 {summary['expired']} / "
        f"无结构 {summary['no_setup']}） -->",
        1,
    )
    text = text.replace(
        f"> 期次 `{payload['session']}` ｜",
        f"> 镜像自 `zhshy/divergence-trade-signals` ｜ 期次 `{payload['session']}` ｜",
        1,
    )
    md_target = target_dir / f"自选股信号-{slot}.md"
    json_target = target_dir / f"自选股信号-{slot}.json"
    html_target = target_dir / f"自选股信号-{slot}.html"
    # 深度研报仓库统一 LF，Windows 下必须显式指定，否则会写成 CRLF
    md_target.write_text(text, encoding="utf-8", newline="\n")
    json_target.write_bytes(source_json.read_bytes().replace(b"\r\n", b"\n"))
    if source_html.exists():
        html_target.write_bytes(source_html.read_bytes().replace(b"\r\n", b"\n"))
    return md_target, json_target, html_target


def main():
    args = parse_args()
    as_of = args.as_of or datetime.now(SHANGHAI).strftime("%Y-%m-%d")
    source_dir = Path(args.reports_dir) / as_of
    source_md = source_dir / f"watchlist-{args.session}.md"
    source_json = source_dir / f"watchlist-{args.session}.json"
    source_html = source_dir / f"watchlist-{args.session}.html"
    if not source_md.exists() or not source_json.exists():
        print(f"缺少源文件：{source_md.name} / {source_json.name}，先跑 watchlist_report.py")
        return 3

    payload = json.loads(source_json.read_text(encoding="utf-8"))
    deep_dir = Path(args.deep_dir)
    md_target, json_target, html_target = mirror(
        source_md, source_json, source_html, deep_dir / "reports" / as_of,
        SLOT_FILE[args.session], payload
    )
    print(f"镜像: {md_target}")
    print(f"镜像: {json_target}")
    if source_html.exists():
        print(f"镜像: {html_target}")
    else:
        print("提示：源报告没有 HTML 版本，仅镜像 md/json")

    index = subprocess.run([sys.executable, "scripts/build_index.py"],
                           cwd=deep_dir, capture_output=True, text=True, encoding="utf-8")
    if index.returncode != 0:
        print(index.stdout, index.stderr)
        return 1
    print(f"索引: {index.stdout.strip()}")

    if args.no_push:
        print("已跳过推送（--no-push）")
        return 0

    auth = token()
    if not auth:
        print("缺少 GH_TOKEN，且 gh auth token 不可用；文件已镜像但未推送")
        return 1
    message = args.message or f"reports: {as_of} 自选股信号（{SLOT_FILE[args.session]}）"
    env = dict(os.environ, GH_TOKEN=auth, PYTHONIOENCODING="utf-8")
    push = subprocess.run(
        [sys.executable, "scripts/ghpush.py", ".", DEEP_REPO, "main", "-", message],
        cwd=deep_dir, capture_output=True, text=True, encoding="utf-8", env=env,
    )
    tail = [line for line in push.stdout.splitlines() if line.strip()][-6:]
    print("\n".join(tail))
    if push.returncode != 0:
        print(push.stderr[-800:])
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
