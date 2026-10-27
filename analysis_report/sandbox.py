from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from deepagents_docker import DockerSandbox

from .config import Settings
from .session import Session, safe_file, write_json


def check_docker(image: str):
    try:
        info = subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}"], capture_output=True, text=True, timeout=15)
        if info.returncode:
            raise RuntimeError("Docker 引擎未运行，请先启动 Docker Desktop（Linux containers）。")
        image_check = subprocess.run(["docker", "image", "inspect", image], capture_output=True, text=True, timeout=15)
        if image_check.returncode:
            raise RuntimeError(f"缺少分析镜像 {image}。请在项目目录执行 docker build -f analysis_report/Dockerfile -t {image} .")
    except FileNotFoundError as exc:
        raise RuntimeError("找不到 Docker，请安装并启动 Docker Desktop。") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Docker 状态检查超时，请检查 Docker Desktop。") from exc


def open_sandbox(settings: Settings, session: Session):
    inputs = str((session.root / "inputs").resolve())
    if "," in inputs:
        raise ValueError("Docker 输入挂载目录不能包含逗号。")
    backend = DockerSandbox(
        image=settings.image, shared_dir=session.root / "sandbox",
        timeout=settings.timeout, max_output_bytes=20000,
        memory=settings.memory, cpus=settings.cpus,
        allow_outbound_traffic=False, auto_remove=True,
        extra_run_args=["--mount", f"type=bind,source={inputs},target=/inputs,readonly",
                        "--cap-drop=ALL", "--security-opt=no-new-privileges", "--read-only",
                        "--tmpfs", "/tmp:rw,nosuid,size=256m"],
    )
    return backend


class Executor:
    """Only fixed Python commands are exposed; the agents never receive a shell tool."""

    def __init__(self, session: Session, backend, timeout: int = 120):
        self.session, self.backend, self.timeout = session, backend, timeout

    def execute(self, command: str, log_name: str):
        result = self.backend.execute(command, timeout=self.timeout)
        (self.session.root / "logs" / log_name).write_text(result.output, encoding="utf-8")
        if result.exit_code != 0 or result.truncated:
            raise RuntimeError(f"Python 执行失败（exit={result.exit_code}）：{result.output[-5000:]}")
        return result

    def scratch_file(self, name: str):
        root = self.session.root / "sandbox"
        path = root / name
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError("拒绝访问沙盒中越界或符号链接文件。")
        return path

    def inspect(self, filename: str, sheet: str | None):
        if filename not in {item["file"] for item in self.session.manifest}:
            raise ValueError("未知输入文件，请先查看输入清单。")
        scratch = self.session.root / "sandbox"
        # Restore the trusted helper before each inspection.
        shutil.copyfile(Path(__file__).with_name("worker.py"), self.scratch_file("worker.py"))
        write_json(self.scratch_file("inspect_request.json"), {"file": filename, "sheet": sheet})
        result_file = self.scratch_file("inspect_result.json")
        if result_file.exists():
            result_file.unlink()
        self.execute("python /shared/worker.py /shared/inspect_request.json /shared/inspect_result.json", f"inspect_{self.session.turn}.log")
        result_file = safe_file(scratch, "inspect_result.json")
        if result_file.stat().st_size > 500_000:
            raise ValueError("数据概况缺失或过大。")
        return json.loads(result_file.read_text(encoding="utf-8"))

    def run(self, code: str):
        session = self.session
        if session.attempts >= 3:
            session.blocked = True
            raise ValueError("本轮分析已达到三次执行上限（首次执行加两次修正），请向用户说明失败原因。")
        session.attempts += 1
        session.blocked = True  # Cleared only after execution AND result validation.
        name = f"analysis_{session.turn:03d}_{session.attempts}"
        evidence = session.root / "analyses" / name
        evidence.mkdir()
        (evidence / "analysis.py").write_text(code, encoding="utf-8")
        scratch = session.root / "sandbox"
        work = self.scratch_file(name)
        work.mkdir()
        shutil.copyfile(Path(__file__).with_name("worker.py"), self.scratch_file("worker.py"))
        (work / "analysis.py").write_text(code, encoding="utf-8")
        self.execute(f"cd /shared/{name} && PYTHONPATH=/shared python analysis.py", name + ".log")
        output = safe_file(work, "result.json")
        if output.stat().st_size > 2_000_000:
            raise ValueError("分析脚本必须在当前目录写出不超过 2 MB 的 result.json。")
        raw = output.read_text(encoding="utf-8")
        (evidence / "raw_result.json").write_text(raw, encoding="utf-8")
        return name, evidence, raw
