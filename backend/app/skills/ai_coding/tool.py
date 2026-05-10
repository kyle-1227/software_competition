from app.services.tools.ai_coding import AICodingTool


async def generate_script(task: str) -> dict[str, object]:
    tool = AICodingTool()
    result = await tool.run({"task": task})
    return result.model_dump(mode="json")
