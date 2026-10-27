"""Deterministic HTML rendering: designers select presentation, never data values."""

from __future__ import annotations

import json
import re
from datetime import datetime
from html import escape
from html.parser import HTMLParser

from plotly.offline import get_plotlyjs

from .models import AnalysisResult, ReportPlan


def display(value):
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:,.4f}".rstrip("0").rstrip(".")
    if isinstance(value, int):
        return f"{value:,}"
    return str(value)


def _paragraph(text: str, result: AnalysisResult):
    metrics = {m.id: m for m in result.metrics}
    parts = []
    cursor = 0
    for match in re.finditer(r"\{\{metric:([A-Za-z][A-Za-z0-9_-]*)\}\}", text):
        parts.append(escape(text[cursor:match.start()]))
        metric = metrics.get(match.group(1))
        if metric is None:
            raise ValueError(f"未知指标引用：{match.group(1)}")
        parts.append(f'<a href="#e-{metric.id}">{escape(display(metric.value))}{escape(metric.unit)}</a>')
        cursor = match.end()
    parts.append(escape(text[cursor:]))
    return "".join(parts)


def render_html(result: AnalysisResult, plan: ReportPlan, provenance: str) -> str:
    charts = {c.id: c for c in result.charts}
    order = plan.chart_order or list(charts)
    if len(set(order)) != len(order) or set(order) != set(charts):
        raise ValueError("chart_order 必须包含全部图表 ID 且不重复。")
    tables = {t.id: t for t in result.tables}
    dark = plan.theme == "dark"
    ink, paper, panel, accent, muted, border = (
        ("#e9eef6", "#101827", "#192438", "#8bd5ca", "#a8b9d0", "#33445d") if dark else
        ("#182c43", "#f5f7fa", "#ffffff", "#087e8b", "#536980", "#dae2eb")
    )
    sections = []
    if plan.summary:
        sections.append('<section><h2>摘要</h2>' + "".join(f"<p>{_paragraph(p, result)}</p>" for p in plan.summary) + '</section>')
    sources = "".join(
        f'<tr><td>{escape(s.file)}</td><td>{escape(s.sheet or "CSV")}</td><td>{s.rows:,}</td><td>{len(s.columns)}</td></tr>'
        for s in result.sources
    )
    sections.append('<section><h2>数据概况</h2><div class="table-scroll"><table><thead><tr><th>文件</th><th>工作表</th><th>原始行数</th><th>字段数</th></tr></thead><tbody>' + sources + '</tbody></table></div></section>')
    if result.metrics:
        sections.append('<section><h2>核心指标</h2><div class="metrics">' + "".join(
            f'<article class="metric" id="e-{m.id}"><h3>{escape(m.label)}</h3><div class="value">{escape(display(m.value))}<small>{escape(m.unit)}</small></div><p>{escape(m.definition)}</p><span class="meta">来源：{escape(m.source)}</span></article>'
            for m in result.metrics
        ) + '</div></section>')
    if result.findings:
        sections.append('<section><h2>主要发现</h2><ul class="findings">' + "".join(
            '<li>' + escape(f.text) + '<div class="evidence">证据：' + " · ".join(f'<a href="#e-{ref}">{escape(ref)}</a>' for ref in f.evidence) + '</div></li>'
            for f in result.findings
        ) + '</ul></section>')
    chart_scripts = []
    for chart_id in order:
        chart = charts[chart_id]
        table = tables[chart.table_id]
        xi, yi = table.columns.index(chart.x), table.columns.index(chart.y)
        x = [row[xi] for row in table.rows]
        y = [row[yi] for row in table.rows]
        trace = {"type": "bar" if chart.kind == "bar" else "scatter", "x": x, "y": y, "marker": {"color": accent}}
        if chart.kind in {"line", "scatter"}:
            trace["mode"] = "lines+markers" if chart.kind == "line" else "markers"
        if chart.kind == "pie":
            trace = {"type": "pie", "labels": x, "values": y, "hole": 0.45}
        spec = {"data": [trace], "layout": {"font": {"color": ink, "family": "Microsoft YaHei, sans-serif"}, "paper_bgcolor": panel, "plot_bgcolor": panel, "margin": {"l": 60, "r": 25, "t": 25, "b": 70}, "xaxis": {"title": {"text": chart.x}, "automargin": True}, "yaxis": {"title": {"text": chart.y}, "automargin": True}, "autosize": True}}
        payload = json.dumps(spec, ensure_ascii=False, allow_nan=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
        chart_scripts.append(f'{{const s={payload};Plotly.newPlot("chart-{chart.id}",s.data,s.layout,{{responsive:true,displaylogo:false,scrollZoom:false}});}}')
        sections.append(f'<section id="e-{chart.id}"><h2>{escape(chart.title)}</h2><p>{escape(chart.description)}</p><div class="chart" id="chart-{chart.id}" role="img" aria-label="{escape(chart.title, quote=True)}"></div><p class="meta">图表数据：<a href="#e-{table.id}">{escape(table.title)}</a></p></section>')
    for table in result.tables:
        headings = "".join(f"<th>{escape(c)}</th>" for c in table.columns)
        rows = "".join("<tr>" + "".join(f"<td>{escape(display(v))}</td>" for v in row) + "</tr>" for row in table.rows[:200])
        note = '<p class="meta">页面展示前 200 行；完整聚合数据保存在分析 JSON 中。</p>' if len(table.rows) > 200 else ''
        sections.append(f'<section id="e-{table.id}"><h2>{escape(table.title)}</h2><p>{escape(table.description)}</p><div class="table-scroll" tabindex="0"><table><thead><tr>{headings}</tr></thead><tbody>{rows}</tbody></table></div>{note}</section>')
    for title, items in (("分析方法", result.methods), ("限制与注意事项", result.limitations)):
        if items:
            sections.append(f'<section><h2>{title}</h2><ul>' + "".join(f'<li>{escape(item)}</li>' for item in items) + '</ul></section>')
    script = '<script>' + get_plotlyjs() + '</script><script>' + "\n".join(chart_scripts) + '</script>' if chart_scripts else ''
    html = f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:; font-src data:; connect-src 'none'">
<title>{escape(plan.title)}</title><style>
:root{{--ink:{ink};--paper:{paper};--panel:{panel};--accent:{accent};--muted:{muted};--border:{border}}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--paper);color:var(--ink);font:16px/1.75 'Segoe UI','Microsoft YaHei',sans-serif}}
main{{max-width:1120px;margin:auto;padding:48px 32px}} header{{padding:24px 0 32px;border-bottom:3px solid var(--accent)}}
.eyebrow{{letter-spacing:.18em;color:var(--accent);font-size:12px;font-weight:700}} h1{{font-size:clamp(26px,4vw,44px);line-height:1.3;margin:14px 0;overflow-wrap:anywhere}}
h2{{font-size:22px;margin:0 0 18px}} h3{{font-size:15px;margin:0;color:var(--muted)}} p{{margin:12px 0}} section{{margin-top:28px;background:var(--panel);padding:28px;border:1px solid var(--border);border-radius:12px;min-width:0}}
.meta,.evidence{{color:var(--muted);font-size:12px;overflow-wrap:anywhere}} .metrics{{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:20px}}
.metric{{padding:18px;border-left:3px solid var(--accent);min-width:0}} .metric p{{font-size:13px;color:var(--muted)}} .value{{font-size:30px;font-weight:650;overflow-wrap:anywhere}} small{{font-size:14px;margin-left:6px}}
a{{color:var(--accent);text-underline-offset:4px}} a:focus-visible,.table-scroll:focus-visible{{outline:3px solid var(--accent);outline-offset:4px}}
.table-scroll{{overflow-x:auto;max-width:100%}} table{{border-collapse:collapse;width:100%;font-size:14px;text-align:left}} th,td{{border-bottom:1px solid var(--border);padding:12px;white-space:nowrap}} th{{color:var(--muted)}}
li{{margin:10px 0}} .findings li{{padding-bottom:12px}} .chart{{height:400px;width:100%;min-width:0}} footer{{padding:32px 0}} 
@media(max-width:600px){{main{{padding:24px 14px}}section{{padding:18px}}.chart{{height:330px}}.metrics{{grid-template-columns:1fr}}}}
@media print{{body{{--paper:#fff;--panel:#fff;--ink:#172a40;--muted:#526173;--border:#ddd;background:#fff;color:var(--ink)}}main{{max-width:none;padding:0}}section{{break-inside:avoid;border-radius:0}}.modebar{{display:none!important}}a{{color:inherit}}.table-scroll{{overflow:visible}}th,td{{white-space:normal}}}}
@media(prefers-reduced-motion:reduce){{*{{scroll-behavior:auto!important;animation:none!important;transition:none!important}}}}
</style></head><body><main><header><div class="eyebrow">DATA ANALYSIS REPORT</div><h1>{escape(plan.title)}</h1><p class="meta">生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')} · 分析记录：{escape(provenance)}</p></header>{''.join(sections)}<footer class="meta">由 Main Agent · DS Analyst · Report Designer 协作生成。指标和图表来自已执行的分析结果；解释与建议需结合业务背景。</footer></main>{script}</body></html>'''
    validate_html(html)
    return html


class _ReportParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids, self.links, self.external = set(), [], []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "id" in attrs:
            if attrs["id"] in self.ids:
                raise ValueError("重复 HTML 元素 ID")
            self.ids.add(attrs["id"])
        if attrs.get("href", "").startswith("#"):
            self.links.append(attrs["href"][1:])
        for key in ("src", "href"):
            value = attrs.get(key, "")
            if value and not value.startswith(("#", "data:", "blob:")):
                self.external.append(value)


def validate_html(html: str):
    parser = _ReportParser()
    parser.feed(html)
    if parser.external:
        raise ValueError("报告包含外部资源引用")
    if not set(parser.links) <= parser.ids:
        raise ValueError("报告存在失效证据链接")
