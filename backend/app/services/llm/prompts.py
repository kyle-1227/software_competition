from pathlib import Path


def load_prompt_text(filename: str) -> str:
    prompt_path = Path(__file__).resolve().parents[2] / "prompts" / filename
    if not prompt_path.exists():
        return ""
    return prompt_path.read_text(encoding="utf-8")
