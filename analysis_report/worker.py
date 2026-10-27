"""Container-only data reader and bounded profiler. Also importable by analysis scripts."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

INPUT_ROOT = Path("/inputs")


def read_frame(filename: str, sheet: str | None = None):
    path = (INPUT_ROOT / filename).resolve()
    if not path.is_relative_to(INPUT_ROOT.resolve()) or not path.is_file():
        raise ValueError("Input path must be inside /inputs")
    if path.suffix.lower() == ".csv":
        if sheet is not None:
            raise ValueError("CSV does not have worksheets")
        for encoding in ("utf-8-sig", "gb18030"):
            try:
                return pd.read_csv(path, encoding=encoding)
            except UnicodeDecodeError:
                continue
        raise ValueError("CSV 编码无法识别，请转为 UTF-8")
    if sheet is None:
        raise ValueError("Excel requires an explicit worksheet name; inspect sheets first")
    return pd.read_excel(path, sheet_name=sheet)


def profile(filename: str, sheet: str | None = None):
    path = INPUT_ROOT / filename
    if path.suffix.lower() != ".csv" and sheet is None:
        # Read sheet names before selecting/reading data from any sheet.
        with pd.ExcelFile(path) as book:
            return {"file": filename, "sheets": book.sheet_names, "note": "请指定工作表；不要默认合并。"}
    frame = read_frame(filename, sheet)
    return {
        "file": filename, "sheet": sheet, "rows": len(frame),
        "columns": [{"name": str(c), "dtype": str(frame[c].dtype), "missing": int(frame[c].isna().sum())} for c in frame.columns],
        "duplicate_rows": int(frame.duplicated().sum()),
        "sample": json.loads(frame.head(5).to_json(orient="records", force_ascii=False, date_format="iso")),
        "statistics": json.loads(frame.describe(include="all").to_json(force_ascii=False, date_format="iso")) if len(frame.columns) and len(frame) else {},
    }


def main():
    request = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    output = Path(sys.argv[2])
    result = profile(request["file"], request.get("sheet"))
    output.write_text(json.dumps(result, ensure_ascii=False, allow_nan=False), encoding="utf-8")


if __name__ == "__main__":
    main()
