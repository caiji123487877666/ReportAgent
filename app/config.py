"""应用环境配置与角色提示词加载。"""

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


class Settings(BaseModel):
    """从环境变量读取的应用配置。"""

    model_config = ConfigDict(frozen=True)

    openai_api_key: str = Field(
        default_factory=lambda: os.getenv("OPENAI_API_KEY", "")
    )
    openai_model: str = Field(
        default_factory=lambda: os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    )
    openai_base_url: str = Field(
        default_factory=lambda: os.getenv(
            "OPENAI_BASE_URL", "https://api.openai.com/v1"
        )
    )
    langfuse_public_key: str = Field(
        default_factory=lambda: os.getenv("LANGFUSE_PUBLIC_KEY", "")
    )
    langfuse_secret_key: str = Field(
        default_factory=lambda: os.getenv("LANGFUSE_SECRET_KEY", "")
    )
    langfuse_host: str = Field(
        default_factory=lambda: os.getenv(
            "LANGFUSE_HOST", "https://cloud.langfuse.com"
        )
    )


def load_prompts(path: Path | None = None) -> dict[str, Any]:
    """加载角色系统提示词。"""

    prompt_path = path or BASE_DIR / "prompts.yaml"
    with prompt_path.open("r", encoding="utf-8") as file:
        prompts = yaml.safe_load(file)

    if not isinstance(prompts, dict):
        raise ValueError("prompts.yaml 顶层必须是 YAML 映射")
    return prompts


settings = Settings()
PROMPTS = load_prompts()
