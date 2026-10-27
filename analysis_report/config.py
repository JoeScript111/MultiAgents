from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    model: str
    provider: str
    api_key: str
    base_url: str | None
    image: str = "analysis-report:0.1"
    timeout: int = 120
    memory: str = "2g"
    cpus: float = 2.0
    output: Path = PROJECT_ROOT / "output" / "analysis_report"

    @classmethod
    def from_env(cls):
        load_dotenv(PROJECT_ROOT / ".env")
        config = cls(
            model=os.getenv("MODEL", "glm-5.3-flash"),
            provider=os.getenv("MODEL_PROVIDER", "openai"),
            api_key=os.getenv("OPENAI_API_KEY", ""),
            base_url=os.getenv("OPENAI_BASE_URL") or None,
            image=os.getenv("ANALYSIS_DOCKER_IMAGE", "analysis-report:0.1"),
            timeout=int(os.getenv("ANALYSIS_TIMEOUT", "120")),
            memory=os.getenv("ANALYSIS_MEMORY", "2g"),
            cpus=float(os.getenv("ANALYSIS_CPUS", "2")),
            output=Path(os.getenv("ANALYSIS_OUTPUT_DIR", str(cls.output))).resolve(),
        )
        if not config.api_key.strip():
            raise ValueError("请在项目 .env 中配置 OPENAI_API_KEY。")
        if config.timeout <= 0 or config.cpus <= 0:
            raise ValueError("ANALYSIS_TIMEOUT 和 ANALYSIS_CPUS 必须大于零。")
        return config
