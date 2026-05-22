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
    - 启动时扫描所有配置的 skills_dirs（支持多目录），收集每个子目录下的 SKILL.md。
    - 解析 frontmatter（YAML），缺失或损坏的 SKILL.md 跳过并打印 warning，不影响其他技能。
    - always=true 的技能始终激活；其他技能按需挂载。
    - 输出格式遵循 Task 4 约定：用 `---` 分隔的中文模板。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any, Iterable

import yaml


def find_writable_skill_dirs(
    skills_dirs: Iterable[Path],
    writable_paths: Iterable[Path],
) -> list[Path]:
    """返回与任一可写路径重叠的 skills_dir（双向嵌套都算）。

    安全背景：SKILL.md 的正文会被拼进 system prompt，always=true 的技能
    甚至每轮都挂载，其优先级高于普通用户消息。如果某个 skills_dir 落在
    write_file.allowed_paths 之下（或反之），LLM 就能通过 write_file 写入
    一个恶意 SKILL.md，把任意指令稳定注入 system prompt——典型的提权 /
    间接 prompt injection 路径。

    判定规则（任一成立即视为重叠）：
        - skills_dir == writable_path
        - skills_dir 在 writable_path 之下（LLM 能直接往技能目录写文件）
        - writable_path 在 skills_dir 之下（LLM 能往技能子目录写，重启后
          被扫描成新技能）

    路径都先 resolve，规避 symlink / 相对路径差异。
    """
    writable_resolved: list[Path] = []
    for wp in writable_paths:
        try:
            writable_resolved.append(wp.resolve())
        except OSError:
            continue

    overlapping: list[Path] = []
    for sd in skills_dirs:
        try:
            sd_r = sd.resolve()
        except OSError:
            continue
        for wp in writable_resolved:
            if sd_r == wp or sd_r.is_relative_to(wp) or wp.is_relative_to(sd_r):
                overlapping.append(sd_r)
                break
    return overlapping


class SkillsLoader:
    """扫描 skills 目录，按需把 SKILL.md 注入 system prompt。"""

    def __init__(self, skills_dirs: Path | Iterable[Path]) -> None:
        """初始化加载器。

        Args:
            skills_dirs: 单个 Path 或 Path 列表。多目录会按顺序扫描，重名以先扫到的为准。
        """
        if isinstance(skills_dirs, Path):
            self.skills_dirs: list[Path] = [skills_dirs]
        else:
            self.skills_dirs = list(skills_dirs)
        self._cache: dict[str, dict[str, Any]] = {}
        self._scan()

    @property
    def skills_dir(self) -> Path:
        """向后兼容：返回第一个目录（如有），否则 Path(".")。"""
        return self.skills_dirs[0] if self.skills_dirs else Path(".")

    def _scan(self) -> None:
        """遍历所有 skills_dirs 下的子目录，解析每个 SKILL.md 并缓存。"""
        self._cache = {}
        for root in self.skills_dirs:
            if not root.exists() or not root.is_dir():
                continue
            for sub in sorted(root.iterdir()):
                skill_md = sub / "SKILL.md"
                if not sub.is_dir() or not skill_md.exists():
                    continue
                try:
                    meta, body = self._parse_skill_md(skill_md)
                except Exception as exc:
                    print(
                        f"[skills] skipping {skill_md}: {exc}",
                        file=sys.stderr,
                    )
                    continue
                # 技能名优先取 frontmatter 的 name，缺失时回退到文件夹名
                name = str(meta.get("name") or sub.name).strip() or sub.name
                if name in self._cache:
                    # 重名：保留先扫到的，跳过后续的
                    print(
                        f"[skills] duplicate skill name '{name}' from {skill_md}, ignoring",
                        file=sys.stderr,
                    )
                    continue
                self._cache[name] = {
                    "name": name,
                    "description": str(meta.get("description", "")),
                    "always": bool(meta.get("always", False)),
                    "body": body.strip(),
                    "path": skill_md,
                }

    @staticmethod
    def _parse_skill_md(path: Path) -> tuple[dict[str, Any], str]:
        """切分 SKILL.md 的 YAML frontmatter 和正文。

        frontmatter 缺失或格式不对时返回 ({}, 全文)，由调用方决定如何处理。
        """
        text = path.read_text(encoding="utf-8")
        match = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, flags=re.DOTALL)
        if not match:
            return {}, text.strip()

        try:
            metadata = yaml.safe_load(match.group(1)) or {}
        except yaml.YAMLError as exc:
            raise ValueError(f"invalid YAML frontmatter: {exc}") from exc
        if not isinstance(metadata, dict):
            raise ValueError(f"frontmatter must be a mapping, got {type(metadata).__name__}")
        body = match.group(2).strip()
        return metadata, body

    # ---------- 对外 API ----------

    def list_skills(self) -> list[dict[str, Any]]:
        """返回所有已发现的技能元信息。"""
        return [
            {
                "name": data["name"],
                "description": data["description"],
                "always": data["always"],
            }
            for data in self._cache.values()
        ]

    def get_always_skills(self) -> list[str]:
        """返回所有 always=true 的技能名。"""
        return [name for name, data in self._cache.items() if data.get("always")]

    def load_skill_body(self, name: str) -> str:
        """读取指定技能的正文（不含 frontmatter）。"""
        return str(self._cache.get(name, {}).get("body", ""))

    def has_skill(self, name: str) -> bool:
        return name in self._cache

    def build_skills_block(self, active_skills: list[str]) -> str:
        """把若干技能正文拼成可嵌入 system prompt 的文本（Task 4 格式）。

        输出形如：
            ---
            以下是你可用的技能：

            ### 技能：greeting
            <SKILL.md 正文>

            ### 技能：farewell
            <SKILL.md 正文>
            ---
        """
        seen: set[str] = set()
        sections: list[str] = []
        for name in active_skills:
            if name in seen or not self.has_skill(name):
                continue
            seen.add(name)
            body = self.load_skill_body(name).strip()
            if not body:
                continue
            sections.append(f"### 技能：{name}\n{body}")

        if not sections:
            return ""
        return "---\n以下是你可用的技能：\n\n" + "\n\n".join(sections) + "\n---"
