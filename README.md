# divergence-trade-signals

> 把「MACD / RSI / 成交量背离 + 斐波那契」这套短线规则，做成**可复现、可复核、会拒绝下单**的信号引擎。

一个 WorkBuddy / Claude 类 AI 助手的 Skill 包。版本 **v0.2.0**（schema 1.0）。纯 Python 标准库，无第三方依赖。

---

## 它解决什么问题

多数「背离战法」的问题不是讲得不好，而是**没法复核**：肉眼估 MACD、主观定摆动点、事后画斐波那契。只要有一点含糊，回测就不可能诚实。

这个引擎换了个做法：**所有判定交给代码，所有输出可回算。**

它回答的不是"该不该买"，而是——

> **这个价位、这个 K 线状态、这个市场制度下，规则允不允许你动手？**

## 它明确不做什么

| 不做 | 原因 |
|---|---|
| 自动下单 | 只输出规则判定，执行是人的事 |
| 编造行情 | 不联网取数，缺数据就报错，不用记忆补 |
| 承诺胜率 | 见下方「诚实结果」，这套规则是极低频过滤器 |
| 用技术面改战略 | 战略授权是**输入**，引擎只负责遵守或拒绝 |

## 三条设计原则

**1. 只用右侧确认的摆动点。** `pivot_right = 3`，第 N 根的高低点必须等 N+3 根出来才算确认。这会让信号变少、变慢，但换来一件事：**回测不会偷看未来**。

**2. 已触发的不追。** 触发价穿越只认"上一根在这一侧、这一根在另一侧"。价格早就跑过去的，一律标 `ALREADY_TRIGGERED_DO_NOT_CHASE`。这条规则砍掉了大部分诱人的图形。

**3. 技术触发 ≠ 执行许可。** 一个 `TRIGGERED` 结构，仍要通过五道闸门才可能变成买点：

| 顺序 | 条件 | 降级为 | blocker |
|---|---|---|---|
| 1 | 未收盘 K 线（`forming`） | `WAIT_BAR_NOT_FINAL` | `BAR_NOT_CLOSED` |
| 2 | A 股做空 | `WAIT_NO_SHORT_PERMISSION` | `SHORT_NOT_ALLOWED` |
| 3 | 未提供位置且要求位置 | `WAIT_LOCATION_UNCONFIRMED` | `LOCATION_NOT_CONFIRMED` |
| 4 | 核心仓范围 | `WAIT_CORE_SCOPE_NOT_TACTICAL` | `CORE_SCOPE_DOES_NOT_USE_TACTICAL_ENTRY` |
| 5 | 无战略授权 | `NO_AUTH` | `STRATEGIC_AUTHORIZATION_MISSING` |

出场与目标动作**不受**这五道闸门限制——不让一个仓位出来，比原来的问题更糟。

## 安装

```bash
# WorkBuddy / Claude 用户级技能目录
git clone https://github.com/zhshy/divergence-trade-signals.git \
  ~/.workbuddy/skills/divergence-trade-signals
```

或者直接下载 ZIP 解压到 `~/.workbuddy/skills/`。

## 快速开始

```bash
# 文本摘要
python3 scripts/divergence_signals.py /path/to/ohlcv.csv --format text

# A 股个股、已收盘、无授权、带位置过滤（最贴近实盘的组合）
python3 scripts/divergence_signals.py /path/to/sz300750.csv \
  --market cn_a_stock --symbol sz300750 \
  --timeframe day --bar-status closed \
  --position-scope tactical \
  --no-strategic-authorization \
  --location-level 238.0 --location-tolerance-pct 1.5 --require-location \
  --tick-size 0.01 --format json
```

CSV 只需要 `open,high,low,close,volume` 五列，`timestamp` 可选，至少 60 行。格式见 [`assets/example_asset.txt`](assets/example_asset.txt)。

## 市场 Profile

```bash
--market cn_a_stock   # 默认
```

| Profile | 可做空 | 交收 | 一手 | 涨跌停 |
|---|---|---|---|---|
| `cn_a_stock` | 否 | T+1 | 100 | 主板 10% / 创业板·科创板 20% / 北交所 30% |
| `cn_a_etf` | 否 | T+1 | 100 | 10% |
| `cn_a_index` | 否 | — | — | 无（指数不可直接交易） |
| `generic` | 是 | T+0 | 1 | 无 |

`--allow-short` 只在你自己确认有融券能力时才能覆盖 profile。否则 A 股的空头背离只是**信息**——它告诉你"别加仓"，不是"去做空"。

## 输出契约

每个 JSON 结果都带版本号与来源指纹，第三方可以逐字节复算：

```json
{
  "schema_version": "1.0",
  "strategy_version": "0.2.0",
  "action": "WAIT_DO_NOT_CHASE",
  "execution_blockers": [],
  "strategic_authorization": "NOT_AUTHORIZED",
  "position_scope": "tactical",
  "bar_status": "closed",
  "instrument": { "symbol": "sz300750", "market": "cn_a_stock" },
  "data": { "rows": 355, "source": "...", "input_sha256": "d66099c5…" },
  "selected_setup": { "state": "ALREADY_TRIGGERED_DO_NOT_CHASE", "direction": "short" },
  "key_levels": { "confirmed_support": [...], "confirmed_resistance": [...] },
  "technical_context": { "rsi14": 38.8, "moving_averages": { "trend": "down" } },
  "warnings": [...]
}
```

`input_sha256` 是原始 CSV 字节的 SHA256——这是「我们算的是同一条序列」的唯一凭据。

## 诚实结果：这套规则只开出 5 笔，净 −2.38R

用 9 只 A 股龙头 × 355 根日线（3,195 根）跑保守事件回测：次日开盘入场、止损优先于目标、T+1 最早次日退出、双边 8bp 成本、样本内不做参数优化。

| 指标 | 结果 |
|---|---:|
| 交易笔数 | **5** |
| 胜率 | 40%（2 胜 3 负） |
| 期望 | **−0.48R** |
| 累计 | **−2.38R** |

**为什么这么少——这是结构性原因，不是数据问题。** 摆动点需要右侧 3 根确认。等结构"可见"，价格在确认窗口里通常已经穿过 23.6% 触发价。这些 bar 实盘不可操作，引擎只能标 `ALREADY_TRIGGERED_DO_NOT_CHASE`（单只标的 295 个可判 bar 上出现 56 次）。要出现真正的 `TRIGGERED`，必须在 20 根结构有效期内**先回抽、再二次穿越**。

**所以：这不是"高胜率系统"，是"极低频过滤器"。** 5 笔样本连统计显著性都谈不上。任何"胜率 80%""稳定盈利"的说法，在本仓库的代码与数据面前都不可验证。

另一件不太好听的事：两条赢单只赚到 +0.07R 和 +0.63R，因为 38.2% 目标离入场太近、止损却给了整整一个锚点高点的距离——**盈亏比本身就不对称**。这是规则原样实现的必然结果，不是参数没调好。

```bash
# 自己验一遍
python3 scripts/backtest.py /path/to/ohlcv.csv \
  --market cn_a_stock --cost-bps 8 --format text
```

## 与研报方法论的关系

引擎处在四层结构的**第三层**，只负责那一层的问题：

```
战略层   deep-dive-investment-report   值不值得买、买多少
授权层   deep-dive-investment-report   这一笔批不批（五态机）
战术层   ← 本仓库                        现在能不能下手
执行层   券商 / 交易所                   制度上能不能下单
```

交接规则见 [deep-dive-investment-report](https://github.com/zhshy/deep-dive-investment-report) 的 `references/10-technical-overlay.md`。核心一条：**技术信号不能反向修改战略结论**。

## 测试

```bash
python3 scripts/test_divergence_signals.py
# Ran 20 tests ... OK
```

覆盖：摆动点右侧确认、Wilder RSI、常规/隐藏背离触发、风险预算仓位封顶、单笔超额拒单、止损与保本、日亏/日盈熔断、CSV 校验与排序、A 股禁空、未收盘拦截、位置过滤、核心仓不适用技术止损，以及回测的四个保守性假设（次日开盘、T+1、止损优先、零交易不虚构胜率）。

## 命令一览

```bash
# 分析
python3 scripts/divergence_signals.py CSV [--market ...] [--bar-status closed] [--format json|text]

# 回测
python3 scripts/backtest.py CSV --market cn_a_stock --cost-bps 8 [--format json|text]

# 测试
python3 scripts/test_divergence_signals.py
```

完整参数、公式与确认规则见 [`references/api_reference.md`](references/api_reference.md)。给 AI 助手看的操作说明见 [`SKILL.md`](SKILL.md)。

## 已知边界

- 不联网取数，不判断数据质量，不复权处理（除权缺口会污染结构判定）
- 不模拟滑点与盘口深度，回测成本只是 bps 常数
- 不做组合级相关性检查，不做新闻/事件过滤
- 五态机中的 `CONFLICT_WAIT`（技术面与基本面矛盾时上报给谁）需要人工判定，没有自动出口
- 没有跨周期自动对齐，高周期目标需显式传 `--htf-file`

## 免责声明

本项目是研究方法与工程实现的产物，**不构成任何投资建议**。所有输出均为规则化计算的结果，不含对未来价格的预测。市场有风险，投资需谨慎；任何决策应结合个人风险承受能力、资金状况与投资目标独立判断，必要时咨询持牌专业机构。过往表现不预示未来收益。

## License

MIT
