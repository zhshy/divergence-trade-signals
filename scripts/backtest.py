#!/usr/bin/env python3
"""Conservative event backtest for the divergence rule engine.

Signals are evaluated only on closed bars and entered at the next bar's open.
A-share profiles enforce T+1 exits. The default exit is the first Fibonacci
target; the test does not optimize parameters or claim predictive validity.
"""

import argparse
import csv
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import statistics
import sys

import divergence_signals as engine


def slice_frame(frame, end):
    result = {}
    for key, value in frame.items():
        result[key] = value[:end + 1] if isinstance(value, list) else value
    return result


def fresh_setup(frame, args):
    setups = engine.detect_divergences(frame, args.pivot_left, args.pivot_right, args.max_setup_age)
    setups = [engine.fibonacci_levels(frame, setup, args.tick_size) for setup in setups]
    failure = engine.detect_macd_failure(
        frame, args.pivot_left, args.pivot_right, args.max_setup_age, args.tick_size
    )
    if failure:
        setups.append(failure)
    fresh = [setup for setup in setups if setup.get("state") == "TRIGGERED"]
    return fresh[-1] if fresh else None


def first_target(frame, setup, args):
    target = engine.target_levels(
        setup["direction"], frame, "current_timeframe_backtest", args.pivot_left, args.pivot_right
    )
    if not target:
        return None
    return target["target_1_38_2_pct"]


def backtest(frame, args):
    frame = engine.add_indicators(frame)
    allow_short = args.market not in {"cn_a_stock", "cn_a_etf", "cn_a_index"}
    min_t1 = args.market in {"cn_a_stock", "cn_a_etf"}
    trades = []
    index = 60
    while index < len(frame["close"]) - 1:
        snapshot = slice_frame(frame, index)
        setup = fresh_setup(snapshot, args)
        if not setup:
            index += 1
            continue
        direction = setup["direction"]
        if direction == "short" and not allow_short:
            index += 1
            continue
        target = first_target(snapshot, setup, args)
        if target is None:
            index += 1
            continue

        entry_index = index + 1
        entry = frame["open"][entry_index]
        stop = setup["initial_stop"]
        if direction == "long" and (entry <= stop or target <= entry):
            index += 1
            continue
        if direction == "short" and (entry >= stop or target >= entry):
            index += 1
            continue

        exit_index = None
        exit_price = None
        exit_reason = None
        earliest_exit = entry_index + 1 if min_t1 else entry_index
        last_index = min(len(frame["close"]) - 1, entry_index + args.hold_bars)
        for bar in range(earliest_exit, last_index + 1):
            if direction == "long":
                stop_hit = frame["low"][bar] <= stop
                target_hit = frame["high"][bar] >= target
            else:
                stop_hit = frame["high"][bar] >= stop
                target_hit = frame["low"][bar] <= target
            if stop_hit:
                exit_price, exit_reason, exit_index = stop, "stop", bar
                break
            if target_hit:
                exit_price, exit_reason, exit_index = target, "target_1", bar
                break
        if exit_index is None:
            exit_index = last_index
            exit_price = frame["close"][exit_index]
            exit_reason = "time"

        if direction == "long":
            gross_pct = exit_price / entry - 1.0
            stop_pct = (entry - stop) / entry
        else:
            gross_pct = entry / exit_price - 1.0
            stop_pct = (stop - entry) / entry
        net_pct = gross_pct - args.cost_bps / 10000.0
        r_multiple = net_pct / stop_pct if stop_pct > 0 else 0.0
        trades.append({
            "signal_time": frame["bar_time"][index],
            "entry_time": frame["bar_time"][entry_index],
            "exit_time": frame["bar_time"][exit_index],
            "direction": direction,
            "type": setup["type"],
            "entry": round(entry, 8),
            "stop": round(stop, 8),
            "target": round(target, 8),
            "exit": round(exit_price, 8),
            "exit_reason": exit_reason,
            "net_return_pct": round(net_pct * 100.0, 4),
            "r_multiple": round(r_multiple, 4),
        })
        index = exit_index + 1

    r_values = [trade["r_multiple"] for trade in trades]
    wins = [value for value in r_values if value > 0]
    losses = [value for value in r_values if value < 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    equity, peak, max_drawdown = 0.0, 0.0, 0.0
    for value in r_values:
        equity += value
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)
    metrics = {
        "trades": len(trades),
        "win_rate_pct": round(len(wins) / len(trades) * 100.0, 2) if trades else None,
        "expectancy_r": round(statistics.mean(r_values), 4) if r_values else None,
        "total_r": round(sum(r_values), 4) if r_values else 0.0,
        "profit_factor": round(gross_profit / gross_loss, 4) if gross_loss > 0 else None,
        "max_drawdown_r": round(max_drawdown, 4),
        "stops": sum(1 for trade in trades if trade["exit_reason"] == "stop"),
    }
    return {
        "schema_version": engine.SCHEMA_VERSION,
        "strategy_version": engine.STRATEGY_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "as_of": frame["bar_time"][-1],
        "bars": len(frame["close"]),
        "market": args.market,
        "allow_short": allow_short,
        "t_plus_one": min_t1,
        "assumptions": {
            "entry": "next_bar_open",
            "exit": "first_target_or_stop_or_time",
            "same_bar_stop_and_target": "stop_first_conservative",
            "cost_bps_round_trip": args.cost_bps,
            "hold_bars": args.hold_bars,
            "note": "In-sample event test; not evidence of future returns.",
        },
        "metrics": metrics,
        "trades": trades,
    }


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv")
    parser.add_argument("--market", choices=sorted(engine.MARKET_PROFILES), default="cn_a_stock")
    parser.add_argument("--pivot-left", type=int, default=3)
    parser.add_argument("--pivot-right", type=int, default=3)
    parser.add_argument("--max-setup-age", type=int, default=20)
    parser.add_argument("--tick-size", type=float, default=0.01)
    parser.add_argument("--hold-bars", type=int, default=40)
    parser.add_argument("--cost-bps", type=float, default=0.0, help="Round-trip cost in basis points; set explicitly")
    parser.add_argument("--format", choices=["json", "text"], default="json")
    return parser


def main():
    args = build_parser().parse_args()
    try:
        frame = engine.load_ohlcv(args.csv)
        result = backtest(frame, args)
    except (ValueError, OSError, csv.Error) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 2
    if args.format == "text":
        metrics = result["metrics"]
        # 零交易时胜率与期望没有定义，打印 None 会被误读成"算不出来"，统一显示 —
        unavailable = "—"
        win_rate = (
            f"{metrics['win_rate_pct']}%" if metrics["win_rate_pct"] is not None else unavailable
        )
        expectancy = (
            f"{metrics['expectancy_r']}R" if metrics["expectancy_r"] is not None else unavailable
        )
        print(f"样本: {result['bars']} bars | 交易: {metrics['trades']}")
        print(f"胜率: {win_rate} | 期望: {expectancy} | 总R: {metrics['total_r']}R")
        print(f"最大回撤: {metrics['max_drawdown_r']}R | 止损次数: {metrics['stops']}")
        if metrics["trades"] == 0:
            print("说明: 样本内没有完成触发的结构；零交易不等于规则失效，但也不支持任何胜率结论。")
        print("声明: 这是样本内事件测试，不构成未来收益证据。")
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
