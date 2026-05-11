from app.services.tools.ai_coding import AICodingTool


async def generate_script(task: str, language: str | None = None) -> dict[str, object]:
    tool = AICodingTool()
    payload = {"task": task}
    if language:
        payload["language"] = language
    result = await tool.run(payload)
    return result.model_dump(mode="json")
