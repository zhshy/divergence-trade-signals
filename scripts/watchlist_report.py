#!/usr/bin/env python3
"""Generate the watchlist divergence report (midday / close) and its index.

Reads config/watchlist.json, fetches unadjusted daily OHLCV from Tencent, runs
scripts/divergence_signals.py for every tradable instrument, then writes
reports/<date>/watchlist-<session>.md and .json.

Exit codes: 0 = report written, 3 = no bar for the requested date (non-trading
day, nothing written), 1 = hard failure.
"""

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from fetch_watchlist_ohlcv import fetch_many


REPO_ROOT = Path(__file__).resolve().parents[1]
SHANGHAI = timezone(timedelta(hours=8))
SESSION_LABEL = {"midday": "午间", "close": "收盘"}
STATE_LABEL = {
    "TRIGGERED": "已触发",
    "WAITING": "等待触发",
    "ALREADY_TRIGGERED_DO_NOT_CHASE": "过期不追",
}


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(REPO_ROOT / "config" / "watchlist.json"))
    parser.add_argument("--session", choices=sorted(SESSION_LABEL), required=True)
    parser.add_argument("--as-of", help="Trading date, defaults to today in Asia/Shanghai")
    parser.add_argument("--reports-dir", default=str(REPO_ROOT / "reports"))
    parser.add_argument("--engine", default=str(REPO_ROOT / "scripts" / "divergence_signals.py"))
    parser.add_argument("--timeframe", default="day")
    parser.add_argument("--bars", type=int, default=400)
    parser.add_argument("--strategic-authorized", action="store_true")
    parser.add_argument("--skip-index", action="store_true")
    return parser.parse_args()


def load_watchlist(path):
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    items = payload["items"]
    signals = [item for item in items if item.get("role") == "signal"]
    contexts = [item for item in items if item.get("role") != "signal"]
    return payload, signals, contexts


def run_engine(args, csv_path, item, bar_status):
    command = [
        sys.executable, args.engine, str(csv_path),
        "--market", item["market"],
        "--symbol", item["code"],
        "--timeframe", args.timeframe,
        "--bar-status", bar_status,
        "--data-source", "Tencent public ifzq daily K-line, unadjusted",
        "--format", "json",
    ]
    command.append("--strategic-authorized" if args.strategic_authorized
                   else "--no-strategic-authorization")
    if item["market"] == "cn_a_stock":
        command += ["--tick-size", "0.01"]
    completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
    if completed.returncode != 0:
        raise RuntimeError(f"{item['code']}: engine failed\n{completed.stderr.strip()}")
    return json.loads(completed.stdout)


def build_record(item, result):
    setup = result.get("selected_setup") or {}
    state = setup.get("state")
    raw_targets = setup.get("targets") or {}
    targets = [
        raw_targets[key]
        for key in ("target_1_38_2_pct", "target_2_two_thirds_custom")
        if raw_targets.get(key) is not None
    ]
    technical = result.get("technical_context") or {}
    return {
        "code": item["code"],
        "name": item["name"],
        "market": item["market"],
        "note": item.get("note"),
        "as_of": result["as_of"],
        "last_close": result["last_close"],
        "action": result["action"],
        "setup_type": setup.get("type"),
        "setup_direction": setup.get("direction"),
        "setup_state": state,
        "state_label": STATE_LABEL.get(state, "无结构"),
        "entry_trigger": setup.get("entry_trigger"),
        "initial_stop": setup.get("initial_stop"),
        "targets": targets,
        "targets_basis": raw_targets.get("basis"),
        "rsi14": technical.get("rsi14"),
        "volume_ratio": technical.get("volume_5d_to_20d"),
        "returns_pct": technical.get("returns_pct"),
        "support": technical.get("nearest_confirmed_support"),
        "resistance": technical.get("nearest_confirmed_resistance"),
        "execution_blockers": result.get("execution_blockers") or [],
        "strategic_authorization": result.get("strategic_authorization"),
        "warnings": result.get("warnings") or [],
        "input_sha256": (result.get("data") or {}).get("input_sha256"),
        "strategy_version": result.get("strategy_version"),
    }


def fmt(value, digits=2):
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def pct(value):
    return "—" if value is None else f"{value:+.2f}%"


def summarize(records, fresh, total):
    triggered = [r for r in records if r["setup_state"] == "TRIGGERED"]
    waiting = [r for r in records if r["setup_state"] == "WAITING"]
    expired = [r for r in records if r["setup_state"] == "ALREADY_TRIGGERED_DO_NOT_CHASE"]
    no_setup = [r for r in records
                if r["setup_state"] not in ("TRIGGERED", "WAITING", "ALREADY_TRIGGERED_DO_NOT_CHASE")]
    authorized = any(r["strategic_authorization"] == "AUTHORIZED" for r in records)
    if triggered and authorized:
        headline = f"{len(triggered)} 只出现已触发结构，且战略授权已给出——仍需人工确认执行"
    elif triggered:
        headline = f"{len(triggered)} 只出现已触发结构，但没有战略授权，规则层拒绝下单"
    elif waiting:
        headline = f"{len(waiting)} 只接近触发，但还没越过触发价，今天不动"
    else:
        headline = "没有标的走到触发这一步，今天不动"
    return {
        "total": len(records),
        "triggered": len(triggered),
        "waiting": len(waiting),
        "expired": len(expired),
        "no_setup": len(no_setup),
        "fresh": fresh,
        "stale_total": total,
        "authorized": authorized,
        "headline": headline,
    }


def render_markdown(payload):
    summary = payload["summary"]
    session_zh = SESSION_LABEL[payload["session"]]
    bar_note = ("当日 K 线未收盘，收盘价可能继续变化，信号只作盘中预演"
                if payload["session"] == "midday"
                else "当日 K 线已收盘，信号基于最终收盘价")
    lines = [
        f"# 自选股背离信号 · {payload['as_of']} {session_zh}",
        "",
        f"> 期次 `{payload['session']}` ｜ 生成时间 {payload['generated_at']}",
        f"> 引擎 v{payload['engine_version']} / schema {payload['schema_version']} ｜ "
        f"数据：腾讯日线（不复权）｜ {bar_note}",
        f"> 名单：`config/watchlist.json`（{summary['total']} 只参与计算，"
        f"{len(payload['context_items'])} 只仅作背景）",
        "",
        "## 结论",
        "",
        f"**{summary['headline']}**",
        "",
        "| 项 | 值 |",
        "|---|---|",
        f"| 参与计算 | {summary['total']} 只 |",
        f"| 已触发（含被闸门拦下） | {summary['triggered']} 只 |",
        f"| 等待触发 | {summary['waiting']} 只 |",
        f"| 过期不追 | {summary['expired']} 只 |",
        f"| 无结构 | {summary['no_setup']} 只 |",
        f"| 战略授权 | {'已授权' if summary['authorized'] else 'NOT_AUTHORIZED'} |",
        f"| 数据新鲜度 | {summary['fresh']}/{summary['stale_total']} 只更新至 {payload['as_of']} |",
        "",
        "## 明细",
        "",
        "| 代码 | 名称 | 最新 | 5日 | RSI14 | 量能 5/20 | 规则动作 | 状态 | 触发 | 止损 | 目标1 |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for record in payload["items"]:
        target1 = (record["targets"] or [None])[0]
        returns = (record["returns_pct"] or {}).get("5d")
        lines.append(
            f"| {record['code']} | {record['name']} | {fmt(record['last_close'])} | "
            f"{pct(returns)} | {fmt(record['rsi14'], 1)} | {fmt(record['volume_ratio'])} | "
            f"{record['action']} | {record['state_label']} | {fmt(record['entry_trigger'])} | "
            f"{fmt(record['initial_stop'])} | {fmt(target1)} |"
        )

    lines += ["", "## 逐只要点", ""]
    for record in payload["items"]:
        title = f"### {record['name']} {record['code']} · {record['action']}"
        lines.append(title)
        lines.append("")
        setup = record["setup_type"] or "无形态"
        lines.append(
            f"- 形态 `{setup}` / 方向 {record['setup_direction'] or '—'} / "
            f"状态 {record['setup_state'] or 'NO_SETUP'}"
        )
        lines.append(
            f"- 触发 {fmt(record['entry_trigger'])} ｜ 失效 {fmt(record['initial_stop'])} ｜ "
            f"目标 {', '.join(fmt(t) for t in record['targets']) or '—'}"
        )
        lines.append(
            f"- RSI14 {fmt(record['rsi14'], 1)} ｜ 量能比 {fmt(record['volume_ratio'])} ｜ "
            f"最近支撑 {fmt(record['support'])} ｜ 最近压力 {fmt(record['resistance'])}"
        )
        if record["execution_blockers"]:
            lines.append(f"- 执行闸门拦截：{', '.join(record['execution_blockers'])}")
        else:
            lines.append("- 执行闸门：无拦截项")
        if record["note"]:
            lines.append(f"- 备注：{record['note']}")
        lines.append("")

    if payload["context_items"]:
        lines += ["## 仅作背景（不参与信号）", ""]
        for item in payload["context_items"]:
            lines.append(f"- **{item['name']} {item['code']}**：{item.get('note') or '无行情源'}")
        lines.append("")

    lines += [
        "## 执行闸门",
        "",
        "技术触发不等于执行许可。以下任一条不满足，规则层都会拒绝下单：",
        "",
        "1. K 线必须已收盘（`closed`），盘中 `forming` 只能预演；",
        "2. A 股不能裸卖空，做空信号只在有券可借时有意义；",
        "3. 位置未确认（无战略价位）时，突破信号降级为等待；",
        "4. 核心仓不使用战术入场；",
        "5. 无战略授权时，任何触发都只输出 `NO_AUTH`。",
        "",
        "本报告默认不授予战略授权：**它是观察清单，不是下单指令。**",
        "",
        "## 数据与限制",
        "",
        "- 行情为腾讯公开日线、**不复权**，除权缺口可能扭曲历史摆动点；",
        "- 未提供更高周期数据，目标价为当前周期回退值；",
        "- 无结构的结论只是“规则没说话”，不是“价格不会动”的预测；",
        "- 回测样本内交易极少，不支持任何胜率或期望收益声明。",
        "",
    ]
    return "\n".join(lines)


def rebuild_index(reports_dir):
    rows = []
    for json_path in sorted(Path(reports_dir).glob("*/watchlist-*.json")):
        try:
            payload = json.loads(json_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, KeyError):
            continue
        if "summary" not in payload:
            continue
        rows.append((
            payload["as_of"],
            1 if payload["session"] == "close" else 0,
            json_path.with_suffix(".md").relative_to(reports_dir).as_posix(),
            payload["session"],
            payload["summary"],
        ))
    rows.sort(reverse=True)
    lines = [
        "# 自选股信号报告索引",
        "",
        "每个交易日两份：午间（11:30 后）与收盘后（15:00 后）。非交易日不生成。",
        "",
        "| 日期 | 期次 | 一句话 | 报告 |",
        "|---|---|---|---|",
    ]
    for as_of, _, md_path, session, summary in rows:
        lines.append(
            f"| {as_of} | {SESSION_LABEL[session]} | {summary['headline']} | "
            f"[阅读]({md_path}) |"
        )
    lines.append("")
    (Path(reports_dir) / "README.md").write_text("\n".join(lines), encoding="utf-8")
    return len(rows)


def main():
    args = parse_args()
    as_of = args.as_of or datetime.now(SHANGHAI).strftime("%Y-%m-%d")
    config_path = Path(args.config)
    raw_config = config_path.read_bytes()
    _, signals, contexts = load_watchlist(config_path)

    date_dir = Path(args.reports_dir) / as_of
    data_dir = date_dir / "data"
    start = (datetime.strptime(as_of, "%Y-%m-%d") - timedelta(days=900)).strftime("%Y-%m-%d")
    manifest = fetch_many(
        [item["code"] for item in signals], start, as_of, as_of, args.bars, data_dir
    )
    fresh = [code for code, meta in manifest["symbols"].items() if meta["last"] == as_of]
    if not fresh:
        print(f"{as_of}: 无新增 K 线（非交易日或数据未更新），跳过报告")
        return 3

    bar_status = "forming" if args.session == "midday" else "closed"
    records = []
    for item in signals:
        csv_path = data_dir / f"{item['code']}.csv"
        result = run_engine(args, csv_path, item, bar_status)
        records.append(build_record(item, result))
        print(f"{item['code']} {item['name']}: {records[-1]['action']}")

    summary = summarize(records, len(fresh), len(signals))
    first = records[0]
    payload = {
        "schema_version": "1.0",
        "generated_at": datetime.now(SHANGHAI).isoformat(timespec="seconds"),
        "session": args.session,
        "as_of": as_of,
        "engine_version": first["strategy_version"],
        "bar_status": bar_status,
        "timeframe": args.timeframe,
        "data_source": manifest["source"],
        "data_caveat": manifest["caveat"],
        "watchlist": {
            "file": "config/watchlist.json",
            "sha256": hashlib.sha256(raw_config).hexdigest(),
            "source": json.loads(raw_config)["source"],
        },
        "summary": summary,
        "items": records,
        "context_items": contexts,
        "limitations": [
            "技术状态不构成战略授权或下单指令。",
            "A 股 T+1，且裸卖空不可行。",
            "未提供更高周期数据，目标价为当前周期回退值。",
            "无结构不等于预测价格不动。",
        ],
    }

    date_dir.mkdir(parents=True, exist_ok=True)
    json_path = date_dir / f"watchlist-{args.session}.json"
    md_path = date_dir / f"watchlist-{args.session}.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                         encoding="utf-8")
    md_path.write_text(render_markdown(payload), encoding="utf-8")
    print(f"JSON: {json_path}")
    print(f"MD:   {md_path}")
    if not args.skip_index:
        print(f"Index: {rebuild_index(args.reports_dir)} reports")
    return 0


if __name__ == "__main__":
    sys.exit(main())
