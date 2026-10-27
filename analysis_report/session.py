from __future__ import annotations

import hashlib
import json
import shutil
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

SUPPORTED = {".csv", ".xlsx", ".xls"}


def write_json(path: Path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def safe_file(root: Path, relative: str) -> Path:
    candidate = root / relative
    path = candidate.resolve()
    if candidate.is_symlink() or not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError(f"无效的产物路径：{relative}")
    return path


@dataclass
class Session:
    root: Path
    manifest: list[dict]
    turn: int = 0
    attempts: int = 0
    blocked: bool = False
    latest_analysis: str | None = None
    reports: list[str] = field(default_factory=list)

    @classmethod
    def create(cls, files: list[Path], output: Path):
        resolved = [p.expanduser().resolve() for p in files]
        for path in resolved:
            if not path.is_file() or path.suffix.lower() not in SUPPORTED:
                raise ValueError(f"请输入存在的 CSV / XLSX / XLS 文件：{path}")
            if path.stat().st_size == 0:
                raise ValueError(f"输入文件为空：{path.name}")
        if len(resolved) != len(set(resolved)):
            raise ValueError("同一个输入文件不能重复传入。")
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]
        root = output / run_id
        for folder in ("inputs", "sandbox", "analyses", "logs", "reports"):
            (root / folder).mkdir(parents=True, exist_ok=False)
        manifest = []
        for index, path in enumerate(resolved, 1):
            name = f"{index:02d}_{path.name}"
            destination = root / "inputs" / name
            shutil.copyfile(path, destination)
            digest = hashlib.sha256(destination.read_bytes()).hexdigest()
            manifest.append({"file": name, "original_name": path.name, "sha256": digest, "bytes": destination.stat().st_size})
        write_json(root / "manifest.json", manifest)
        return cls(root, manifest)

    @property
    def id(self):
        return self.root.name

    def begin_turn(self):
        self.turn += 1
        self.attempts = 0
        self.blocked = False

    def event(self, agent: str, action: str, **details):
        record = {"time": datetime.now().isoformat(), "turn": self.turn, "agent": agent, "action": action, **details}
        with (self.root / "logs" / "events.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        print(f"[{agent}] {action}", flush=True)

    def inventory(self):
        return {"inputs": self.manifest, "latest_analysis": self.latest_analysis, "reports": list(self.reports)}
