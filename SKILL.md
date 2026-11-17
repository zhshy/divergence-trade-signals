---
name: divergence-trade-signals
description: Analyze OHLCV market data with confirmed MACD/RSI/volume divergence and Fibonacci rules to produce conditional long, short, exit, stop, target, position-size, and daily-risk signals under an explicit execution-permission gate. This skill should be used when the user asks when to buy, sell, go long, go short, take profit, or manage a position using the David-style MACD divergence trading rules, or asks for a leader-stock tactical snapshot, or asks whether a divergence setup is actionable today. It accepts local CSV files or OHLCV data obtained from connected financial sources; it does not auto-trade or claim guaranteed profitability.
agent_created: true
---

# Divergence Trade Signals

## Purpose

Convert the supplied discretionary trading rules into deterministic, reproducible signals. Use the bundled standard-library Python engine for all calculations rather than visually estimating MACD, RSI, pivots, Fibonacci levels, or position size.

Treat every entry as a conditional rule output, not a certain prediction. Never place an order, invent live prices, or describe an unconfirmed pivot as confirmed.

**Core design principle: a technical trigger is not an execution permission.** Thesis quality, position scope, market rules, and location context each independently gate whether a `TRIGGERED` setup may become an order. When any gate fails, the engine must return a blocking action — never a buy/sell instruction.

## Architecture: Four Separated Layers

| Layer | Owner | Question it answers | Failure mode if collapsed |
|---|---|---|---|
| Strategic thesis | Research report | Is this asset worth owning at all? | Chasing a good chart in a bad business |
| Authorization | Research report, explicit | Is a tactical tranche approved, and how much? | Technical signal silently overrides the thesis |
| Tactical trigger | This skill | Is there a confirmed, location-valid entry now? | Discretionary "it looks ready" replaces rules |
| Execution | Broker / user | Can this order legally and physically be placed? | T+1, lot size, or short-sale ban violated |

Only the tactical layer is computed here. The other three are **inputs** (`--strategic-authorized`, `--position-scope`, `--market`), and the engine refuses to emit an actionable order when they are absent or contradictory.

## Workflow

1. Obtain chronological OHLCV data with at least 60 bars. Require `open,high,low,close,volume`; accept an optional `timestamp`, `datetime`, `date`, or `time` column. Record the symbol, timeframe, source, data timestamp, and timezone in the response.
2. Prefer OHLCV from a connected financial data source when current-market analysis is requested. Otherwise request a CSV file. Do not fabricate missing bars or silently mix sources and timeframes. When a source fails, degrade explicitly and label the substitute — never backfill from memory.
3. Declare the market profile with `--market`. Default assumptions differ sharply by market; guessing is a defect, not a convenience.
4. Set `--bar-status closed` unless the user explicitly wants an intraday preview. An unclosed daily bar can flip its close and invalidate the trigger.
5. Supply a higher-timeframe CSV with `--htf-file` whenever possible. Use it for completed-swing targets and support/resistance context. If omitted, disclose that targets use the current-timeframe fallback.
6. Run `scripts/divergence_signals.py` and inspect JSON output. Use `--format text` only for a quick human-readable summary; retain JSON for detailed explanation.
7. Check `execution_blockers` first, then `selected_setup.state`, then `location`, then the higher-timeframe key levels and data freshness. A fresh trigger without nearby support/resistance is not location-confirmed; state this limitation explicitly.
8. Report the action, pattern, trigger, invalidation/stop, targets, RSI/volume confirmations, risk-capped size, and reason for waiting or blocking. Distinguish opening a short from selling an existing long.
9. Run `scripts/test_divergence_signals.py` after modifying the engine. Do not change thresholds merely to force a buy or sell result.

## Market Profiles

Selected with `--market`; the profile decides whether a signal is even expressible.

| Profile | Short allowed | Settlement | Lot size | Default price limit |
|---|---|---|---|---|
| `cn_a_stock` | No | T+1 | 100 | 10% main board / 20% ChiNext & STAR / 30% BSE |
| `cn_a_etf` | No | T+1 | 100 | 10% |
| `cn_a_index` | No | T+1 | 1 | none (index is not directly tradable) |
| `generic` | Yes | T+0 | 1 | none |

`--allow-short` can override the profile only when the user has stated they hold securities-lending capacity. Without that, a bearish divergence in A-shares is **information, not a trade** — the engine returns `WAIT_NO_SHORT_PERMISSION` with blocker `SHORT_NOT_ALLOWED`, and the correct output is "this tells you not to add, not to sell short".

## Strategic Authorization State Machine

The engine exposes a single boolean gate, reported as `strategic_authorization`:

| Field value | Flag | Permitted output |
|---|---|---|
| `AUTHORIZED` | `--strategic-authorized` | `BUY_LONG` allowed if the tactical gates also pass |
| `NOT_AUTHORIZED` | `--no-strategic-authorization` (default) | Entry downgraded to `NO_AUTH` |
| `UNSPECIFIED` | flag omitted | Treated as unauthorized for entries; report the ambiguity |

The wider five-state machine — `NO_AUTH` → `AUTH_WAIT` → `AUTH_TRIGGERED` / `CONFLICT_WAIT` → `EXPIRED` — belongs to the research layer (`deep-dive-investment-report`), which owns tranche approval and expiry. Pass its conclusion in through the flag; do not attempt to infer authorization from chart quality.

A technical state of `TRIGGERED` with authorization `NOT_AUTHORIZED` must never render as a buy. Report both fields so the reader sees the disagreement.

## Core vs Tactical Position Scope

`--position-scope core` disables technical stops entirely: a core holding is exited on thesis break, not on a 2-ATR wiggle. `--position-scope tactical` applies the full stop/target logic. Mixing the two produces the classic failure of stopping out of a long-term holding on a short-term chart. Never apply a tactical stop to a core position, and never let core conviction justify an oversized tactical entry.

## Location Filter

A divergence is only actionable near a structural level. Provide `--location-level` (a price) plus `--location-tolerance-pct`, and add `--require-location` to block entries when no level is supplied. Without a location, a valid pattern in the middle of nowhere is a lower-quality trade; the engine marks it and, under `--require-location`, blocks it.

## Commands

Resolve the skill directory and run the script with absolute input paths.

Basic analysis:

```bash
python3 scripts/divergence_signals.py /absolute/path/ohlcv.csv --format text
```

A-share stock, closed bar, no authorization, location-gated:

```bash
python3 scripts/divergence_signals.py /absolute/path/sz300750.csv \
  --market cn_a_stock --symbol sz300750 \
  --timeframe day --bar-status closed \
  --position-scope tactical \
  --no-strategic-authorization \
  --location-level 238.0 --location-tolerance-pct 1.5 --require-location \
  --tick-size 0.01 --format json
```

Higher-timeframe targets and risk-capped sizing:

```bash
python3 scripts/divergence_signals.py /absolute/path/intraday.csv \
  --htf-file /absolute/path/hourly.csv \
  --tick-size 0.25 --point-value 20 \
  --account-equity 100000 --risk-pct 0.5 \
  --fees-per-contract 5 --slippage-ticks 1
```

Manage an existing position and enforce a daily circuit breaker:

```bash
python3 scripts/divergence_signals.py /absolute/path/intraday.csv \
  --position long --entry-price 100 --initial-stop 98 \
  --break-even-trigger-r 1 --break-even-offset 0.1 \
  --daily-pnl -100 --daily-loss-limit 100 --daily-profit-target 300
```

Conservative event backtest (next-open entry, stop-before-target, T+1 earliest exit, no in-sample tuning):

```bash
python3 scripts/backtest.py /absolute/path/ohlcv.csv \
  --market cn_a_stock --symbol sz300750 \
  --cost-bps 8 --output /absolute/path/backtest.json
```

Validate the installation:

```bash
python3 scripts/test_divergence_signals.py
```

The scripts use only the Python standard library. `assets/example_asset.txt` contains a copyable CSV schema example.

## Action Interpretation

Read `execution_blockers` before the action: a non-empty list means the action is suppressed regardless of the pattern.

- `BUY_LONG`: a long rule has just triggered on the latest confirmed close, authorization and location both passed.
- `SELL_SHORT`: a short rule has just triggered; this is not an instruction to sell an existing long. Requires `allow_short`.
- `WATCH_LONG` / `WATCH_SHORT`: divergence exists but the fresh Fibonacci trigger has not occurred.
- `WAIT_DO_NOT_CHASE`: the trigger occurred earlier, or the pattern is a bearish structure in a no-short market; do not present it as a fresh entry.
- `NO_AUTH` (`STRATEGIC_AUTHORIZATION_MISSING`): the pattern may be valid but no strategic tranche is approved.
- `WAIT_LOCATION_UNCONFIRMED` (`LOCATION_NOT_CONFIRMED`): the pattern is valid but sits away from a structural level.
- `WAIT_BAR_NOT_FINAL` (`BAR_NOT_CLOSED`): the trigger fired on an unclosed bar and may still flip.
- `WAIT_NO_SHORT_PERMISSION` (`SHORT_NOT_ALLOWED`): bearish pattern in a market that cannot be shorted; exit/reduce information only.
- `WAIT_CORE_SCOPE_NOT_TACTICAL` (`CORE_SCOPE_DOES_NOT_USE_TACTICAL_ENTRY`): a core holding must not be opened on a tactical trigger.
- `WAIT_RISK_BUDGET_TOO_SMALL`: one lot exceeds the configured risk budget.
- `SELL_LONG_TARGET_1` / `SELL_LONG_TARGET_2`: sell an existing long at the corresponding target.
- `BUY_COVER_TARGET_1` / `BUY_COVER_TARGET_2`: buy to cover an existing short at the corresponding target.
- `EXIT_LONG_STOP` / `EXIT_SHORT_STOP`: exit at the user-supplied initial stop.
- `EXIT_LONG_OPPOSITE_SIGNAL` / `EXIT_SHORT_OPPOSITE_SIGNAL`: exit because a fresh opposite signal appeared.
- `STOP_FOR_DAY_LOSS_LIMIT` / `STOP_FOR_DAY_PROFIT_TARGET`: block new entries; continue necessary risk management for an open position.
- `WAIT`: no complete current setup.

## Output Contract

Every JSON result carries version and provenance fields so a third party can re-run and verify the same answer:

- `schema_version`, `strategy_version`, `generated_at`
- `instrument` (symbol, market, profile), `timeframe`, `bar_status`
- `data` (rows, start, end, source, `input_sha256`)
- `strategic_authorization`, `position_scope`, `execution_blockers`, `location`
- `selected_setup`, `key_levels`, `technical_context`, `warnings`

`technical_context` is descriptive reporting only — 5/20/60-day change, RSI14, MACD state, MA5/10/20/60 with MA20 slope, nearest confirmed support/resistance and distance, volume ratio. It must never be used to invent a signal the rule engine did not produce.

Read `references/api_reference.md` for exact formulas, confirmation rules, operational assumptions, output fields, and limitations. Do not attach a high-win-rate claim to the MACD failure pattern without independent out-of-sample evidence.

## Honest Reporting Rules

- A backtest with zero trades is a finding, not a failure. Report the sample size, trade count, and the fact that low-frequency rules cannot support a win-rate claim.
- Never present `NO_SETUP` as bullish or bearish. It means the rule set is silent.
- Never quote an unadjusted price series as if it were total-return; corporate actions create gaps.
- When data comes from a public endpoint rather than a licensed feed, say so in the output.

## Response Requirements

Include the market, timeframe, data end time, `bar_status`, authorization state, and whether higher-timeframe targets were available. Explain that MACD and RSI are correlated price-derived indicators. Never equate signal count with justified leverage. For any concrete buy, sell, target, stop, or position-size output, append this disclaimer verbatim:

> **免责声明**：以上内容基于公开数据和量化分析，仅供参考，不构成投资建议。市场有风险，投资需谨慎。任何投资决策应结合个人风险承受能力、资金状况和投资目标独立判断，必要时咨询持牌专业机构。过往表现不预示未来收益。
