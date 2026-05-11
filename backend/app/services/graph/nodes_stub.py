from typing import Any


async def run_fallback_flow(services, state: dict[str, Any]) -> dict[str, Any]:
    del services
    return {
        **state,
        "response": state.get("response"),
    }
