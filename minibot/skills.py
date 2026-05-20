"""skills.py — 技能（Skill）加载器。

对应 Nanobot 的 agent/skills.py（精简版）。

技能目录结构：
    skills/
        greeting/
            SKILL.md          # 必有，含 YAML frontmatter
            scripts/...       # 可选，技能私有脚本

SKILL.md frontmatter 示例：
    ---
    name: greeting
    description: 礼貌地问候用户并自我介绍
    always: false             # true 表示永远挂载到 system prompt
    ---
    # 你的技能正文（Markdown）
    当用户打招呼时...

加载策略：
    - 启动时扫描 workspace/skills/，解析所有 SKILL.md 的 frontmatter。
    - always=true 的技能始终激活。
    - 其他技能按需激活（core 可通过 LLM 自主决策或显式调用）。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml


class SkillsLoader:
    """扫描 skills 目录，按需把 SKILL.md 注入 system prompt。"""

    def __init__(self, skills_dir: Path) -> None:
        """初始化加载器。

        Args:
            skills_dir: 技能根目录，如 workspace / "skills"。

        TODO:
            - self.skills_dir = skills_dir
            - self._cache: dict[str, dict] = {}  # name -> {meta, body, path}
            - 立即扫描一次（self._scan）
        """
        self.skills_dir = skills_dir
        self._cache: dict[str, dict[str, Any]] = {}
        self._scan()

    def _scan(self) -> None:
        """遍历 skills_dir 下所有子目录的 SKILL.md，填充 self._cache。

        TODO:
            - for sub in skills_dir.iterdir(): if (sub / "SKILL.md").exists(): parse
            - 解析 frontmatter（yaml.safe_load）和正文
        """
        self._cache = {}
        if not self.skills_dir.exists():
            return

        for sub in sorted(self.skills_dir.iterdir()):
            skill_md = sub / "SKILL.md"
            if not sub.is_dir() or not skill_md.exists():
                continue
            meta, body = self._parse_skill_md(skill_md)
            name = str(meta.get("name") or sub.name)
            self._cache[name] = {
                "name": name,
                "description": meta.get("description", ""),
                "always": bool(meta.get("always", False)),
                "body": body.strip(),
                "path": skill_md,
            }

    @staticmethod
    def _parse_skill_md(path: Path) -> tuple[dict[str, Any], str]:
        """切分 SKILL.md 的 YAML frontmatter 和正文。

        Args:
            path: SKILL.md 路径。

        Returns:
            (metadata_dict, body_str)；frontmatter 缺失时 metadata 为 {}。

        TODO:
            - 用正则 r"^---\\n(.*?)\\n---\\n(.*)$" + DOTALL 匹配
            - yaml.safe_load(frontmatter)
        """
        text = path.read_text(encoding="utf-8")
        match = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, flags=re.DOTALL)
        if not match:
            return {}, text.strip()

        metadata = yaml.safe_load(match.group(1)) or {}
        if not isinstance(metadata, dict):
            metadata = {}
        body = match.group(2).strip()
        return metadata, body

    def list_skills(self) -> list[dict[str, Any]]:
        """返回所有已发现的技能元信息。

        Returns:
            列表，每个元素形如：
                {"name": "greeting", "description": "...", "always": False}

        TODO: 从 self._cache 提取
        """
        return [
            {
                "name": name,
                "description": data.get("description", ""),
                "always": bool(data.get("always", False)),
            }
            for name, data in self._cache.items()
        ]

    def get_always_skills(self) -> list[str]:
        """返回所有 always=true 的技能名。

        Returns:
            名称列表。

        TODO: 过滤 self._cache
        """
        return [name for name, data in self._cache.items() if data.get("always")]

    def load_skill_body(self, name: str) -> str:
        """读取指定技能的正文（不含 frontmatter）。

        Args:
            name: 技能名。

        Returns:
            正文字符串；技能不存在返回 ""。

        TODO: self._cache[name]["body"]
        """
        return str(self._cache.get(name, {}).get("body", ""))

    def build_skills_block(self, active_skills: list[str]) -> str:
        """把若干技能正文拼成一段可嵌入 system prompt 的文本。

        Args:
            active_skills: 要挂载的技能名列表。

        Returns:
            拼好的字符串，例：
                "# Skills\\n\\n## greeting\\n<body>\\n\\n## farewell\\n<body>"

        TODO: 实现拼接，跳过不存在的 skill
        """
        seen: set[str] = set()
        sections: list[str] = []
        for name in active_skills:
            if name in seen:
                continue
            seen.add(name)
            body = self.load_skill_body(name).strip()
            if not body:
                continue
            sections.append(f"## {name}\n{body}")

        if not sections:
            return ""
        return "# Skills\n\n" + "\n\n".join(sections)
