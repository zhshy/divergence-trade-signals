#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把自选股日报 payload 渲染成单文件 HTML（内联样式，无外部依赖）。"""

import html

SESSION_LABEL = {"midday": "午间", "close": "收盘"}

CSS = """
* { box-sizing: border-box; }
body { margin: 0; background: #f5f6f8; color: #1f2937;
       font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
       line-height: 1.65; }
.wrap { max-width: 1040px; margin: 0 auto; padding: 0 20px; }
header { background: #0f172a; color: #f8fafc; padding: 34px 0 26px; }
header h1 { font-size: 23px; font-weight: 600; margin: 0 0 8px; }
header .meta { color: #94a3b8; font-size: 13px; }
header .meta b { color: #e2e8f0; font-weight: 500; }
section { background: #fff; border: 1px solid #e5e7eb; border-radius: 12px;
          padding: 20px 22px; margin: 16px 0; }
h2 { font-size: 15px; font-weight: 600; margin: 0 0 12px; color: #0f172a; }
.headline { font-size: 16px; font-weight: 600; color: #b91c1c; margin: 0 0 14px; }
.headline.neutral { color: #1e3a8a; }
.cards { display: flex; flex-wrap: wrap; gap: 10px; }
.card { flex: 1 1 130px; background: #f8fafc; border: 1px solid #e5e7eb;
        border-radius: 10px; padding: 10px 14px; }
.card .k { font-size: 12px; color: #64748b; }
.card .v { font-size: 18px; font-weight: 600; font-variant-numeric: tabular-nums; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th, td { padding: 8px 6px; text-align: right; border-bottom: 1px solid #eef2f7;
         font-variant-numeric: tabular-nums; white-space: nowrap; }
th { color: #64748b; font-weight: 500; background: #fbfcfe; }
th:nth-child(-n+2), td:nth-child(-n+2) { text-align: left; }
th:nth-last-child(-n+2), td:nth-last-child(-n+2) { text-align: right; }
.scroll { overflow-x: auto; }
.tag { font-size: 11px; padding: 1px 8px; border-radius: 20px; background: #eef2ff; color: #3730a3; }
.tag.wait { background: #f1f5f9; color: #475569; }
.tag.expired { background: #f8fafc; color: #94a3b8; }
.tag.none { background: #f1f5f9; color: #94a3b8; }
.tag.hot { background: #fef2f2; color: #b91c1c; }
.pos { color: #b91c1c; }
.neg { color: #15803d; }
.notes { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 12px; }
.note { border: 1px solid #e5e7eb; border-radius: 10px; padding: 12px 14px; }
.note h3 { font-size: 14px; margin: 0 0 6px; }
.note ul { margin: 0; padding-left: 16px; font-size: 12.5px; color: #334155; }
.note li { margin: 3px 0; }
ol.rules { margin: 0; padding-left: 20px; font-size: 13px; color: #334155; }
ul.limits { margin: 0; padding-left: 18px; font-size: 12.5px; color: #64748b; }
footer { color: #64748b; font-size: 12px; padding: 6px 0 40px; }
a { color: #3730a3; text-decoration: none; }
@media (max-width: 640px) { header h1 { font-size: 19px; } section { padding: 16px; } }
"""


def esc(value):
    return html.escape("" if value is None else str(value))


def num(value, digits=2):
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def cls_of(record):
    state = record.get("setup_state")
    if state == "TRIGGERED":
        return "hot"
    if state == "WAITING":
        return "wait"
    if state == "ALREADY_TRIGGERED_DO_NOT_CHASE":
        return "expired"
    return "none"


def pct_class(value):
    if value is None:
        return ""
    return "pos" if value > 0 else ("neg" if value < 0 else "")


def render(payload):
    summary = payload["summary"]
    session_zh = SESSION_LABEL[payload["session"]]
    bar_note = ("当日 K 线未收盘，收盘价仍会变化，仅作盘中预演"
                if payload["session"] == "midday" else "当日 K 线已收盘")
    headline_cls = "headline" if summary["triggered"] else "headline neutral"

    cards = [
        ("参与计算", f"{summary['total']} 只"),
        ("已触发", f"{summary['triggered']} 只"),
        ("等待触发", f"{summary['waiting']} 只"),
        ("过期不追", f"{summary['expired']} 只"),
        ("无结构", f"{summary['no_setup']} 只"),
        ("战略授权", "已授权" if summary["authorized"] else "NOT_AUTHORIZED"),
    ]
    card_html = "".join(
        f'<div class="card"><div class="k">{esc(k)}</div><div class="v">{esc(v)}</div></div>'
        for k, v in cards
    )

    rows = []
    for record in payload["items"]:
        target1 = (record["targets"] or [None])[0]
        five_day = (record["returns_pct"] or {}).get("5d")
        rows.append(
            "<tr>"
            f"<td>{esc(record['code'])}</td>"
            f"<td>{esc(record['name'])}</td>"
            f"<td>{num(record['last_close'])}</td>"
            f'<td class="{pct_class(five_day)}">'
            f"{'—' if five_day is None else f'{five_day:+.2f}%'}</td>"
            f"<td>{num(record['rsi14'], 1)}</td>"
            f"<td>{num(record['volume_ratio'])}</td>"
            f"<td>{esc(record['action'])}</td>"
            f'<td><span class="tag {cls_of(record)}">{esc(record["state_label"])}</span></td>'
            f"<td>{num(record['entry_trigger'])}</td>"
            f"<td>{num(record['initial_stop'])}</td>"
            f"<td>{num(target1)}</td>"
            "</tr>"
        )

    notes = []
    for record in payload["items"]:
        blockers = record["execution_blockers"]
        gate = ("拦截：" + "、".join(blockers)) if blockers else "无拦截项"
        notes.append(
            '<div class="note">'
            f"<h3>{esc(record['name'])} <span style=\"color:#94a3b8;font-weight:400\">"
            f"{esc(record['code'])}</span> "
            f'<span class="tag {cls_of(record)}">{esc(record["state_label"])}</span></h3>'
            "<ul>"
            f"<li>{esc(record['action'])} ｜ 形态 {esc(record['setup_type'] or '无形态')} ｜ "
            f"方向 {esc(record['setup_direction'] or '—')}</li>"
            f"<li>触发 {num(record['entry_trigger'])} ｜ 失效 {num(record['initial_stop'])} ｜ "
            f"目标 {', '.join(num(t) for t in record['targets']) or '—'}</li>"
            f"<li>RSI14 {num(record['rsi14'], 1)} ｜ 量能比 {num(record['volume_ratio'])} ｜ "
            f"支撑 {num(record['support'])} ｜ 压力 {num(record['resistance'])}</li>"
            f"<li>{esc(gate)}</li>"
            + (f"<li>{esc(record['note'])}</li>" if record.get("note") else "")
            + "</ul></div>"
        )

    context_html = ""
    if payload["context_items"]:
        items = "".join(
            f"<li><b>{esc(item['name'])} {esc(item['code'])}</b>：{esc(item.get('note') or '无行情源')}</li>"
            for item in payload["context_items"]
        )
        context_html = f'<section><h2>仅作背景（不参与信号）</h2><ul class="limits">{items}</ul></section>'

    limits = "".join(f"<li>{esc(text)}</li>" for text in payload["limitations"])

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>自选股背离信号 · {esc(payload['as_of'])} {session_zh}</title>
<style>{CSS}</style>
</head>
<body>
<header>
  <div class="wrap">
    <h1>自选股背离信号 · {esc(payload['as_of'])} {session_zh}</h1>
    <div class="meta">
      期次 <b>{esc(payload['session'])}</b> ｜ 生成 <b>{esc(payload['generated_at'])}</b> ｜
      引擎 <b>v{esc(payload['engine_version'])}</b> ｜ {esc(bar_note)}<br>
      数据：腾讯日线（不复权）｜ 名单 <b>config/watchlist.json</b>
      （{summary['total']} 只参与计算，{len(payload['context_items'])} 只仅作背景）
    </div>
  </div>
</header>

<div class="wrap">
  <section>
    <div class="{headline_cls}">{esc(summary['headline'])}</div>
    <div class="cards">{card_html}</div>
  </section>

  <section>
    <h2>明细</h2>
    <div class="scroll">
      <table>
        <thead><tr>
          <th>代码</th><th>名称</th><th>最新</th><th>5日</th><th>RSI14</th><th>量能 5/20</th>
          <th>规则动作</th><th>状态</th><th>触发</th><th>止损</th><th>目标1</th>
        </tr></thead>
        <tbody>{''.join(rows)}</tbody>
      </table>
    </div>
  </section>

  <section>
    <h2>逐只要点</h2>
    <div class="notes">{''.join(notes)}</div>
  </section>

  {context_html}

  <section>
    <h2>执行闸门</h2>
    <p style="font-size:13px;color:#334155;margin:0 0 10px">
      技术触发不等于执行许可，以下任一条不满足，规则层都会拒绝下单：
    </p>
    <ol class="rules">
      <li>K 线必须已收盘（closed），盘中 forming 只能预演；</li>
      <li>A 股不能裸卖空，做空信号只在有券可借时有意义；</li>
      <li>位置未确认（无战略价位）时，突破信号降级为等待；</li>
      <li>核心仓不使用战术入场；</li>
      <li>无战略授权时，任何触发都只输出 NO_AUTH。</li>
    </ol>
    <p style="font-size:13px;color:#b91c1c;margin:12px 0 0">
      本报告默认不授予战略授权：它是观察清单，不是下单指令。
    </p>
  </section>

  <section>
    <h2>数据与限制</h2>
    <ul class="limits">{limits}</ul>
  </section>

  <footer>
    由 <a href="https://github.com/zhshy/divergence-trade-signals">divergence-trade-signals</a>
    生成 · 不构成任何投资建议
  </footer>
</div>
</body>
</html>
"""
