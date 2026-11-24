"""Self-contained HTML analytical report (opens in any browser, prints to PDF).
Charts are interactive Plotly figures loaded from the Plotly CDN; every chart
is accompanied by its data table so the report is still useful offline."""
from __future__ import annotations

import html
import json
from datetime import datetime, timezone

PLOTLY_CDN = "https://cdn.plot.ly/plotly-2.35.2.min.js"

CSS = """
:root{--ink:#0F172A;--muted:#64748B;--line:#E2E8F0;--brand:#4F6BED;--warn:#B45309;--ok:#047857;--bg:#F8FAFC}
*{box-sizing:border-box}body{font:15px/1.6 'Manrope',system-ui,sans-serif;color:var(--ink);background:var(--bg);margin:0}
main{max-width:920px;margin:0 auto;padding:40px 24px 80px}
h1{font-size:30px;margin:0 0 4px;letter-spacing:-.02em}h2{font-size:20px;margin:40px 0 12px;padding-top:8px;border-top:2px solid var(--ink)}
h3{font-size:15px;margin:24px 0 8px}.muted{color:var(--muted)}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:20px 0}
.kpi{background:#fff;border:1px solid var(--line);border-radius:10px;padding:14px}.kpi b{display:block;font-size:22px}
.finding{background:#fff;border:1px solid var(--line);border-left:4px solid var(--brand);border-radius:8px;padding:10px 14px;margin:8px 0}
.finding.warning{border-left-color:#F59E0B}.finding.positive{border-left-color:#10B981}.finding b{display:block}
.chart{background:#fff;border:1px solid var(--line);border-radius:10px;padding:8px;margin:12px 0}
table{border-collapse:collapse;width:100%;font-size:13px;background:#fff;margin:8px 0}
th,td{border:1px solid var(--line);padding:6px 9px;text-align:left}th{background:#F1F5F9}
pre{background:#0F172A;color:#E2E8F0;padding:12px;border-radius:8px;overflow:auto;font-size:12px;white-space:pre-wrap}
.qa{background:#fff;border:1px solid var(--line);border-radius:10px;padding:16px;margin:16px 0}
.q{font-weight:700}button{font:inherit;background:var(--ink);color:#fff;border:0;border-radius:8px;padding:8px 14px;cursor:pointer}
@media print{button{display:none}body{background:#fff}.chart,.qa,.kpi,.finding{break-inside:avoid}}
"""


def _e(x) -> str:
    return html.escape(str(x))


def _table(columns, rows, limit=15) -> str:
    head = "".join(f"<th>{_e(c)}</th>" for c in columns)
    body = "".join("<tr>" + "".join(f"<td>{_e('' if v is None else v)}</td>" for v in r) + "</tr>" for r in rows[:limit])
    more = f'<p class="muted">Showing {min(limit, len(rows))} of {len(rows)} rows.</p>' if len(rows) > limit else ""
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>{more}"


def _chart(div_id: str, figure: dict) -> tuple[str, str]:
    div = f'<div class="chart"><div id="{div_id}"></div></div>'
    fig = dict(figure)
    layout = dict(fig.get("layout", {}))
    layout.pop("template", None)
    script = (f"Plotly.newPlot({json.dumps(div_id)},{json.dumps(fig.get('data', []))},"
              f"{json.dumps(layout)},{{responsive:true,displaylogo:false}});")
    return div, script


def build_report(dataset, insights: dict, conversation=None) -> str:
    p = dataset.profile
    scripts: list[str] = []
    parts: list[str] = []
    now = datetime.now(timezone.utc).strftime("%d %b %Y, %H:%M UTC")

    parts.append(f"<h1>{_e(dataset.name)}</h1><p class='muted'>DataSense AI analytical report · generated {now} · "
                 f"{p['n_rows']:,} rows × {p['n_cols']} columns · source file {_e(dataset.filename)}</p>")
    parts.append('<button onclick="window.print()">Print / Save as PDF</button>')
    parts.append('<div class="kpis">' + "".join(
        f'<div class="kpi"><span class="muted">{_e(k["label"])}</span><b>{_e(k["value"])}</b></div>' for k in insights["kpis"]) + "</div>")

    if insights.get("ai_summary"):
        summary = _e(insights["ai_summary"]).replace("\n", "<br>")
        parts.append(f"<h2>Executive summary</h2><div class='finding'>{summary}</div>")

    parts.append("<h2>Key findings</h2>")
    for f in insights["findings"]:
        parts.append(f'<div class="finding {_e(f["severity"])}"><b>{_e(f["title"])}</b>{_e(f["detail"])}</div>')

    an = insights["anomalies"]
    parts.append(f"<h2>Anomalies ({an['total']})</h2>")
    if an["total"] == 0:
        parts.append("<p class='muted'>No significant anomalies were detected.</p>")
    if an["time_series"]:
        parts.append("<h3>Unusual periods (time series)</h3>" + _table(
            ["Period", "Value", "Expected", "Type", "Robust z-score"],
            [[a["period"], a["value"], a["expected"], a["direction"], a["z_score"]] for a in an["time_series"]]))
    if an["outliers"]:
        parts.append("<h3>Extreme values (per column)</h3>" + _table(
            ["Row", "Column", "Value", "Typical (median)", "Robust z-score"],
            [[o["row"], o["column"], o["value"], o["typical"], o["z_score"]] for o in an["outliers"]]))
    if an["multivariate"]:
        cols = list(an["multivariate"][0]["values"].keys())
        parts.append("<h3>Unusual rows (Isolation Forest)</h3>" + _table(
            ["Row", "Score"] + cols, [[m["row"], m["score"]] + [m["values"].get(c) for c in cols] for m in an["multivariate"]]))

    parts.append("<h2>Charts</h2>")
    for i, ch in enumerate(insights["charts"]):
        div, js = _chart(f"dash{i}", ch["figure"])
        parts.append(f"<h3>{_e(ch['title'])}</h3>{div}")
        scripts.append(js)

    parts.append("<h2>Data dictionary</h2>" + _table(
        ["Column", "Type", "Kind", "Missing %", "Unique", "Examples"],
        [[c["name"], c["sql_type"], c["kind"], c["null_pct"], c["unique"], ", ".join(map(str, c["samples"]))] for c in p["columns"]], limit=400))

    if conversation is not None:
        parts.append(f"<h2>Analyst Q&amp;A: {_e(conversation.title)}</h2>")
        for i, m in enumerate(conversation.messages):
            if m.role == "user":
                parts.append(f"<div class='qa'><div class='q'>Q: {_e(m.content)}</div>")
                continue
            pl = m.payload or {}
            parts.append(f"<p>{_e(m.content).replace(chr(10), '<br>')}</p>")
            if pl.get("chart"):
                div, js = _chart(f"qa{i}", pl["chart"])
                parts.append(div)
                scripts.append(js)
            if pl.get("table"):
                parts.append(_table(pl["table"]["columns"], pl["table"]["rows"]))
            if pl.get("sql"):
                parts.append(f"<details><summary class='muted'>SQL used</summary><pre>{_e(pl['sql'])}</pre></details>")
            parts.append("</div>")

    parts.append("<p class='muted'>Figures are computed from the uploaded data with SQL and Pandas. "
                 "AI-written text is generated from those computed results and should be reviewed before decisions are made.</p>")
    return (f"<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{_e(dataset.name)} · DataSense AI report</title><style>{CSS}</style>"
            f"<script src='{PLOTLY_CDN}'></script></head><body><main>{''.join(parts)}</main>"
            f"<script>window.addEventListener('load',function(){{if(!window.Plotly)return;{''.join(scripts)}}});</script></body></html>")
