import os
import sys
from pathlib import Path

import pytest


BACKEND_DIR = Path(__file__).resolve().parents[1]
# 允许从仓库根目录运行 pytest 时直接导入 app.*。
sys.path.insert(0, str(BACKEND_DIR))
# 单元测试默认离线运行，避免真实 .env 中的 DeepSeek key 触发外部调用。
os.environ["DEEPSEEK_API_KEY"] = ""


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _force_offline_llm_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RUN_LIVE_LLM_TESTS", raising=False)
