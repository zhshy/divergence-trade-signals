#!/usr/bin/env python3
"""Generate conditional trade signals from OHLCV CSV data.

Use confirmed pivots only, MACD(12, 26, 9), Wilder RSI(14), volume
confirmation, Fibonacci triggers, invalidation stops, and optional risk-based
position sizing. This standard-library-only script never fetches data or
places orders.
"""

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
import math
from pathlib import Path
import sys


SCHEMA_VERSION = "1.0"
STRATEGY_VERSION = "0.2.0"
REQUIRED_COLUMNS = ["open", "high", "low", "close", "volume"]
TIME_COLUMNS = ["timestamp", "datetime", "date", "time"]
MARKET_PROFILES = {
    "cn_a_stock": {
        "label": "A-share stock",
        "allow_short": False,
        "settlement": "T+1",
        "lot_size": 100,
        "intraday_round_trip": False,
    },
    "cn_a_etf": {
        "label": "A-share listed ETF",
        "allow_short": False,
        "settlement": "T+1",
        "lot_size": 100,
        "intraday_round_trip": False,
    },
    "cn_a_index": {
        "label": "A-share index (signal only)",
        "allow_short": False,
        "settlement": "not_applicable",
        "lot_size": None,
        "intraday_round_trip": False,
    },
    "generic": {
        "label": "Generic tradable instrument",
        "allow_short": True,
        "settlement": "T+0",
        "lot_size": None,
        "intraday_round_trip": True,
    },
}


def rounded(value):
    return None if value is None else round(float(value), 8)


def finite(value):
    return value is not None and math.isfinite(float(value))


def parse_timestamp(value):
    text = str(value).strip()
    if not text:
        raise ValueError("Empty timestamp")
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def load_ohlcv(path):
    """Read, sort, deduplicate and validate an OHLCV CSV file."""
    source = Path(path).expanduser()
    if not source.is_file():
        raise ValueError(f"CSV file not found: {source}")
    raw_bytes = source.read_bytes()
    input_sha256 = hashlib.sha256(raw_bytes).hexdigest()
    with io.StringIO(raw_bytes.decode("utf-8-sig"), newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError("CSV header is missing")
        fieldnames = [str(name).strip().lower() for name in reader.fieldnames]
        missing = [column for column in REQUIRED_COLUMNS if column not in fieldnames]
        if missing:
            raise ValueError(f"Missing required columns: {', '.join(missing)}")
        rows = []
        time_column = next((column for column in TIME_COLUMNS if column in fieldnames), None)
        for line_number, raw in enumerate(reader, start=2):
            row = {(str(key).strip().lower() if key else key): value for key, value in raw.items()}
            try:
                parsed = {column: float(row[column]) for column in REQUIRED_COLUMNS}
            except (TypeError, ValueError) as error:
                raise ValueError(f"Invalid numeric OHLCV value on CSV row {line_number}") from error
            if any(not math.isfinite(value) for value in parsed.values()):
                raise ValueError(f"Non-finite OHLCV value on CSV row {line_number}")
            if any(parsed[column] <= 0 for column in ["open", "high", "low", "close"]):
                raise ValueError(f"OHLC prices must be positive on CSV row {line_number}")
            if (
                parsed["high"] < parsed["low"]
                or parsed["high"] < max(parsed["open"], parsed["close"])
                or parsed["low"] > min(parsed["open"], parsed["close"])
                or parsed["volume"] < 0
            ):
                raise ValueError(f"Invalid OHLCV range on CSV row {line_number}")
            if time_column:
                try:
                    sort_time = parse_timestamp(row[time_column])
                except (TypeError, ValueError) as error:
                    raise ValueError(f"Invalid timestamp on CSV row {line_number}") from error
                parsed["_time_key"] = str(row[time_column]).strip()
                parsed["_sort_time"] = sort_time
            rows.append(parsed)

    if time_column:
        rows.sort(key=lambda row: row["_sort_time"])
        deduplicated = {}
        for row in rows:
            deduplicated[row["_time_key"]] = row
        rows = list(deduplicated.values())
        rows.sort(key=lambda row: row["_sort_time"])
    if len(rows) < 60:
        raise ValueError("At least 60 chronological bars are required")

    frame = {column: [row[column] for row in rows] for column in REQUIRED_COLUMNS}
    frame["bar_time"] = [row.get("_time_key", str(index)) for index, row in enumerate(rows)]
    frame["input_sha256"] = input_sha256
    return frame


def ema(values, span):
    alpha = 2.0 / (span + 1.0)
    output = [float(values[0])]
    for value in values[1:]:
        output.append(alpha * float(value) + (1.0 - alpha) * output[-1])
    return output


def wilder_rsi(values, period=14):
    """Calculate Wilder RSI with a simple-average seed and recursive smoothing."""
    output = [None] * len(values)
    if len(values) <= period:
        return output
    gains = [max(float(values[index]) - float(values[index - 1]), 0.0) for index in range(1, len(values))]
    losses = [max(float(values[index - 1]) - float(values[index]), 0.0) for index in range(1, len(values))]
    average_gain = sum(gains[:period]) / period
    average_loss = sum(losses[:period]) / period

    def rsi_value(gain, loss):
        if loss == 0:
            return 50.0 if gain == 0 else 100.0
        return 100.0 - 100.0 / (1.0 + gain / loss)

    output[period] = rsi_value(average_gain, average_loss)
    for index in range(period + 1, len(values)):
        average_gain = (average_gain * (period - 1) + gains[index - 1]) / period
        average_loss = (average_loss * (period - 1) + losses[index - 1]) / period
        output[index] = rsi_value(average_gain, average_loss)
    return output


def add_indicators(frame):
    result = {key: list(value) if isinstance(value, list) else value for key, value in frame.items()}
    close = result["close"]
    ema_fast = ema(close, 12)
    ema_slow = ema(close, 26)
    result["dif"] = [fast - slow for fast, slow in zip(ema_fast, ema_slow)]
    result["dea"] = ema(result["dif"], 9)
    result["macd_gap"] = [dif - dea for dif, dea in zip(result["dif"], result["dea"])]
    result["rsi"] = wilder_rsi(close, 14)
    result["trend_ema"] = ema(close, 50)
    return result


def confirmed_pivots(values, kind, left=3, right=3):
    """Return pivots confirmed by right bars; never use future-unconfirmed pivots."""
    if left < 1 or right < 1:
        raise ValueError("Pivot left and right values must be >= 1")
    pivots = []
    for index in range(left, len(values) - right):
        value = float(values[index])
        left_values = values[index - left:index]
        right_values = values[index + 1:index + right + 1]
        if kind == "high":
            is_pivot = value > max(left_values) and value > max(right_values)
        else:
            is_pivot = value < min(left_values) and value < min(right_values)
        if is_pivot:
            pivots.append(index)
    return pivots


def pivot_snapshot(frame, index, pivot_price=None):
    price = frame["close"][index] if pivot_price is None else pivot_price
    return {
        "index": int(index),
        "time": str(frame["bar_time"][index]),
        "price": rounded(price),
        "dif": rounded(frame["dif"][index]),
        "rsi": rounded(frame["rsi"][index]),
        "volume": rounded(frame["volume"][index]),
    }


def detect_divergences(frame, left, right, max_setup_age):
    """Detect regular and hidden divergence from the latest confirmed pivot pair."""
    setups = []
    latest_close = frame["close"][-1]
    trend_now = frame["trend_ema"][-1]
    trend_previous = frame["trend_ema"][-6]

    for kind, direction in [("low", "long"), ("high", "short")]:
        price_column = "low" if kind == "low" else "high"
        values = frame[price_column]
        pivots = confirmed_pivots(values, kind, left, right)
        if len(pivots) < 2:
            continue
        first, second = pivots[-2], pivots[-1]
        if len(values) - 1 - second > max_setup_age:
            continue

        price1, price2 = values[first], values[second]
        dif1, dif2 = frame["dif"][first], frame["dif"][second]
        rsi1, rsi2 = frame["rsi"][first], frame["rsi"][second]
        volume1, volume2 = frame["volume"][first], frame["volume"][second]
        if not all(finite(value) for value in [dif1, dif2, rsi1, rsi2]):
            continue

        regular = (
            direction == "long" and price2 < price1 and dif2 > dif1
        ) or (
            direction == "short" and price2 > price1 and dif2 < dif1
        )
        hidden = (
            direction == "long" and price2 > price1 and dif2 < dif1
            and latest_close > trend_now and trend_now > trend_previous
        ) or (
            direction == "short" and price2 < price1 and dif2 > dif1
            and latest_close < trend_now and trend_now < trend_previous
        )
        if not regular and not hidden:
            continue

        rsi_confirmation = rsi2 > rsi1 if direction == "long" else rsi2 < rsi1
        volume_confirmation = volume2 < volume1
        if regular:
            setup_type = "regular_bullish" if direction == "long" else "regular_bearish"
        else:
            setup_type = "hidden_bullish" if direction == "long" else "hidden_bearish"
        setups.append({
            "type": setup_type,
            "direction": direction,
            "pivot_kind": kind,
            "p1": first,
            "p2": second,
            "pivot1": pivot_snapshot(frame, first, price1),
            "pivot2": pivot_snapshot(frame, second, price2),
            "confirmations": {
                "rsi_divergence": bool(rsi_confirmation),
                "volume_divergence": bool(volume_confirmation),
            },
        })
    return setups


def crossing(frame, trigger, direction):
    """Require a fresh close-through trigger instead of chasing an old cross."""
    previous, current = frame["close"][-2], frame["close"][-1]
    if direction == "long":
        crossed = previous <= trigger and current > trigger
        already = previous > trigger and current > trigger
    else:
        crossed = previous >= trigger and current < trigger
        already = previous < trigger and current < trigger
    return crossed, already


def extreme_index(values, start, find_max):
    indexes = range(start, len(values))
    return max(indexes, key=lambda index: values[index]) if find_max else min(indexes, key=lambda index: values[index])


def fibonacci_levels(frame, setup, tick_size):
    """Convert a divergence into entry, stop and invalidation levels."""
    direction = setup["direction"]
    p1, p2 = setup["p1"], setup["p2"]
    if p2 - p1 < 3:
        setup["state"] = "INVALID_NO_INTERMEDIATE_SWING"
        return setup

    if setup["type"].startswith("regular"):
        if direction == "short":
            middle_low = min(frame["low"][p1 + 1:p2])
            anchor_index = extreme_index(frame["high"], p2, True)
            anchor = frame["high"][anchor_index]
            width = anchor - middle_low
            trigger = anchor - 0.236 * width
            stop = anchor + tick_size
        else:
            middle_high = max(frame["high"][p1 + 1:p2])
            anchor_index = extreme_index(frame["low"], p2, False)
            anchor = frame["low"][anchor_index]
            width = middle_high - anchor
            trigger = anchor + 0.236 * width
            stop = anchor - tick_size
        setup["reanchored_after_p2"] = bool(anchor_index != p2)
    else:
        anchor_index = p2
        if direction == "long":
            anchor = frame["low"][p2]
            if min(frame["low"][p2:]) < anchor:
                setup["state"] = "INVALID_NEW_LOW"
                return setup
            middle_high = max(frame["high"][p1 + 1:p2])
            width = middle_high - anchor
            trigger = anchor + 0.236 * width
            stop = anchor - tick_size
        else:
            anchor = frame["high"][p2]
            if max(frame["high"][p2:]) > anchor:
                setup["state"] = "INVALID_NEW_HIGH"
                return setup
            middle_low = min(frame["low"][p1 + 1:p2])
            width = anchor - middle_low
            trigger = anchor - 0.236 * width
            stop = anchor + tick_size
        setup["reanchored_after_p2"] = False

    if width <= 0:
        setup["state"] = "INVALID_ZERO_RANGE"
        return setup
    crossed, already = crossing(frame, trigger, direction)
    setup.update({
        "state": "TRIGGERED" if crossed else "ALREADY_TRIGGERED_DO_NOT_CHASE" if already else "WAITING",
        "entry_trigger": rounded(trigger),
        "entry_reference": rounded(frame["close"][-1] if crossed else trigger),
        "initial_stop": rounded(stop),
        "anchor_index": int(anchor_index),
        "anchor_time": str(frame["bar_time"][anchor_index]),
        "anchor_price": rounded(anchor),
        "fibonacci_trigger_pct": 23.6,
    })
    return setup


def detect_macd_failure(frame, left, right, max_setup_age, tick_size):
    """Detect a bullish near-cross failure without attaching a win-rate claim."""
    lows = confirmed_pivots(frame["low"], "low", left, right)
    if len(lows) < 2 or len(frame["low"]) < 4:
        return None
    first, second = lows[-2], lows[-1]
    if len(frame["low"]) - 1 - second > max_setup_age:
        return None
    if not frame["low"][second] < frame["low"][first]:
        return None

    gap = frame["macd_gap"][second:]
    if len(gap) < 3 or min(gap) < 0 or gap[-1] <= 0:
        return None
    if min(gap) >= gap[0] * 0.5:
        return None
    dif = frame["dif"]
    turned_up = dif[-1] > dif[-2] and dif[-2] <= dif[-3]
    if not turned_up or frame["close"][-1] <= frame["close"][-2]:
        return None
    if not all(finite(frame["rsi"][index]) for index in [first, second]):
        return None

    rsi_confirmation = frame["rsi"][second] > frame["rsi"][first]
    volume_confirmation = frame["volume"][second] < frame["volume"][first]
    return {
        "type": "macd_failure_bullish",
        "direction": "long",
        "state": "TRIGGERED",
        "entry_trigger": rounded(frame["close"][-1]),
        "entry_reference": rounded(frame["close"][-1]),
        "initial_stop": rounded(frame["low"][second] - tick_size),
        "anchor_index": int(second),
        "anchor_time": str(frame["bar_time"][second]),
        "anchor_price": rounded(frame["low"][second]),
        "pivot1": pivot_snapshot(frame, first, frame["low"][first]),
        "pivot2": pivot_snapshot(frame, second, frame["low"][second]),
        "confirmations": {
            "rsi_divergence": bool(rsi_confirmation),
            "volume_divergence": bool(volume_confirmation),
        },
        "reanchored_after_p2": False,
        "note": "Near-cross failure is an observational pattern, not a proven high-win-rate signal.",
    }


def target_levels(direction, source, basis, left, right):
    """Use the latest completed swing for 38.2% and custom two-thirds targets."""
    highs = confirmed_pivots(source["high"], "high", left, right)
    lows = confirmed_pivots(source["low"], "low", left, right)
    if direction == "short":
        if not highs:
            return None
        high_index = highs[-1]
        prior_lows = [index for index in lows if index < high_index]
        if not prior_lows:
            return None
        low_index = prior_lows[-1]
        high, low = source["high"][high_index], source["low"][low_index]
        width = high - low
        if width <= 0:
            return None
        target1 = high - 0.382 * width
        target2 = high - (2.0 / 3.0) * width
        start_index, end_index = low_index, high_index
    else:
        if not lows:
            return None
        low_index = lows[-1]
        prior_highs = [index for index in highs if index < low_index]
        if not prior_highs:
            return None
        high_index = prior_highs[-1]
        high, low = source["high"][high_index], source["low"][low_index]
        width = high - low
        if width <= 0:
            return None
        target1 = low + 0.382 * width
        target2 = low + (2.0 / 3.0) * width
        start_index, end_index = high_index, low_index
    return {
        "basis": basis,
        "swing_start_time": str(source["bar_time"][start_index]),
        "swing_end_time": str(source["bar_time"][end_index]),
        "target_1_38_2_pct": rounded(target1),
        "target_2_two_thirds_custom": rounded(target2),
    }


def add_risk_sizing(setup, args):
    """Cap the rule's 1/2/3-contract score by the account risk budget."""
    raw_size = 1 + sum(bool(value) for value in setup["confirmations"].values())
    sizing = {"raw_rule_contract_score": int(raw_size), "status": "SIGNAL_ONLY_NO_ACCOUNT_PARAMETERS"}
    required = [args.account_equity, args.risk_pct, args.point_value]
    if any(value is None for value in required):
        setup["position_sizing"] = sizing
        return setup

    entry, stop = setup["entry_reference"], setup["initial_stop"]
    risk_budget = args.account_equity * args.risk_pct / 100.0
    price_risk = abs(entry - stop) * args.point_value
    trading_cost = args.fees_per_contract + args.slippage_ticks * args.tick_size * args.point_value
    risk_per_contract = price_risk + trading_cost
    if risk_budget <= 0 or risk_per_contract <= 0:
        sizing["status"] = "INVALID_RISK_PARAMETERS"
    else:
        maximum = math.floor(risk_budget / risk_per_contract)
        sizing.update({
            "status": "CALCULATED",
            "risk_budget": rounded(risk_budget),
            "estimated_risk_per_contract": rounded(risk_per_contract),
            "max_contracts_by_risk": int(maximum),
            "contracts_after_risk_cap": int(min(raw_size, maximum)),
        })
        if maximum < 1:
            setup["state"] = "BLOCKED_RISK_BUDGET_TOO_SMALL"
    setup["position_sizing"] = sizing
    return setup


def position_management(frame, args):
    """Return explicit exit or break-even rules for an optional open position."""
    if args.position == "flat":
        return None
    close = frame["close"][-1]
    scope = getattr(args, "position_scope", "tactical")
    management = {
        "position": args.position,
        "position_scope": scope,
        "last_close": rounded(close),
        "technical_exit_applies": scope == "tactical",
    }
    if args.initial_stop is not None and scope == "tactical":
        stop = args.initial_stop
        management["initial_stop"] = stop
        if args.position == "long" and close <= stop:
            management["action"] = "EXIT_LONG_STOP"
            management["meaning"] = "Sell to exit the tactical long position at the defined stop."
            return management
        if args.position == "short" and close >= stop:
            management["action"] = "EXIT_SHORT_STOP"
            management["meaning"] = "Buy to cover the tactical short position at the defined stop."
            return management
    elif args.initial_stop is not None:
        management["initial_stop"] = args.initial_stop
        management["core_scope_note"] = "Core positions use logical invalidation, not a tactical technical stop."

    if scope == "tactical" and args.entry_price is not None and args.initial_stop is not None:
        entry, stop = args.entry_price, args.initial_stop
        initial_risk = abs(entry - stop)
        if initial_risk > 0:
            favorable = close - entry if args.position == "long" else entry - close
            r_multiple = favorable / initial_risk
            management["open_profit_r"] = round(r_multiple, 4)
            if r_multiple >= args.break_even_trigger_r:
                management["break_even_stop"] = (
                    entry + args.break_even_offset if args.position == "long" else entry - args.break_even_offset
                )
                management["break_even_rule"] = "Eligible after the configured profit threshold; include all costs in the offset."
            else:
                management["break_even_rule"] = "Do not move the stop to break even yet; never widen a losing position's stop."
    return management


def recent_levels(frame, left, right):
    """Split confirmed pivots by which side of the last close they sit on.

    A pivot low that price has already broken below is overhead supply, not
    support. Reporting it as support would tell a reader to buy into resistance,
    so the classification follows the current close rather than pivot polarity.
    """
    close = frame["close"][-1]
    highs = confirmed_pivots(frame["high"], "high", left, right)
    lows = confirmed_pivots(frame["low"], "low", left, right)
    resistance = sorted(
        {rounded(frame["high"][index]) for index in highs if frame["high"][index] > close}
    )
    support = sorted(
        {rounded(frame["low"][index]) for index in lows if frame["low"][index] < close}
    )
    return {
        "confirmed_resistance": resistance[:3],
        "confirmed_support": support[-3:],
        "basis": "classified_against_last_close",
    }


def technical_context(frame, left, right):
    """Summarize price location and indicator state for report narratives."""
    close = frame["close"][-1]
    def return_pct(bars):
        if len(frame["close"]) <= bars:
            return None
        base = frame["close"][-1 - bars]
        return round((close / base - 1.0) * 100.0, 2) if base else None

    for span in (5, 10, 20, 60):
        frame[f"ma{span}"] = [
            sum(frame["close"][max(0, index - span + 1):index + 1]) /
            len(frame["close"][max(0, index - span + 1):index + 1])
            for index in range(len(frame["close"]))
        ]
    highs = [frame["high"][index] for index in confirmed_pivots(frame["high"], "high", left, right)]
    lows = [frame["low"][index] for index in confirmed_pivots(frame["low"], "low", left, right)]
    resistances = [value for value in highs if value > close]
    supports = [value for value in lows if value < close]
    nearest_resistance = min(resistances) if resistances else None
    nearest_support = max(supports) if supports else None
    ma20 = frame["ma20"][-1]
    ma60 = frame["ma60"][-1]
    ma20_prior = frame["ma20"][-6]
    trend = "up" if close > ma20 > ma60 and ma20 > ma20_prior else (
        "down" if close < ma20 < ma60 and ma20 < ma20_prior else "mixed"
    )
    volume_5 = sum(frame["volume"][-5:]) / 5.0
    volume_20 = sum(frame["volume"][-20:]) / 20.0
    return {
        "returns_pct": {"5d": return_pct(5), "20d": return_pct(20), "60d": return_pct(60)},
        "rsi14": rounded(frame["rsi"][-1]),
        "macd": {
            "dif": rounded(frame["dif"][-1]),
            "dea": rounded(frame["dea"][-1]),
            "state": "above_signal" if frame["dif"][-1] > frame["dea"][-1] else "below_signal",
            "histogram": rounded(frame["macd_gap"][-1]),
        },
        "moving_averages": {
            "ma5": rounded(frame["ma5"][-1]),
            "ma10": rounded(frame["ma10"][-1]),
            "ma20": rounded(ma20),
            "ma60": rounded(ma60),
            "ma20_slope_5d_pct": round((ma20 / ma20_prior - 1.0) * 100.0, 2) if ma20_prior else None,
            "trend": trend,
        },
        "nearest_confirmed_support": rounded(nearest_support),
        "nearest_confirmed_resistance": rounded(nearest_resistance),
        "distance_to_support_pct": round((close / nearest_support - 1.0) * 100.0, 2) if nearest_support else None,
        "distance_to_resistance_pct": round((nearest_resistance / close - 1.0) * 100.0, 2) if nearest_resistance else None,
        "volume_5d_to_20d": round(volume_5 / volume_20, 2) if volume_20 else None,
    }


def daily_guard(args):
    """Block new entries after a configured daily loss or profit limit."""
    if args.daily_pnl is None:
        return None
    if args.daily_loss_limit is not None and args.daily_pnl <= -abs(args.daily_loss_limit):
        return {"action": "STOP_FOR_DAY_LOSS_LIMIT", "daily_pnl": args.daily_pnl}
    if args.daily_profit_target is not None and args.daily_pnl >= abs(args.daily_profit_target):
        return {"action": "STOP_FOR_DAY_PROFIT_TARGET", "daily_pnl": args.daily_pnl}
    return {"action": "ALLOW_NEW_ENTRIES", "daily_pnl": args.daily_pnl}


def price_limit_pct(market, symbol):
    """Infer the ordinary daily price limit for common mainland boards."""
    if market != "cn_a_stock":
        return None
    code = "".join(character for character in str(symbol or "") if character.isdigit())
    if code.startswith(("300", "301", "688", "689")):
        return 20.0
    if code.startswith(("4", "8", "92")):
        return 30.0
    return 10.0


def market_context(args):
    profile = dict(MARKET_PROFILES[args.market])
    if args.allow_short is not None:
        profile["allow_short"] = args.allow_short
    profile["price_limit_pct"] = price_limit_pct(args.market, args.symbol)
    profile["market"] = args.market
    return profile


def location_context(selected, args):
    """Check whether a trigger is close enough to a strategic price level."""
    levels = [float(level) for level in (args.location_level or []) if float(level) > 0]
    if not levels:
        return {
            "status": "UNCHECKED",
            "confirmed": False,
            "levels": [],
            "tolerance_pct": args.location_tolerance_pct,
        }
    trigger = selected.get("entry_trigger") if selected else None
    if trigger is None:
        return {
            "status": "NO_TRIGGER",
            "confirmed": False,
            "levels": levels,
            "tolerance_pct": args.location_tolerance_pct,
        }
    distances = [abs(float(trigger) - level) / level * 100.0 for level in levels]
    nearest_index = min(range(len(distances)), key=distances.__getitem__)
    confirmed = distances[nearest_index] <= args.location_tolerance_pct
    return {
        "status": "CONFIRMED" if confirmed else "NOT_NEAR_LEVEL",
        "confirmed": confirmed,
        "levels": levels,
        "nearest_level": levels[nearest_index],
        "distance_pct": round(distances[nearest_index], 4),
        "tolerance_pct": args.location_tolerance_pct,
    }


def apply_execution_permissions(action, args, profile, location):
    """Downgrade actions that are not executable in the selected market."""
    blockers = []
    is_entry = action in {"BUY_LONG", "SELL_SHORT"}
    if is_entry and args.bar_status != "closed":
        action = "WAIT_BAR_NOT_FINAL"
        blockers.append("BAR_NOT_CLOSED")
    if action == "SELL_SHORT" and not profile["allow_short"]:
        action = "WAIT_NO_SHORT_PERMISSION"
        blockers.append("SHORT_NOT_ALLOWED")
    if is_entry and args.require_location and not location["confirmed"]:
        action = "WAIT_LOCATION_UNCONFIRMED"
        blockers.append("LOCATION_NOT_CONFIRMED")
    if is_entry and args.position_scope == "core":
        action = "WAIT_CORE_SCOPE_NOT_TACTICAL"
        blockers.append("CORE_SCOPE_DOES_NOT_USE_TACTICAL_ENTRY")
    if is_entry and getattr(args, "strategic_authorized", None) is False:
        action = "NO_AUTH"
        blockers.append("STRATEGIC_AUTHORIZATION_MISSING")
    return action, blockers


def analyze(frame, htf_frame, args):
    frame = add_indicators(frame)
    profile = market_context(args)
    target_source = htf_frame if htf_frame is not None else frame
    target_basis = "higher_timeframe" if htf_frame is not None else "current_timeframe_fallback"
    setups = detect_divergences(frame, args.pivot_left, args.pivot_right, args.max_setup_age)
    setups = [fibonacci_levels(frame, setup, args.tick_size) for setup in setups]

    failure = detect_macd_failure(frame, args.pivot_left, args.pivot_right, args.max_setup_age, args.tick_size)
    if failure:
        setups.append(failure)

    complete = []
    for setup in setups:
        if "entry_trigger" not in setup:
            continue
        setup["targets"] = target_levels(
            setup["direction"], target_source, target_basis, args.pivot_left, args.pivot_right
        )
        complete.append(add_risk_sizing(setup, args))

    triggered = [setup for setup in complete if setup["state"] == "TRIGGERED"]
    waiting = [setup for setup in complete if setup["state"] == "WAITING"]
    selected = triggered[-1] if triggered else waiting[-1] if waiting else complete[-1] if complete else None
    location = location_context(selected, args)
    action = "WAIT"
    if selected:
        if selected["state"] == "TRIGGERED":
            action = "BUY_LONG" if selected["direction"] == "long" else "SELL_SHORT"
        elif selected["state"] == "WAITING":
            action = "WATCH_LONG" if selected["direction"] == "long" else "WATCH_SHORT"
        elif selected["state"] == "BLOCKED_RISK_BUDGET_TOO_SMALL":
            action = "WAIT_RISK_BUDGET_TOO_SMALL"
        else:
            action = "WAIT_DO_NOT_CHASE"

    management = position_management(frame, args)
    if management and management.get("action"):
        action = management["action"]
    elif management:
        position_direction = args.position
        matching = [
            setup for setup in complete
            if setup["direction"] == position_direction and setup.get("targets")
        ]
        if matching:
            targets = matching[-1]["targets"]
            target1 = targets["target_1_38_2_pct"]
            target2 = targets["target_2_two_thirds_custom"]
            close = frame["close"][-1]
            if position_direction == "long" and close >= target2:
                action = "SELL_LONG_TARGET_2"
            elif position_direction == "long" and close >= target1:
                action = "SELL_LONG_TARGET_1"
            elif position_direction == "short" and close <= target2:
                action = "BUY_COVER_TARGET_2"
            elif position_direction == "short" and close <= target1:
                action = "BUY_COVER_TARGET_1"
        if not action.startswith(("SELL_LONG_TARGET", "BUY_COVER_TARGET")):
            if position_direction == "long" and any(setup["direction"] == "short" for setup in triggered):
                action = "EXIT_LONG_OPPOSITE_SIGNAL"
            elif position_direction == "short" and any(setup["direction"] == "long" for setup in triggered):
                action = "EXIT_SHORT_OPPOSITE_SIGNAL"

    guard = daily_guard(args)
    position_exit = action.startswith(("EXIT_", "SELL_LONG_TARGET", "BUY_COVER_TARGET"))
    if guard and guard["action"] != "ALLOW_NEW_ENTRIES" and not position_exit:
        action = guard["action"]
    action, execution_blockers = apply_execution_permissions(action, args, profile, location)

    warnings = [
        "Signals are rule outputs, not guaranteed predictions; divergence can persist in a strong trend.",
        "MACD and RSI are correlated price-derived confirmations, not independent evidence.",
        "Check the listed higher-timeframe support/resistance before treating a setup as location-confirmed.",
    ]
    if htf_frame is None:
        warnings.append("No higher-timeframe CSV supplied; targets use a clearly marked current-timeframe fallback.")
    if any(setup.get("reanchored_after_p2") for setup in complete):
        warnings.append("A post-divergence new extreme was found; Fibonacci levels were re-anchored as required.")
    if not profile["allow_short"]:
        warnings.append("Short entries are disabled for this market profile; bearish divergence is exit/reduce information only.")
    if args.bar_status != "closed":
        warnings.append("The latest bar is not final; new entries are blocked until the bar closes.")
    if args.require_location and location["status"] != "CONFIRMED":
        warnings.append("A strategic location level is required, but the trigger is not close enough to any supplied level.")
    if args.position_scope == "core":
        warnings.append("Core position scope is active; tactical technical exits are suppressed.")

    return {
        "schema_version": SCHEMA_VERSION,
        "strategy_version": STRATEGY_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "as_of": str(frame["bar_time"][-1]),
        "bars": len(frame["close"]),
        "last_close": rounded(frame["close"][-1]),
        "action": action,
        "execution_blockers": execution_blockers,
        "selected_setup": selected,
        "all_setups": complete,
        "position_management": management,
        "daily_guard": guard,
        "key_levels": recent_levels(target_source, args.pivot_left, args.pivot_right),
        "technical_context": technical_context(frame, args.pivot_left, args.pivot_right),
        "instrument": {
            "symbol": args.symbol,
            "signal_symbol": args.signal_symbol or args.symbol,
            "execution_symbol": args.execution_symbol or args.symbol,
        },
        "market_profile": profile,
        "strategic_authorization": (
            "AUTHORIZED" if args.strategic_authorized is True
            else "NOT_AUTHORIZED" if args.strategic_authorized is False
            else "UNSPECIFIED"
        ),
        "bar_status": args.bar_status,
        "timeframe": args.timeframe,
        "position_scope": args.position_scope,
        "location": location,
        "data": {
            "source": args.data_source,
            "input_sha256": frame.get("input_sha256"),
            "as_of": str(frame["bar_time"][-1]),
        },
        "parameters": {
            "macd": [12, 26, 9],
            "rsi": 14,
            "pivot_left": args.pivot_left,
            "pivot_right": args.pivot_right,
            "tick_size": args.tick_size,
            "max_setup_age_bars": args.max_setup_age,
        },
        "warnings": warnings,
    }


def text_report(result):
    lines = [
        f"时间: {result['as_of']}",
        f"最新收盘: {result['last_close']}",
        f"规则动作: {result['action']}",
    ]
    setup = result.get("selected_setup")
    if setup:
        lines.extend([
            f"形态: {setup['type']} | 状态: {setup['state']}",
            f"触发价: {setup['entry_trigger']} | 初始失效/止损: {setup['initial_stop']}",
            f"RSI确认: {setup['confirmations']['rsi_divergence']} | 成交量确认: {setup['confirmations']['volume_divergence']}",
        ])
        if setup.get("targets"):
            targets = setup["targets"]
            lines.append(
                f"目标1(38.2%): {targets['target_1_38_2_pct']} | "
                f"目标2(自定义2/3): {targets['target_2_two_thirds_custom']} | 依据: {targets['basis']}"
            )
        sizing = setup.get("position_sizing", {})
        if sizing.get("status") == "CALCULATED":
            lines.append(
                f"风险预算上限: {sizing['max_contracts_by_risk']}手 | 规则分数经风险限制后: "
                f"{sizing['contracts_after_risk_cap']}手"
            )
    if result.get("position_management"):
        lines.append(f"持仓管理: {json.dumps(result['position_management'], ensure_ascii=False)}")
    lines.append("提示: 触发表示规则条件成立，不代表必然盈利；已穿越很久的信号不追价。")
    return "\n".join(lines)


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", help="Chronological OHLCV CSV file")
    parser.add_argument("--htf-file", help="Optional higher-timeframe OHLCV CSV for targets and key levels")
    parser.add_argument("--market", choices=sorted(MARKET_PROFILES), default="generic")
    parser.add_argument("--symbol", help="Signal instrument code, e.g. sz300750")
    parser.add_argument("--signal-symbol", help="Instrument represented by the CSV")
    parser.add_argument("--execution-symbol", help="Tradable instrument used to execute the signal")
    parser.add_argument("--timeframe", default="day", help="Bar timeframe label, e.g. day or m60")
    parser.add_argument("--bar-status", choices=["closed", "forming"], default="closed")
    parser.add_argument("--data-source", default="unspecified")
    parser.add_argument("--allow-short", action="store_true", default=None)
    parser.add_argument("--position-scope", choices=["core", "tactical"], default="tactical")
    authorization = parser.add_mutually_exclusive_group()
    authorization.add_argument("--strategic-authorized", dest="strategic_authorized", action="store_true")
    authorization.add_argument("--no-strategic-authorization", dest="strategic_authorized", action="store_false")
    parser.set_defaults(strategic_authorized=None)
    parser.add_argument("--location-level", type=float, action="append", help="Repeatable strategic price level")
    parser.add_argument("--location-tolerance-pct", type=float, default=3.0)
    parser.add_argument("--require-location", action="store_true")
    parser.add_argument("--pivot-left", type=int, default=3)
    parser.add_argument("--pivot-right", type=int, default=3)
    parser.add_argument("--max-setup-age", type=int, default=20)
    parser.add_argument("--tick-size", type=float, default=0.01)
    parser.add_argument("--account-equity", type=float)
    parser.add_argument("--risk-pct", type=float, help="Risk budget per trade as percent, e.g. 0.5")
    parser.add_argument("--point-value", type=float, help="Currency value of a 1.0 price move per contract")
    parser.add_argument("--fees-per-contract", type=float, default=0.0)
    parser.add_argument("--slippage-ticks", type=float, default=1.0)
    parser.add_argument("--daily-pnl", type=float, help="Realized plus marked daily PnL in account currency")
    parser.add_argument("--daily-loss-limit", type=float, help="Positive account-currency loss limit")
    parser.add_argument("--daily-profit-target", type=float, help="Positive account-currency daily stop target")
    parser.add_argument("--position", choices=["flat", "long", "short"], default="flat")
    parser.add_argument("--entry-price", type=float)
    parser.add_argument("--initial-stop", type=float)
    parser.add_argument("--break-even-trigger-r", type=float, default=1.0)
    parser.add_argument("--break-even-offset", type=float, default=0.0, help="Price points beyond entry, not ticks")
    parser.add_argument("--format", choices=["json", "text"], default="json")
    return parser


def main():
    args = build_parser().parse_args()
    if args.tick_size <= 0:
        print(json.dumps({"error": "tick-size must be positive"}), file=sys.stderr)
        return 2
    try:
        frame = load_ohlcv(args.csv)
        htf_frame = load_ohlcv(args.htf_file) if args.htf_file else None
        result = analyze(frame, htf_frame, args)
    except (ValueError, OSError, csv.Error) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 2
    output = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
    print(text_report(result) if args.format == "text" else output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
