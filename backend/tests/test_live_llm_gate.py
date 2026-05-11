import os

import pytest


pytestmark = pytest.mark.skipif(
    not (os.getenv("RUN_LIVE_LLM_TESTS") == "1" and os.getenv("DEEPSEEK_API_KEY")),
    reason="live LLM tests require RUN_LIVE_LLM_TESTS=1 and DEEPSEEK_API_KEY",
)


def test_live_llm_gate_placeholder() -> None:
    assert True
