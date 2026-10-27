from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from analysis_report.config import Settings
from analysis_report.sandbox import Executor, check_docker, open_sandbox
from analysis_report.session import Session
from analysis_report.tools import make_tools

pytestmark = pytest.mark.docker


@pytest.fixture(scope="module")
def live(tmp_path_factory):
    root = tmp_path_factory.mktemp("docker_analysis")
    inputs = root / "fixtures"
    inputs.mkdir()
    check_docker("analysis-report:0.1")
    create = """
from pathlib import Path
import pandas as pd
p = Path('/fixtures')
df = pd.DataFrame({'地区': ['华东','华南','华东'], '金额': [100,200,None]})
df.to_csv(p/'sales.csv', index=False)
df.to_csv(p/'gbk.csv', index=False, encoding='gb18030')
with pd.ExcelWriter(p/'sheets.xlsx') as w:
    df.to_excel(w, sheet_name='销售', index=False)
    pd.DataFrame({'备注':['待确认']}).to_excel(w, sheet_name='说明', index=False)
pd.DataFrame({'类别':['甲','乙'], '混合':['10','未知']}).to_csv(p/'mixed.csv', index=False)
pd.DataFrame(columns=['类别','值']).to_csv(p/'empty.csv', index=False)
"""
    subprocess.run(["docker", "run", "--rm", "--network=none", "--mount", f"type=bind,source={inputs},target=/fixtures", "analysis-report:0.1", "python", "-c", create], check=True, capture_output=True, text=True, encoding="utf-8", timeout=60)
    session = Session.create([inputs / name for name in ["sales.csv", "gbk.csv", "sheets.xlsx", "mixed.csv", "empty.csv"]], root / "runs")
    session.begin_turn()
    settings = Settings("test", "openai", "unused", None, timeout=15)
    with open_sandbox(settings, session) as backend:
        executor = Executor(session, backend, 15)
        tools = {t.name: t for group in make_tools(session, executor).values() for t in group}
        yield session, backend, executor, tools


def test_real_container_csv_excel_and_edge_cases(live):
    session, backend, executor, tools = live
    csv = json.loads(tools["inspect_data"].invoke({"file": "01_sales.csv"}))
    assert csv["rows"] == 3 and csv["columns"][1]["missing"] == 1
    gbk = json.loads(tools["inspect_data"].invoke({"file": "02_gbk.csv"}))
    assert gbk["sample"][0]["地区"] == "华东"
    book = json.loads(tools["inspect_data"].invoke({"file": "03_sheets.xlsx"}))
    assert book["sheets"] == ["销售", "说明"] and "rows" not in book
    sheet = json.loads(tools["inspect_data"].invoke({"file": "03_sheets.xlsx", "sheet": "销售"}))
    assert sheet["rows"] == 3
    absent = json.loads(tools["inspect_data"].invoke({"file": "03_sheets.xlsx", "sheet": "不存在"}))
    assert "error" in absent
    mixed = json.loads(tools["inspect_data"].invoke({"file": "04_mixed.csv"}))
    assert mixed["columns"][1]["dtype"] == "object"
    empty = json.loads(tools["inspect_data"].invoke({"file": "05_empty.csv"}))
    assert empty["rows"] == 0 and empty["statistics"] == {}
    assert "error" in json.loads(tools["inspect_data"].invoke({"file": "../../.env"}))


def test_real_computation_and_render(live):
    session, backend, executor, tools = live
    session.begin_turn()
    tools["inspect_data"].invoke({"file": "01_sales.csv"})
    code = """
import json
from worker import read_frame
df = read_frame('01_sales.csv')
totals = df.groupby('地区')['金额'].sum()
result = {
 'title':'真实计算验收',
 'sources':[{'file':'01_sales.csv','rows':len(df),'columns':list(df.columns)}],
 'metrics':[
  {'id':'total','label':'总金额','value':float(df['金额'].sum()),'definition':'金额非空值求和','source':'01_sales.csv'},
  {'id':'missing','label':'缺失金额','value':int(df['金额'].isna().sum()),'definition':'金额列缺失行数','source':'01_sales.csv'}],
 'tables':[{'id':'regions','title':'地区汇总','columns':['地区','金额'],'rows':[[str(k),float(v)] for k,v in totals.items()]}],
 'charts':[{'id':'compare','title':'地区比较','kind':'bar','table_id':'regions','x':'地区','y':'金额'}],
 'findings':[{'text':'金额存在缺失；未做填补。','evidence':['missing']}],
 'methods':['按地区求和，保留原始行，金额缺失值不计入求和。'],
 'limitations':['没有日期字段，不执行趋势分析。']
}
with open('result.json','w',encoding='utf-8') as f: json.dump(result,f,ensure_ascii=False,allow_nan=False)
"""
    result = json.loads(tools["run_analysis"].invoke({"code": code}))
    assert result["status"] == "validated", result
    saved = json.loads((session.root / session.latest_analysis).read_text(encoding="utf-8"))
    assert saved["metrics"][0]["value"] == 300
    assert saved["metrics"][1]["value"] == 1
    assert sum(row[1] for row in saved["tables"][0]["rows"]) == 300
    report = json.loads(tools["render_report"].invoke({"plan": {"title": "真实计算验收", "summary": ["总金额为 {{metric:total}}。"]}}))
    assert report["status"] == "saved", report
    assert (session.root / report["report"]).stat().st_size > 1_000_000


def test_readonly_inputs_and_timeout(live):
    session, backend, executor, tools = live
    result = backend.execute("python -c \"from pathlib import Path; Path('/inputs/01_sales.csv').write_text('changed')\"", timeout=10)
    assert result.exit_code != 0 and "Read-only" in result.output
    assert "changed" not in (session.root / "inputs" / "01_sales.csv").read_text(encoding="utf-8")
    timeout = backend.execute("python -c 'import time; time.sleep(10)'", timeout=1)
    assert timeout.exit_code == 124


def test_real_missing_column_blocks_new_report(live):
    session, backend, executor, tools = live
    session.begin_turn()
    result = json.loads(tools["run_analysis"].invoke({"code": "from worker import read_frame\ndf=read_frame('01_sales.csv')\nprint(df['不存在'])"}))
    assert "error" in result and "KeyError" in result["error"]
    before = len(session.reports)
    report = json.loads(tools["render_report"].invoke({"plan": {"title": "should fail"}}))
    assert "error" in report and len(session.reports) == before
