from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_openai import ChatOpenAI
from pydantic import PrivateAttr, ValidationError

from analysis_report.agents import build_agent
from analysis_report.cli import run_turn
from analysis_report.config import Settings
from analysis_report.models import AnalysisResult, ReportPlan
from analysis_report.report import render_html, validate_html
from analysis_report.sandbox import Executor, check_docker, open_sandbox
from analysis_report.session import Session, safe_file
from analysis_report.tools import make_tools


@pytest.fixture
def session(tmp_path):
    source = tmp_path / "中文.csv"
    source.write_text("地区,金额\n华东,100\n华南,200\n华东,300\n", encoding="utf-8")
    result = Session.create([source], tmp_path / "runs")
    result.begin_turn()
    return result


def result_dict(file="01_中文.csv"):
    return {
        "title": "销售分析", "sources": [{"file": file, "sheet": None, "rows": 3, "columns": ["地区", "金额"]}],
        "metrics": [{"id": "total", "label": "总金额", "value": 600, "unit": "元", "definition": "金额求和", "source": file}],
        "tables": [{"id": "regions", "title": "地区汇总", "columns": ["地区", "金额"], "rows": [["华东", 400], ["华南", 200]]}],
        "charts": [{"id": "region_chart", "title": "地区金额对比", "kind": "bar", "table_id": "regions", "x": "地区", "y": "金额"}],
        "findings": [{"text": "华东金额高于华南。", "evidence": ["regions"]}],
        "methods": ["按地区汇总金额；未删除记录。"], "limitations": ["示例数据不代表总体。"],
    }


class FakeBackend:
    """No generated code is executed by unit tests; simulate the Docker boundary."""
    def __init__(self, session, fail=False, invalid=False):
        self.session, self.fail, self.invalid = session, fail, invalid
        self.calls = []

    def execute(self, command, *, timeout):
        self.calls.append(command)
        if "worker.py" in command:
            payload = {"file": self.session.manifest[0]["file"], "rows": 3, "columns": [{"name": "地区"}, {"name": "金额"}], "sample": [{"地区": "华东", "金额": 100}]}
            (self.session.root / "sandbox" / "inspect_result.json").write_text(json.dumps(payload), encoding="utf-8")
        elif self.fail:
            return SimpleNamespace(output="Command timed out", exit_code=124, truncated=False)
        else:
            name = re.search(r"cd /shared/(analysis_\d+_\d+)", command)[1]
            payload = result_dict(self.session.manifest[0]["file"])
            if self.invalid:
                payload["charts"][0]["table_id"] = "missing"
            (self.session.root / "sandbox" / name / "result.json").write_text(json.dumps(payload), encoding="utf-8")
        return SimpleNamespace(output="ok", exit_code=0, truncated=False)


def tool_map(session, backend=None):
    backend = backend or FakeBackend(session)
    groups = make_tools(session, Executor(session, backend))
    return {t.name: t for group in groups.values() for t in group}, backend


def analyze(tools, session):
    tools["inspect_data"].invoke({"file": session.manifest[0]["file"]})
    return json.loads(tools["run_analysis"].invoke({"code": "# Simulated script"}))


def test_analysis_schema_checks_references_and_nonfinite():
    payload = result_dict()
    assert AnalysisResult.model_validate(payload).metrics[0].value == 600
    payload["findings"][0]["evidence"] = ["missing"]
    with pytest.raises(ValidationError):
        AnalysisResult.model_validate(payload)
    payload = result_dict()
    payload["tables"][0]["rows"][0][1] = float("nan")
    with pytest.raises(ValidationError):
        AnalysisResult.model_validate(payload)


@pytest.mark.parametrize("change", ["duplicate_id", "bad_source", "row_width", "nonnumeric_chart"])
def test_invalid_evidence_contract(change):
    payload = result_dict()
    if change == "duplicate_id":
        payload["charts"][0]["id"] = "total"
    elif change == "bad_source":
        payload["metrics"][0]["source"] = "unknown.csv"
    elif change == "row_width":
        payload["tables"][0]["rows"][0].append(1)
    else:
        payload["tables"][0]["rows"][0][1] = "not a number"
    with pytest.raises(ValidationError):
        AnalysisResult.model_validate(payload)


def test_report_binds_metrics_and_escapes_untrusted_content():
    payload = result_dict()
    payload["tables"][0]["rows"][0][0] = '</script><script>alert(1)</script>'
    result = AnalysisResult.model_validate(payload)
    html = render_html(result, ReportPlan(title='<img src=x onerror="alert(1)">', summary=["总金额为 {{metric:total}}。"]), "test")
    assert 'href="#e-total">600元</a>' in html
    assert '&lt;img src=x' in html
    assert '<script>alert(1)</script>' not in html
    assert 'Plotly.newPlot' in html
    assert 'cdn.plot.ly/plotly' not in html[:html.find('<script>')]
    validate_html(html)
    with pytest.raises(ValueError):
        render_html(result, ReportPlan(title="报告", summary=["{{metric:unknown}}"]), "test")
    with pytest.raises(ValidationError):
        ReportPlan(title="报告", summary=["金额为 999 元"])


def test_versions_and_presentation_only_reuse(session):
    tools, backend = tool_map(session)
    assert analyze(tools, session)["status"] == "validated"
    first = json.loads(tools["render_report"].invoke({"plan": {"title": "报告", "summary": ["总金额 {{metric:total}}。"]}}))
    before = len(backend.calls)
    session.begin_turn()
    second = json.loads(tools["render_report"].invoke({"plan": {"title": "深色报告", "theme": "dark"}}))
    assert len(backend.calls) == before
    assert first["report"] != second["report"]
    assert all((session.root / p).is_file() for p in session.reports)
    assert "#101827" in (session.root / second["report"]).read_text(encoding="utf-8")
    first_plan = json.loads((session.root / first["report"]).with_suffix(".json").read_text(encoding="utf-8"))
    second_plan = json.loads((session.root / second["report"]).with_suffix(".json").read_text(encoding="utf-8"))
    assert first_plan["plan"]["summary"] == second_plan["plan"]["summary"]


@pytest.mark.parametrize("invalid", [False, True])
def test_failed_or_invalid_analysis_retries_and_blocks_report(session, invalid):
    backend = FakeBackend(session, fail=not invalid, invalid=invalid)
    tools, _ = tool_map(session, backend)
    tools["inspect_data"].invoke({"file": session.manifest[0]["file"]})
    for _ in range(4):
        assert "error" in json.loads(tools["run_analysis"].invoke({"code": "bad"}))
    assert session.attempts == 3
    assert len([c for c in backend.calls if "analysis.py" in c]) == 3
    assert "error" in json.loads(tools["render_report"].invoke({"plan": {"title": "不应生成"}}))
    assert session.reports == []


def test_failed_new_analysis_does_not_publish_stale_result(session):
    backend = FakeBackend(session)
    tools, _ = tool_map(session, backend)
    analyze(tools, session)
    session.begin_turn()
    backend.fail = True
    analyze(tools, session)
    assert "error" in json.loads(tools["render_report"].invoke({"plan": {"title": "stale"}}))


def test_source_profile_must_match(session):
    tools, _ = tool_map(session)
    # No inspection performed; even a valid JSON structure cannot be published.
    assert "error" in json.loads(tools["run_analysis"].invoke({"code": "anything"}))
    assert session.latest_analysis is None


def test_paths_and_empty_inputs(tmp_path):
    empty = tmp_path / "empty.csv"
    empty.touch()
    with pytest.raises(ValueError, match="为空"):
        Session.create([empty], tmp_path / "out")
    with pytest.raises(ValueError):
        safe_file(tmp_path / "inside", "../empty.csv")


def test_docker_unavailable_and_mount_scope(session, monkeypatch):
    monkeypatch.setattr("analysis_report.sandbox.subprocess.run", lambda *a, **k: SimpleNamespace(returncode=1))
    with pytest.raises(RuntimeError, match="Docker 引擎未运行"):
        check_docker("image")
    captured = {}
    monkeypatch.setattr("analysis_report.sandbox.DockerSandbox", lambda **kwargs: captured.update(kwargs) or captured)
    settings = Settings("model", "openai", "test", None)
    open_sandbox(settings, session)
    assert captured["shared_dir"] == session.root / "sandbox"
    assert captured["allow_outbound_traffic"] is False
    mount = captured["extra_run_args"][1]
    assert "target=/inputs,readonly" in mount
    assert str(session.root / "inputs") in mount


class ScriptedChatModel(ChatOpenAI):
    """Real LangGraph/tool routing, deterministic model responses, no network."""
    _counter: int = PrivateAttr(default=0)
    _seen: list = PrivateAttr(default_factory=list)

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self._counter += 1
        system = str(messages[0].content)
        last = messages[-1]
        self._seen.append((system[:70], last.type))

        def call(name, args):
            return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"test-{self._counter}", "type": "tool_call"}])

        if "你是 DS Analyst" in system:
            if last.type == "human":
                response = call("inspect_data", {"file": "01_中文.csv"})
            elif getattr(last, "name", "") == "inspect_data":
                response = call("run_analysis", {"code": "# FakeBackend supplies computed results"})
            else:
                response = AIMessage(content="validated，分析已保存。")
        elif "你是 Report Designer" in system:
            if last.type == "human":
                response = call("read_analysis", {})
            elif getattr(last, "name", "") == "read_analysis":
                response = call("render_report", {"plan": {"title": "模拟报告", "summary": ["总金额 {{metric:total}}。"], "theme": "dark"}})
            else:
                response = AIMessage(content="报告已保存。")
        else:
            # New user turns choose new computation or reuse based on wording.
            if last.type == "human":
                if "澄清" in last.content:
                    response = AIMessage(content="请说明需要分析的工作表。")
                else:
                    specialist = "report-designer" if "排版" in last.content else "ds-analyst"
                    response = call("task", {"subagent_type": specialist, "description": last.content})
            elif last.type == "tool" and getattr(last, "name", "") == "task":
                previous = next(m for m in reversed(messages[:-1]) if getattr(m, "tool_calls", []))
                name = previous.tool_calls[0]["args"]["subagent_type"]
                response = call("task", {"subagent_type": "report-designer", "description": "生成报告"}) if name == "ds-analyst" else call("inspect_artifacts", {})
            else:
                response = AIMessage(content="任务完成，请查看报告路径。")
        return ChatResult(generations=[ChatGeneration(message=response)])


def test_real_graph_delegates_both_agents_and_retains_conversation(session):
    settings = Settings("test-model", "openai", "test", None)
    model = ScriptedChatModel(model="test-model", api_key="test", temperature=0)
    backend = FakeBackend(session)
    agent = build_agent(settings, session, Executor(session, backend), model=model)
    run_turn(agent, session, "请分析数据并生成报告")
    assert len(session.reports) == 1
    calls = len(backend.calls)
    run_turn(agent, session, "修改排版为深色")
    assert len(session.reports) == 2 and len(backend.calls) == calls
    run_turn(agent, session, "需要澄清工作表")
    assert len(session.reports) == 2
    run_turn(agent, session, "补充分析")
    assert len(session.reports) == 3 and len(backend.calls) > calls
    state = agent.get_state({"configurable": {"thread_id": session.id}})
    human_messages = [m for m in state.values["messages"] if m.type == "human"]
    assert len(human_messages) == 4
    events = (session.root / "logs" / "events.jsonl").read_text(encoding="utf-8")
    assert '"subagent": "ds-analyst"' in events
    assert '"subagent": "report-designer"' in events


@pytest.mark.parametrize("error", [KeyboardInterrupt, RuntimeError])
def test_cli_cleans_up_on_cancel_or_model_failure(tmp_path, monkeypatch, error):
    from contextlib import contextmanager
    from analysis_report.cli import main
    settings = Settings("test", "openai", "secret-test-key", None, output=tmp_path / "runs")
    data = tmp_path / "sales.csv"
    data.write_text("x\n1\n", encoding="utf-8")
    closed = []

    @contextmanager
    def sandbox(*args):
        try:
            yield object()
        finally:
            closed.append(True)

    class FailedAgent:
        def stream(self, *args, **kwargs):
            raise error("model secret-test-key failed")

    monkeypatch.setattr(Settings, "from_env", lambda: settings)
    monkeypatch.setattr("analysis_report.cli.check_docker", lambda *a: None)
    monkeypatch.setattr("analysis_report.cli.open_sandbox", sandbox)
    monkeypatch.setattr("analysis_report.agents.build_agent", lambda *a, **k: FailedAgent())
    monkeypatch.setattr("builtins.input", lambda *a: "/exit")
    status = main(["--data", str(data), "--question", "分析"])
    assert closed == [True]
    assert status == (130 if error is KeyboardInterrupt else 0)
    if error is RuntimeError:
        log = next((tmp_path / "runs").rglob("turn_1_error.txt")).read_text(encoding="utf-8")
        assert "secret-test-key" not in log and "[REDACTED]" in log


def test_guard_prevents_stale_report_after_analyst_cannot_complete(session):
    tools, backend = tool_map(session)
    analyze(tools, session)
    backend.fail = True
    model = ScriptedChatModel(model="test-model", api_key="test")
    settings = Settings("test-model", "openai", "test", None)
    agent = build_agent(settings, session, Executor(session, backend), model=model)
    # Fake analyst incorrectly claims success; main's tool guard still blocks publication.
    run_turn(agent, session, "补充计算")
    assert session.blocked and session.reports == []
