# Changelog

本项目的所有重要变更都记录在此文件。

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号采用 `0.0.x` 递增，`SCHEMA_VERSION` 独立编号。

## [0.2.0] - 2026-11-17

把引擎从「能算信号」推到「能拒绝不合法的信号」。起因是把它接到 A 股研报上时发现：一个 `TRIGGERED` 结构在 T+1、不可裸空、核心仓这几条现实约束面前，经常根本不该变成买点，而旧版本照样输出 `BUY_LONG`。

### Added

- **`MARKET_PROFILES`**：`cn_a_stock` / `cn_a_etf` / `cn_a_index` / `generic` 四套制度假设（可做空与否、交收、一手股数），配套 `price_limit_pct()` 按代码推断涨跌停（主板 10% / 创业板·科创板 20% / 北交所 30%）。
- **`execution_blockers` 五道闸门**：`BAR_NOT_CLOSED` → `SHORT_NOT_ALLOWED` → `LOCATION_NOT_CONFIRMED` → `CORE_SCOPE_DOES_NOT_USE_TACTICAL_ENTRY` → `STRATEGIC_AUTHORIZATION_MISSING`。任一失败即改写动作，结构本身不改。出场与目标动作不受限制。
- **`--bar-status closed|forming`**：未收盘日线不得触发。这条来自一次真实错误——午间版把半天 K 线当收盘 K 线用。
- **位置过滤**：`--location-level` / `--location-tolerance-pct` / `--require-location`，把"离支撑够不够近"从肉眼判断变成强制闸门。
- **`--position-scope core|tactical`**：核心仓不使用战术止损，避免用 2 个 ATR 把长期持仓震出去。
- **`technical_context`**：5/20/60 日涨跌幅、RSI14、MACD 状态、MA5/10/20/60 与 MA20 斜率、最近确认支撑/阻力及距离、量比。**只用于叙述，不得据此发明引擎没算出的信号。**
- **`data.input_sha256`**：原始 CSV 字节哈希，供第三方确认"算的是同一条序列"。
- **`scripts/backtest.py`**：保守事件回测。次日开盘入场、止损优先于目标、T+1 最早次日退出、成本 bps、最大回撤、零交易如实报 0。
- **输出契约**：`schema_version` / `strategy_version` / `generated_at` / `instrument` / `timeframe` / `position_scope` / `strategic_authorization`。
- 新增 8 项测试（12 → 20），覆盖 A 股禁空、未收盘拦截、位置过滤、核心仓免止损，以及回测的四个保守性假设。

### Fixed

- **`recent_levels` 不过滤价格方向**：会把已被跌破的摆动低点继续报成支撑。实测中际旭创现价 408.97 时，"支撑"列表里出现 858.0 —— 那是头顶的套牢盘。改为按最后收盘价分侧，并输出 `basis: classified_against_last_close`。
- **`add_indicators` 拷贝时把字符串拆成字符列表**：`input_sha256` 被序列化成字符数组。改为只对 list 做拷贝。
- **`technical_context` 误用 EMA 当 MA**：改为简单移动平均窗口。
- **`SKILL.md` 文档动作名与实现不一致**（`WAIT_NOT_AUTHORIZED` 等在代码里不存在）：文档按实现改正。
- **`backtest.py` 缺 `import csv`**：异常分支会二次抛错。
- **零交易时文本输出 `胜率: None%`**：把"没有定义"印成了"None"，容易被读成计算出错。改为 `—`，并补一行说明"零交易不等于规则失效，但也不支持任何胜率结论"。

### Changed

- `description` 与 `SKILL.md` 重写为四层职责模型：战略层 / 授权层 / 战术层 / 执行层。明确"技术触发不是执行许可"。

### Known limitations

- 回测样本内不做参数寻优，也**不提供任何胜率结论**：9 只标的 3,195 根日线只开出 5 笔，净 −2.38R。这不是 bug，是规则原样实现的结果。
- 不复权，除权缺口会污染结构判定。
- 不做组合级相关性检查与事件过滤。
- `CONFLICT_WAIT` 无自动出口，需人工判定。

## [0.1.0] - 2026-11-17

首版。把 MACD(12,26,9) 常规/隐藏背离、Wilder RSI(14)、成交量确认、斐波那契 23.6% 触发、38.2% 与自定义 2/3 目标、风险预算仓位与单日风控固化为纯标准库脚本。只使用右侧确认的摆动点以避免未来函数；不联网、不下单。含 12 项单元测试。
