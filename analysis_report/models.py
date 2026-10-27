"""The evidence contract shared by the analyst and report renderer."""

from __future__ import annotations

import math
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Source(Contract):
    file: str
    sheet: str | None = None
    rows: int = Field(ge=0)
    columns: list[str]


class Metric(Contract):
    id: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_-]*$")
    label: str
    value: float | int | str | None
    unit: str = ""
    definition: str
    source: str


class Table(Contract):
    id: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_-]*$")
    title: str
    columns: list[str] = Field(min_length=1)
    rows: list[list[str | float | int | bool | None]] = Field(max_length=1000)
    description: str = ""

    @model_validator(mode="after")
    def rectangular(self):
        if len(set(self.columns)) != len(self.columns):
            raise ValueError("Table column names must be unique")
        if any(len(row) != len(self.columns) for row in self.rows):
            raise ValueError("Table rows must match the column count")
        return self


class Chart(Contract):
    id: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_-]*$")
    title: str
    kind: Literal["bar", "line", "scatter", "pie"]
    table_id: str
    x: str
    y: str
    description: str = ""


class Finding(Contract):
    text: str
    evidence: list[str] = Field(min_length=1)


class AnalysisResult(Contract):
    title: str
    sources: list[Source] = Field(min_length=1)
    metrics: list[Metric] = Field(default_factory=list)
    tables: list[Table] = Field(default_factory=list)
    charts: list[Chart] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    methods: list[str] = Field(min_length=1)
    limitations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def valid_references(self):
        ids = [item.id for group in (self.metrics, self.tables, self.charts) for item in group]
        if len(ids) != len(set(ids)):
            raise ValueError("Evidence IDs must be globally unique")
        files = {s.file for s in self.sources}
        if any(m.source not in files for m in self.metrics):
            raise ValueError("Every metric source must reference a source file")
        tables = {t.id: t for t in self.tables}
        for chart in self.charts:
            table = tables.get(chart.table_id)
            if not table or chart.x not in table.columns or chart.y not in table.columns:
                raise ValueError(f"Chart {chart.id} references an unknown table/column")
            y_index = table.columns.index(chart.y)
            if any(row[y_index] is not None and (isinstance(row[y_index], bool) or not isinstance(row[y_index], (int, float))) for row in table.rows):
                raise ValueError(f"Chart {chart.id} requires numeric y values")
            if chart.kind == "pie" and any(row[y_index] is not None and row[y_index] < 0 for row in table.rows):
                raise ValueError("Pie charts cannot contain negative values")
        for finding in self.findings:
            if not set(finding.evidence) <= set(ids):
                raise ValueError("Finding references unknown evidence")
        # Reject NaN/Infinity even when nested in union-typed cells.
        def check(value):
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError("Use null instead of NaN or Infinity")
            if isinstance(value, dict):
                for item in value.values():
                    check(item)
            if isinstance(value, list):
                for item in value:
                    check(item)
        check(self.model_dump())
        return self


class ReportPlan(Contract):
    title: str
    summary: list[str] = Field(default_factory=list, max_length=8)
    theme: Literal["light", "dark"] = "light"
    chart_order: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def evidence_tokens(self):
        # Designer prose contains no new numbers: use {{metric:revenue}}.
        for line in self.summary:
            stripped = re.sub(r"\{\{metric:[A-Za-z][A-Za-z0-9_-]*\}\}", "", line)
            if re.search(r"[0-9]|[零〇一二三四五六七八九十百千万亿两]|\{\{|\}\}", stripped):
                raise ValueError("Summary numbers must use {{metric:ID}} tokens; use qualitative prose otherwise")
        return self
