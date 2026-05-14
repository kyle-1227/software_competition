from __future__ import annotations

from pathlib import Path

SKILLS_DIR = Path(__file__).resolve().parents[1] / "skills"


class SkillsLoader:
    """运行时加载 skill.md 和 AGENT.md 内容，缓存到内存。"""

    _cache: dict[str, dict[str, str]] = {}

    @classmethod
    def load(cls, skill_name: str) -> dict[str, str]:
        """返回 {"skill": "...", "agent": "..."}"""
        if skill_name in cls._cache:
            return cls._cache[skill_name]

        result: dict[str, str] = {}
        skill_dir = SKILLS_DIR / skill_name

        for filename, key in (("skill.md", "skill"), ("AGENT.md", "agent")):
            filepath = skill_dir / filename
            if filepath.exists():
                result[key] = filepath.read_text(encoding="utf-8")

        cls._cache[skill_name] = result
        return result

    @classmethod
    def list_skills(cls) -> list[str]:
        """列出所有可用 skill 名称。"""
        if not SKILLS_DIR.exists():
            return []
        return [
            entry.name
            for entry in SKILLS_DIR.iterdir()
            if entry.is_dir() and (entry / "skill.md").exists()
        ]
