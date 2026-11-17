#!/usr/bin/env python3
"""Unit tests for the deterministic divergence signal engine."""

import argparse
from datetime import datetime, timedelta
import importlib.util
from pathlib import Path
import tempfile
import unittest


MODULE_PATH = Path(__file__).with_name("divergence_signals.py")
SPEC = importlib.util.spec_from_file_location("divergence_signals", MODULE_PATH)
engine = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(engine)


def synthetic_frame(size=30):
    return {
        "open": [100.0] * size,
        "high": [105.0] * size,
        "low": [95.0] * size,
        "close": [100.0] * size,
        "volume": [100.0] * size,
        "bar_time": [str(index) for index in range(size)],
        "dif": [0.0] * size,
        "dea": [0.0] * size,
        "macd_gap": [0.0] * size,
        "rsi": [50.0] * size,
        "trend_ema": [100.0] * size,
    }


def risk_args(**overrides):
    values = {
        "account_equity": None,
        "risk_pct": None,
        "point_value": None,
        "tick_size": 0.01,
        "fees_per_contract": 0.0,
        "slippage_ticks": 0.0,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


class DivergenceEngineTests(unittest.TestCase):
    def test_pivot_requires_right_side_confirmation(self):
        values = [3.0, 2.0, 1.0, 2.0, 3.0, 0.5]
        self.assertEqual(engine.confirmed_pivots(values, "low", 2, 2), [2])

    def test_wilder_rsi_on_rising_market(self):
        result = engine.wilder_rsi([float(value) for value in range(1, 40)], 14)
        self.assertIsNone(result[13])
        self.assertEqual(result[14], 100.0)
        self.assertEqual(result[-1], 100.0)

    def test_regular_bullish_trigger_and_confirmations(self):
        frame = synthetic_frame()
        frame["low"][8] = 90.0
        frame["low"][18] = 80.0
        frame["high"][13] = 110.0
        frame["dif"][8] = -2.0
        frame["dif"][18] = -1.0
        frame["rsi"][8] = 30.0
        frame["rsi"][18] = 40.0
        frame["volume"][18] = 80.0
        frame["close"][28] = 86.0
        frame["high"][28] = 88.0
        frame["low"][28] = 85.0
        frame["close"][29] = 88.0
        frame["high"][29] = 90.0
        frame["low"][29] = 85.0

        setups = engine.detect_divergences(frame, 2, 2, 20)
        self.assertEqual(len(setups), 1)
        setup = engine.fibonacci_levels(frame, setups[0], 0.01)
        self.assertEqual(setup["type"], "regular_bullish")
        self.assertEqual(setup["state"], "TRIGGERED")
        self.assertAlmostEqual(setup["entry_trigger"], 87.08)
        self.assertEqual(setup["initial_stop"], 79.99)
        self.assertTrue(setup["confirmations"]["rsi_divergence"])
        self.assertTrue(setup["confirmations"]["volume_divergence"])
        self.assertEqual(setup["pivot2"]["price"], 80.0)

    def test_regular_bearish_trigger(self):
        frame = synthetic_frame()
        frame["high"][8] = 110.0
        frame["high"][18] = 120.0
        frame["low"][13] = 90.0
        frame["dif"][8] = 2.0
        frame["dif"][18] = 1.0
        frame["rsi"][8] = 60.0
        frame["rsi"][18] = 50.0
        frame["volume"][18] = 80.0
        frame["close"][28] = 113.0
        frame["high"][28] = 114.0
        frame["low"][28] = 110.0
        frame["close"][29] = 111.0
        frame["high"][29] = 113.0
        frame["low"][29] = 110.0

        setups = engine.detect_divergences(frame, 2, 2, 20)
        setup = engine.fibonacci_levels(frame, setups[0], 0.01)
        self.assertEqual(setup["type"], "regular_bearish")
        self.assertEqual(setup["state"], "TRIGGERED")
        self.assertAlmostEqual(setup["entry_trigger"], 112.92)
        self.assertEqual(setup["initial_stop"], 120.01)

    def test_position_size_is_capped_by_risk(self):
        setup = {
            "entry_reference": 86.0,
            "initial_stop": 79.99,
            "confirmations": {"rsi_divergence": True, "volume_divergence": True},
        }
        args = risk_args(account_equity=10000.0, risk_pct=1.0, point_value=1.0)
        result = engine.add_risk_sizing(setup, args)
        self.assertEqual(result["position_sizing"]["raw_rule_contract_score"], 3)
        self.assertEqual(result["position_sizing"]["max_contracts_by_risk"], 16)
        self.assertEqual(result["position_sizing"]["contracts_after_risk_cap"], 3)

    def test_position_is_blocked_when_one_contract_exceeds_budget(self):
        setup = {
            "state": "TRIGGERED",
            "entry_reference": 86.0,
            "initial_stop": 79.99,
            "confirmations": {"rsi_divergence": True, "volume_divergence": True},
        }
        args = risk_args(account_equity=100.0, risk_pct=0.1, point_value=1.0)
        result = engine.add_risk_sizing(setup, args)
        self.assertEqual(result["state"], "BLOCKED_RISK_BUDGET_TOO_SMALL")
        self.assertEqual(result["position_sizing"]["contracts_after_risk_cap"], 0)

    def test_long_position_stop_exit(self):
        frame = synthetic_frame()
        frame["close"][-1] = 89.0
        args = argparse.Namespace(
            position="long", initial_stop=90.0, entry_price=100.0,
            break_even_trigger_r=1.0, break_even_offset=0.0,
        )
        management = engine.position_management(frame, args)
        self.assertEqual(management["action"], "EXIT_LONG_STOP")

    def test_break_even_requires_profit_threshold(self):
        frame = synthetic_frame()
        frame["close"][-1] = 106.0
        args = argparse.Namespace(
            position="long", initial_stop=95.0, entry_price=100.0,
            break_even_trigger_r=1.0, break_even_offset=0.1,
        )
        management = engine.position_management(frame, args)
        self.assertAlmostEqual(management["open_profit_r"], 1.2)
        self.assertEqual(management["break_even_stop"], 100.1)

    def test_csv_loader_sorts_and_validates(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.csv"
            rows = ["timestamp,open,high,low,close,volume"]
            for index in range(60):
                price = 100.0 + index * 0.1
                day = (datetime(2026, 1, 1) + timedelta(days=index)).date().isoformat()
                rows.append(f"{day},{price},{price + 1},{price - 1},{price},100")
            path.write_text("\n".join(rows), encoding="utf-8")
            frame = engine.load_ohlcv(path)
            self.assertEqual(len(frame["close"]), 60)
            self.assertEqual(frame["close"][-1], 105.9)

    def test_daily_loss_limit_blocks_new_entries(self):
        args = argparse.Namespace(
            daily_pnl=-100.0, daily_loss_limit=100.0, daily_profit_target=300.0,
        )
        self.assertEqual(engine.daily_guard(args)["action"], "STOP_FOR_DAY_LOSS_LIMIT")

    def test_daily_profit_target_stops_new_entries(self):
        args = argparse.Namespace(
            daily_pnl=300.0, daily_loss_limit=100.0, daily_profit_target=300.0,
        )
        self.assertEqual(engine.daily_guard(args)["action"], "STOP_FOR_DAY_PROFIT_TARGET")

    def test_empty_csv_has_clean_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "empty.csv"
            path.write_text("timestamp,open,high,low,close,volume\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "At least 60"):
                engine.load_ohlcv(path)

    def test_cn_a_profile_blocks_short_and_forming_bar(self):
        args = argparse.Namespace(
            market="cn_a_stock", allow_short=None, symbol="sz300750",
            position_scope="tactical", bar_status="forming", require_location=False,
        )
        profile = engine.market_context(args)
        self.assertFalse(profile["allow_short"])
        self.assertEqual(profile["price_limit_pct"], 20.0)
        location = {"confirmed": False}
        action, blockers = engine.apply_execution_permissions(
            "BUY_LONG", args, profile, location
        )
        self.assertEqual(action, "WAIT_BAR_NOT_FINAL")
        self.assertIn("BAR_NOT_CLOSED", blockers)

    def test_short_permission_block_is_explicit(self):
        args = argparse.Namespace(
            market="cn_a_stock", allow_short=None, symbol="sz300750",
            position_scope="tactical", bar_status="closed", require_location=False,
        )
        profile = engine.market_context(args)
        action, blockers = engine.apply_execution_permissions(
            "SELL_SHORT", args, profile, {"confirmed": False}
        )
        self.assertEqual(action, "WAIT_NO_SHORT_PERMISSION")
        self.assertIn("SHORT_NOT_ALLOWED", blockers)

    def test_location_filter(self):
        selected = {"entry_trigger": 100.5}
        args = argparse.Namespace(location_level=[100.0, 90.0], location_tolerance_pct=1.0)
        result = engine.location_context(selected, args)
        self.assertTrue(result["confirmed"])
        self.assertEqual(result["nearest_level"], 100.0)

    def test_core_position_ignores_tactical_stop(self):
        frame = synthetic_frame()
        frame["close"][-1] = 80.0
        args = argparse.Namespace(
            position="long", position_scope="core", initial_stop=90.0,
            entry_price=100.0, break_even_trigger_r=1.0, break_even_offset=0.0,
        )
        management = engine.position_management(frame, args)
        self.assertFalse(management["technical_exit_applies"])
        self.assertNotIn("action", management)


class BacktestConservatismTests(unittest.TestCase):
    """The backtest must not flatter the rules through optimistic assumptions."""

    def build_frame(self, size=90):
        frame = {
            "open": [100.0] * size,
            "high": [101.0] * size,
            "low": [99.0] * size,
            "close": [100.0] * size,
            "volume": [1000.0] * size,
            "bar_time": [str(index) for index in range(size)],
        }
        return engine.add_indicators(frame)

    def args(self, **overrides):
        values = {
            "market": "cn_a_stock",
            "pivot_left": 3,
            "pivot_right": 3,
            "max_setup_age": 20,
            "tick_size": 0.01,
            "hold_bars": 20,
            "cost_bps": 8.0,
        }
        values.update(overrides)
        return argparse.Namespace(**values)

    def forced_setup(self, direction="long"):
        return {
            "direction": direction,
            "type": "regular_bullish" if direction == "long" else "regular_bearish",
            "state": "TRIGGERED",
            "initial_stop": 98.0 if direction == "long" else 102.0,
            "p1": 40,
            "p2": 55,
        }

    def test_entry_uses_next_bar_open_and_respects_t_plus_one(self):
        import backtest as bt

        frame = self.build_frame()
        original_fresh, original_target = bt.fresh_setup, bt.first_target
        bt.fresh_setup = lambda snapshot, args: (
            self.forced_setup() if len(snapshot["close"]) == 61 else None
        )
        bt.first_target = lambda snapshot, setup, args: 120.0
        try:
            result = bt.backtest(frame, self.args())
        finally:
            bt.fresh_setup, bt.first_target = original_fresh, original_target

        self.assertEqual(len(result["trades"]), 1)
        trade = result["trades"][0]
        self.assertEqual(trade["signal_time"], "60")
        self.assertEqual(trade["entry"], 100.0)
        self.assertEqual(trade["entry_time"], "61")
        self.assertGreaterEqual(int(trade["exit_time"]), 62)

    def test_stop_wins_when_stop_and_target_share_a_bar(self):
        import backtest as bt

        frame = self.build_frame()
        frame["low"][70] = 95.0
        frame["high"][70] = 125.0
        original_fresh, original_target = bt.fresh_setup, bt.first_target
        bt.fresh_setup = lambda snapshot, args: (
            self.forced_setup() if len(snapshot["close"]) == 61 else None
        )
        bt.first_target = lambda snapshot, setup, args: 120.0
        try:
            result = bt.backtest(frame, self.args())
        finally:
            bt.fresh_setup, bt.first_target = original_fresh, original_target

        self.assertEqual(result["trades"][0]["exit_reason"], "stop")
        self.assertLess(result["trades"][0]["r_multiple"], 0)

    def test_empty_sample_reports_zero_trades_without_claiming_a_win_rate(self):
        import backtest as bt

        result = bt.backtest(self.build_frame(), self.args())
        self.assertEqual(result["metrics"]["trades"], 0)
        self.assertIsNone(result["metrics"]["win_rate_pct"])
        self.assertIsNone(result["metrics"]["expectancy_r"])
        self.assertEqual(result["assumptions"]["same_bar_stop_and_target"], "stop_first_conservative")

    def test_cn_a_short_is_not_backtested(self):
        import backtest as bt

        frame = self.build_frame()
        original_fresh, original_target = bt.fresh_setup, bt.first_target
        bt.fresh_setup = lambda snapshot, args: (
            self.forced_setup("short") if len(snapshot["close"]) == 61 else None
        )
        bt.first_target = lambda snapshot, setup, args: 80.0
        try:
            result = bt.backtest(frame, self.args())
        finally:
            bt.fresh_setup, bt.first_target = original_fresh, original_target

        self.assertEqual(result["metrics"]["trades"], 0)
        self.assertFalse(result["allow_short"])


if __name__ == "__main__":
    unittest.main()
