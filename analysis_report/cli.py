from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import Settings
from .sandbox import Executor, check_docker, open_sandbox
from .session import Session


HELP = """输入自然语言需求可继续分析、追问或修改报告。
/help   显示帮助
/files  列出输入、分析结果与各版报告
/exit   退出并清理容器；文件保留，会话记忆清除
"""


def print_files(session: Session):
    print(f"\n本次运行：{session.root}")
    for item in session.manifest:
        print(f"  输入：{session.root / 'inputs' / item['file']}")
    if session.latest_analysis:
        print(f"  分析：{session.root / session.latest_analysis}")
    for path in session.reports:
        print(f"  报告：{session.root / path}")


def message_text(message):
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    return "\n".join(block.get("text", "") for block in content if isinstance(block, dict) and block.get("type") == "text")


def run_turn(agent, session: Session, question: str):
    session.begin_turn()
    session.event("Main Agent", "处理需求")
    session.event("User", "message", text=question)
    final_text = ""
    for update in agent.stream(
        {"messages": [{"role": "user", "content": question}]},
        config={"configurable": {"thread_id": session.id}, "recursion_limit": 80},
        stream_mode="updates",
    ):
        for node in update.values():
            if not isinstance(node, dict):
                continue
            for message in node.get("messages", []):
                if getattr(message, "type", None) == "ai":
                    for call in getattr(message, "tool_calls", []):
                        print(f"[Main Agent] 工具：{call['name']}", flush=True)
                    if not getattr(message, "tool_calls", []):
                        final_text = message_text(message)
    if final_text:
        print("\n" + final_text)
        session.event("Main Agent", "reply", text=final_text)
    if session.blocked:
        print("\n本轮分析未通过验证；已有报告属于此前版本。")
    print_files(session)


def main(argv=None):
    # Windows pipes otherwise use the active legacy code page for Chinese text.
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="DeepAgents 多智能体数据分析与 HTML 报告（持续对话）")
    parser.add_argument("--data", action="append", required=True, type=Path, help="CSV/XLSX/XLS 文件，可重复指定")
    parser.add_argument("--question", help="首次分析需求；省略则交互输入")
    args = parser.parse_args(argv)
    session = None
    try:
        settings = Settings.from_env()
        # Validate inputs before Docker startup and before creating any outputs.
        for path in args.data:
            if not path.is_file() or path.suffix.lower() not in {".csv", ".xlsx", ".xls"}:
                raise ValueError(f"不存在或不支持的输入文件：{path}")
            if path.stat().st_size == 0:
                raise ValueError(f"输入文件为空：{path}")
        check_docker(settings.image)
        session = Session.create(args.data, settings.output)
        print(f"模型：{settings.provider}:{settings.model}\n运行目录：{session.root}\n{HELP}")
        # Lazy import keeps --help independent of model initialization.
        from .agents import build_agent
        with open_sandbox(settings, session) as backend:
            executor = Executor(session, backend, settings.timeout)
            agent = build_agent(settings, session, executor)
            question = args.question
            while True:
                if question is None:
                    try:
                        question = input("\n你 > ").strip()
                    except EOFError:
                        break
                question = question.strip()
                if question == "/exit":
                    break
                if question == "/help":
                    print(HELP)
                elif question == "/files":
                    print_files(session)
                elif question:
                    try:
                        run_turn(agent, session, question)
                    except Exception as exc:
                        session.event("Main Agent", "执行失败", error=type(exc).__name__)
                        print(f"本轮未完成：{type(exc).__name__}。详情已记录；可重试或输入 /exit。", file=sys.stderr)
                        # Do not log potentially credential-bearing HTTP request objects.
                        (session.root / "logs" / f"turn_{session.turn}_error.txt").write_text(
                            str(exc).replace(settings.api_key, "[REDACTED]"), encoding="utf-8")
                question = None
        print("已退出。容器已清理，分析与报告文件已保留。")
        return 0
    except KeyboardInterrupt:
        print("\n已取消。正在清理容器；已有文件保留。")
        return 130
    except Exception as exc:
        print(f"启动失败：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
