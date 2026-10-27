"""模型与请求上限的显式配置，认证字段不进入普通序列化。"""

from typing import ClassVar, Literal
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from min_agent.llm.openai_protocol import load_json


class OpenAISettings(BaseModel):
    _credential_key: ClassVar[str] = "openai_api_key"
    _provider_name: ClassVar[str] = "OpenAI"
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid", allow_inf_nan=False)
    api_key: SecretStr = Field(repr=False, exclude=True)
    model: Literal["gpt-6.1-sol"] = "gpt-6.1-sol"
    reasoning_effort: Literal["high"] = "high"
    max_output_tokens: int = Field(default=4096, ge=64, le=32768)
    max_requests: int = Field(default=3, ge=1, le=100)
    timeout_seconds: float = Field(default=60.0, gt=0, le=300)

    @classmethod
    def from_credentials_file(cls, path: str | Path, **options):
        """仅取显式文件中当前提供商字段；不查询环境或个人工具配置。"""
        try:
            data = load_json(Path(path).read_text(encoding="utf-8-sig"))
            if not isinstance(data, dict) or set(data) - {"openai_api_key", "deepseek_api_key"}:
                raise ValueError("凭据文件包含不支持的配置")
            key = data[cls._credential_key]
            if not isinstance(key, str) or not key or not key.isascii() or not key.isprintable() or any(character.isspace() for character in key):
                raise ValueError("密钥格式无效")
            return cls(api_key=SecretStr(key), **options)
        except (OSError, UnicodeError, ValueError, TypeError, KeyError):
            raise ValueError(f"无法加载指定{cls._provider_name}凭据或配置") from None
