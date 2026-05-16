from pathlib import Path

from pydantic import Field, field_validator
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

    # ------------------------------------------------------------------
    # Harness feature flags (Phase 0–4)
    # Set to False to revert to legacy 13-node DAG behaviour.
    # ------------------------------------------------------------------
    # Phase 1
    use_orchestrator: bool = True
    use_input_guardrail: bool = True
    use_real_ai_coding: bool = True
    # Phase 2
    use_evaluator_optimizer: bool = True
    evaluator_max_iterations: int = 3
    evaluator_confidence_threshold: float = 0.7
    # Phase 3
    use_output_guardrail: bool = True
    trace_storage_path: str = "../data/traces"
    trace_exporters: str = "console,json"
    # Phase 4
    memory_max_window: int = 20
    memory_summary_trigger: int = 15
    streaming_enabled: bool = True

    # ------------------------------------------------------------------
    # Bounded Agent Loop
    # ------------------------------------------------------------------
    agent_loop_enabled: bool = True
    agent_loop_max_steps: int = 8
    agent_loop_max_tool_retries: int = 5
    agent_loop_max_retrieval_retries: int = 2
    agent_loop_max_answer_regenerations: int = 2
    agent_loop_confidence_threshold: float = 0.7
    agent_loop_retry_backoff_ms: list[int] = Field(
        default_factory=lambda: [0, 100, 200, 400, 800]
    )
    agent_loop_high_risk_requires_approval: bool = True

    # ------------------------------------------------------------------
    # Reranker (SiliconFlow)
    # ------------------------------------------------------------------
    reranker_enabled: bool = True
    reranker_model: str = "Qwen/Qwen3-VL-Reranker-8B"
    reranker_fallback_model: str = "BAAI/bge-reranker-v2-m3"
    reranker_top_n: int = 5
    reranker_retrieve_multiplier: int = 4
    hyde_enabled: bool = False

    # ------------------------------------------------------------------
    # Embedding provider (SiliconFlow)
    # ------------------------------------------------------------------
    siliconflow_api_key: str | None = None
    siliconflow_base_url: str = "https://api.siliconflow.cn/v1"
    embedding_model: str = "BAAI/bge-large-zh-v1.5"

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
