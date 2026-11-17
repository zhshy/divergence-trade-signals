# Divergence Signal Rules and CLI Reference

## Deterministic conventions

- Calculate MACD as EMA(12) minus EMA(26), with DEA equal to EMA(DIF, 9). Use DIF for divergence and DIF minus DEA for the near-cross failure pattern.
- Calculate RSI(14) with Wilder's simple-average seed and recursive smoothing.
- Confirm a swing high only when its high is strictly greater than the configurable left and right bars. Confirm a swing low symmetrically. Default to three bars on each side.
- Use only the latest two confirmed same-type pivots. Discard a setup when the second pivot is more than 20 bars old by default.
- Compare price, DIF, RSI, and volume at the same pivot bars. Do not pair a price extreme with an indicator value from a different bar.
- Treat all signals as latest-bar evaluations. The script does not provide a historical backtest or performance claim.

## Divergence rules

Regular bullish divergence: the second price low is lower, while its DIF value is higher.

Regular bearish divergence: the second price high is higher, while its DIF value is lower.

Hidden bullish divergence: the second price low is higher, while its DIF value is lower. Require close above EMA(50) and a rising EMA(50) over the latest five bars. This trend definition is an explicit operational assumption because the source rule did not define trend quantitatively.

Hidden bearish divergence is implemented symmetrically as an extension: lower price high, higher DIF, close below EMA(50), and falling EMA(50).

RSI confirmation uses RSI at the two price pivots in the same direction as the price-versus-DIF divergence. Volume confirmation requires volume at the second pivot to be lower. These confirmations increase the raw rule score but do not prove independent evidence or future profitability.

## Fibonacci entry and invalidation

For regular bearish divergence, let `H` be the second high or a later new high and `L` be the lowest low strictly between the two divergence highs:

- Entry trigger = `H - 0.236 × (H - L)`.
- Initial stop = `H + one tick`.
- Re-anchor `H` when a later bar makes a new high.
- Trigger only when the latest close crosses below the level after the previous close was at or above it.

For regular bullish divergence, let `L` be the second low or a later new low and `H` be the highest high strictly between the divergence lows:

- Entry trigger = `L + 0.236 × (H - L)`.
- Initial stop = `L - one tick`.
- Re-anchor `L` when a later bar makes a new low.
- Trigger only when the latest close crosses above the level after the previous close was at or below it.

For hidden divergence, do not re-anchor after a new extreme; invalidate the setup. Use the high between the two lows for hidden bullish calculations and the low between the two highs for hidden bearish calculations.

An `ALREADY_TRIGGERED_DO_NOT_CHASE` state means the close is beyond the level but no fresh cross occurred on the latest bar. Do not convert it into a market-entry instruction.

## MACD near-cross failure

Detect only a bullish version. Require:

1. Two confirmed lows with a lower second price low.
2. DIF minus DEA to remain nonnegative from the second low onward.
3. The gap to contract below half its initial value, demonstrating an approach without a bearish cross.
4. DIF to turn upward on the latest bar after a non-rising previous bar.
5. The latest close to rise from the previous close.

Use the latest close as the reference entry and the second low minus one tick as invalidation. This is a deliberately strict proxy for the discretionary description, not a universally standard or proven high-win-rate pattern.

## Targets and key levels

With `--htf-file`, use the latest confirmed higher-timeframe completed swing:

- Short targets derive from a prior low-to-high upswing: `H - 0.382 × (H-L)` and `H - 2/3 × (H-L)`.
- Long targets derive from a prior high-to-low downswing: `L + 0.382 × (H-L)` and `L + 2/3 × (H-L)`.

## Target weighting and the two-thirds level

The second target is a custom two-thirds level, not a standard Fibonacci ratio. Without higher-timeframe data, use the current timeframe only as a disclosed fallback. The engine lists the latest three confirmed support and resistance levels; `--location-level` plus `--require-location` turns that contextual check into an enforced gate instead of an eyeball judgement.

## Risk sizing and daily controls

The raw rule score is one contract plus one for RSI confirmation plus one for volume confirmation. This score is not a final position recommendation.

When account equity, risk percentage, and point value are supplied:

`risk budget = account equity × risk percent / 100`

`estimated risk per contract = absolute(entry-stop) × point value + fees + slippage ticks × tick size × point value`

`risk-capped contracts = min(raw rule score, floor(risk budget / estimated risk per contract))`

Block the setup when the budget cannot support one contract. Use the correct contract multiplier and currency conversion externally when necessary.

For existing positions, pass entry price and initial stop. The engine reports the initial stop exit, open-profit R multiple, and break-even eligibility. Default break-even eligibility is +1R; the offset is expressed in price points, not ticks. Never widen a losing position's stop.

Optional daily PnL, loss-limit, and profit-target parameters block new entries after either limit. Existing-position stops and target exits retain priority.

## Input and output

CSV columns are case-insensitive. Timestamp data is sorted and exact duplicate timestamp strings are deduplicated with the last row retained. OHLC values must be positive and internally consistent; volume must be nonnegative. At least 60 rows are required. The raw file bytes are hashed into `data.input_sha256`, so a third party can confirm they scored the identical series.

JSON output includes `action`, `execution_blockers`, `selected_setup`, `all_setups`, `position_management`, `daily_guard`, `key_levels`, `technical_context`, `instrument`, `market_profile`, `strategic_authorization`, `position_scope`, `bar_status`, `timeframe`, `location`, `data`, `parameters`, and `warnings`. Inspect `all_setups` when the selected action does not match another still-waiting setup.

## Execution permission gate

Computed after the rule action is resolved, in this order. The first failing check rewrites the action and appends a blocker; the pattern itself is never altered.

| Order | Condition | Resulting action | Blocker code |
|---|---|---|---|
| 1 | Entry on `--bar-status forming` | `WAIT_BAR_NOT_FINAL` | `BAR_NOT_CLOSED` |
| 2 | `SELL_SHORT` where the profile forbids shorting | `WAIT_NO_SHORT_PERMISSION` | `SHORT_NOT_ALLOWED` |
| 3 | Entry with `--require-location` and an unconfirmed level | `WAIT_LOCATION_UNCONFIRMED` | `LOCATION_NOT_CONFIRMED` |
| 4 | Entry with `--position-scope core` | `WAIT_CORE_SCOPE_NOT_TACTICAL` | `CORE_SCOPE_DOES_NOT_USE_TACTICAL_ENTRY` |
| 5 | Entry with `--no-strategic-authorization` | `NO_AUTH` | `STRATEGIC_AUTHORIZATION_MISSING` |

Exit and target actions bypass this gate: refusing to let a position out because the market profile is inconvenient would be worse than the original problem.

`market_profile` echoes the resolved constraints — `allow_short`, `settlement`, `lot_size`, and `price_limit_pct` — so the reader can see which rule produced the block. Price limits by board: main board 10%, ChiNext and STAR 20%, BSE 30%, ETF 10%, index none.

## Backtest

`scripts/backtest.py` replays the same engine bar by bar and reports trade count, win rate, average R, expectancy, and max drawdown. Deliberate conservatism:

- Entry at the **next open** after a trigger, never at the trigger close.
- Stop evaluated before target when both are touched in one bar.
- T+1 enforced for `cn_a_stock` and `cn_a_etf`: the earliest exit is the following session.
- Costs applied in basis points on both legs.
- No parameter search on the sample. A zero-trade result is reported as zero trades, not smoothed into a claim.

## Known boundaries

No real-time feed, no spread-aware execution simulation, no order placement, no portfolio-level correlation check, no automatic news filter, and no statistical validation of edge. Unadjusted price series contain corporate-action gaps; use adjusted data for return claims. Use out-of-sample testing with actual fees and slippage before relying on any rule output.
