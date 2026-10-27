from __future__ import annotations

import hashlib
import json
import threading

from langchain_core.tools import tool

from .models import AnalysisResult, ReportPlan
from .report import render_html, validate_html
from .sandbox import Executor
from .session import Session, safe_file, write_json


def make_tools(session: Session, executor: Executor):
    profiles: dict[tuple[str, str | None], dict] = {}
    lock = threading.Lock()

    @tool
    def inspect_data(file: str, sheet: str | None = None) -> str:
        """Inspect one input file in Docker. Excel without sheet lists worksheets first; CSV returns statistics and five sample rows."""
        with lock:
            session.event("DS Analyst", "检查数据", file=file, sheet=sheet)
            try:
                result = executor.inspect(file, sheet)
                profiles[(file, sheet)] = result
                return json.dumps(result, ensure_ascii=False)
            except Exception as exc:
                return json.dumps({"error": str(exc)}, ensure_ascii=False)

    @tool
    def analysis_contract() -> str:
        """Get the required result.json JSON schema and available input names. Call before writing analysis code."""
        return json.dumps({"inputs": session.manifest, "schema": AnalysisResult.model_json_schema()}, ensure_ascii=False)

    @tool
    def run_analysis(code: str) -> str:
        """Execute Python in Docker and validate its result.json. Maximum three attempts per user turn, including validation failures. Use /inputs/<file> or worker.read_frame; write result.json in cwd."""
        with lock:
            session.event("DS Analyst", "执行分析", attempt=session.attempts + 1)
            try:
                name, evidence, raw = executor.run(code)
                result = AnalysisResult.model_validate_json(raw)
                for source in result.sources:
                    profile = profiles.get((source.file, source.sheet))
                    if not profile or "rows" not in profile:
                        raise ValueError(f"必须先用 inspect_data 检查来源 {source.file} / {source.sheet}")
                    if source.rows != profile["rows"] or source.columns != [c["name"] for c in profile["columns"]]:
                        raise ValueError("sources 必须描述原始数据的行数和字段，不能填清洗后的尺寸。")
                write_json(evidence / "result.json", result.model_dump())
                write_json(evidence / "provenance.json", {"turn": session.turn, "script_sha256": hashlib.sha256(code.encode()).hexdigest(), "inputs": session.manifest})
                session.latest_analysis = f"analyses/{name}/result.json"
                session.blocked = False
                session.event("DS Analyst", "分析已验证", path=session.latest_analysis)
                return json.dumps({"status": "validated", "analysis": session.latest_analysis, "title": result.title, "metrics": [m.model_dump() for m in result.metrics], "findings": [f.model_dump() for f in result.findings]}, ensure_ascii=False)
            except Exception as exc:
                session.blocked = True
                session.event("DS Analyst", "分析失败", error=str(exc))
                return json.dumps({"error": str(exc), "attempts_remaining": max(0, 3 - session.attempts), "report_allowed": False}, ensure_ascii=False)

    @tool
    def read_analysis() -> str:
        """Read the latest validated analysis for report design; tables are previewed, full chart/table data remain in the renderer."""
        if session.blocked or not session.latest_analysis:
            return json.dumps({"error": "当前没有可交付的分析；请先成功运行 DS Analyst。"}, ensure_ascii=False)
        result = AnalysisResult.model_validate_json(safe_file(session.root, session.latest_analysis).read_text(encoding="utf-8"))
        payload = result.model_dump()
        for table in payload["tables"]:
            table["total_rows"] = len(table["rows"])
            table["rows"] = table["rows"][:10]
        previous = None
        if session.reports:
            previous = json.loads((session.root / session.reports[-1]).with_suffix(".json").read_text(encoding="utf-8"))
        return json.dumps({"analysis_path": session.latest_analysis, "result": payload, "previous_report": previous, "report_plan_schema": ReportPlan.model_json_schema()}, ensure_ascii=False)

    @tool
    def render_report(plan: ReportPlan) -> str:
        """Render a new offline HTML report from the latest validated analysis. Summary numbers MUST use {{metric:ID}} tokens. theme=light|dark, chart_order must list all chart IDs or be empty. Never supply new metrics or chart values."""
        with lock:
            session.event("Report Designer", "生成报告")
            try:
                if session.blocked or not session.latest_analysis:
                    raise ValueError("本轮分析失败或没有已验证结果，不能生成报告。")
                if session.reports:
                    previous = json.loads((session.root / session.reports[-1]).with_suffix(".json").read_text(encoding="utf-8"))
                    if previous["analysis"] == session.latest_analysis:
                        # Omitted options on a presentation revision preserve the prior design.
                        plan = ReportPlan.model_validate({**previous["plan"], **plan.model_dump(exclude_unset=True)})
                result = AnalysisResult.model_validate_json(safe_file(session.root, session.latest_analysis).read_text(encoding="utf-8"))
                html = render_html(result, plan, session.latest_analysis)
                version = len(session.reports) + 1
                path = f"reports/report_v{version:03d}.html"
                with (session.root / path).open("x", encoding="utf-8") as handle:
                    handle.write(html)
                write_json(session.root / "reports" / f"report_v{version:03d}.json", {"analysis": session.latest_analysis, "plan": plan.model_dump()})
                session.reports.append(path)
                session.event("Report Designer", "报告已保存", path=path)
                return json.dumps({"status": "saved", "report": path, "version": version}, ensure_ascii=False)
            except Exception as exc:
                return json.dumps({"error": str(exc)}, ensure_ascii=False)

    @tool
    def inspect_artifacts() -> str:
        """List input files, verified analysis and actual reports; check every saved report's links before declaring success."""
        try:
            for path in session.reports:
                validate_html(safe_file(session.root, path).read_text(encoding="utf-8"))
            return json.dumps({**session.inventory(), "analysis_failed_this_turn": session.blocked}, ensure_ascii=False)
        except Exception as exc:
            return json.dumps({"error": str(exc)}, ensure_ascii=False)

    return {
        "analyst": [inspect_data, analysis_contract, run_analysis],
        "designer": [read_analysis, render_report],
        "main": [inspect_artifacts],
    }
