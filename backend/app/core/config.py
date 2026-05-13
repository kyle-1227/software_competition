from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "设备检修智能辅助系统"
    app_env: str = "development"
    debug: bool = False
    api_prefix: str = "/api"
    frontend_origins: str = "http://localhost:8001,http://127.0.0.1:8001"
    data_dir: str = "../data"
    deepseek_api_key: str | None = None
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-v4-pro"
    deepseek_thinking_enabled: bool = True
    deepseek_reasoning_effort: str = "high"
    deepseek_temperature: float = 0.2
    deepseek_max_tokens: int = 2048
    run_live_llm_tests: bool = False

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @field_validator("debug", mode="before")
    @classmethod
    def parse_debug(cls, value: bool | str) -> bool:
        # 某些环境会全局设置 DEBUG=release，这里按生产模式处理，避免配置初始化失败。
        if isinstance(value, str) and value.lower() in {"release", "prod", "production"}:
            return False
        return value

    @property
    def cors_origins(self) -> list[str]:
        return [
            origin.strip()
            for origin in self.frontend_origins.split(",")
            if origin.strip()
        ]

    @property
    def data_path(self) -> Path:
        # 相对路径基于 backend 项目根目录解析，而不是基于命令执行目录解析。
        backend_dir = Path(__file__).resolve().parents[2]
        return (backend_dir / self.data_dir).resolve()


settings = Settings()
