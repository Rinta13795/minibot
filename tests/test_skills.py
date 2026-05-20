"""tests/test_skills.py — 技能加载测试。"""

from __future__ import annotations

from pathlib import Path

from minibot.skills import SkillsLoader


def _write_skill(root: Path, name: str, body: str, *, always: bool = False, description: str = "") -> Path:
    """工具函数：在 root/<name>/SKILL.md 里写一个带 frontmatter 的技能文件。"""
    sub = root / name
    sub.mkdir(parents=True, exist_ok=True)
    skill = sub / "SKILL.md"
    skill.write_text(
        f"---\nname: {name}\ndescription: {description}\nalways: {str(always).lower()}\n---\n{body}",
        encoding="utf-8",
    )
    return skill


class TestSkillsLoader:
    def test_loads_single_skill(self, tmp_path: Path) -> None:
        _write_skill(tmp_path, "greeting", "# Greeting\n打招呼。", description="问候")
        loader = SkillsLoader(tmp_path)
        assert loader.has_skill("greeting")
        assert "打招呼" in loader.load_skill_body("greeting")

    def test_list_skills_returns_metadata(self, tmp_path: Path) -> None:
        _write_skill(tmp_path, "a", "body-a", description="A")
        _write_skill(tmp_path, "b", "body-b", always=True, description="B")
        loader = SkillsLoader(tmp_path)
        names = {s["name"] for s in loader.list_skills()}
        assert names == {"a", "b"}
        # always 应正确解析为 bool
        b = [s for s in loader.list_skills() if s["name"] == "b"][0]
        assert b["always"] is True
        assert b["description"] == "B"

    def test_get_always_skills(self, tmp_path: Path) -> None:
        _write_skill(tmp_path, "a", "x", always=False)
        _write_skill(tmp_path, "b", "y", always=True)
        loader = SkillsLoader(tmp_path)
        assert loader.get_always_skills() == ["b"]

    def test_build_skills_block_format(self, tmp_path: Path) -> None:
        """输出格式必须符合 Task 4 约定的中文模板。"""
        _write_skill(tmp_path, "greeting", "打招呼的正文。")
        loader = SkillsLoader(tmp_path)
        block = loader.build_skills_block(["greeting"])
        assert block.startswith("---\n以下是你可用的技能：")
        assert block.endswith("---")
        assert "### 技能：greeting" in block
        assert "打招呼的正文。" in block

    def test_build_skills_block_empty_for_no_active(self, tmp_path: Path) -> None:
        _write_skill(tmp_path, "a", "x")
        loader = SkillsLoader(tmp_path)
        assert loader.build_skills_block([]) == ""
        assert loader.build_skills_block(["nonexistent"]) == ""

    def test_build_skills_block_dedupes(self, tmp_path: Path) -> None:
        _write_skill(tmp_path, "a", "body-a")
        loader = SkillsLoader(tmp_path)
        block = loader.build_skills_block(["a", "a", "a"])
        assert block.count("### 技能：a") == 1

    def test_missing_directory_does_not_crash(self, tmp_path: Path) -> None:
        loader = SkillsLoader(tmp_path / "nonexistent")
        assert loader.list_skills() == []
        assert loader.build_skills_block(["whatever"]) == ""

    def test_multiple_skills_dirs(self, tmp_path: Path) -> None:
        dir_a = tmp_path / "dir_a"
        dir_b = tmp_path / "dir_b"
        _write_skill(dir_a, "skill1", "from-a")
        _write_skill(dir_b, "skill2", "from-b")
        loader = SkillsLoader([dir_a, dir_b])
        assert {s["name"] for s in loader.list_skills()} == {"skill1", "skill2"}

    def test_missing_frontmatter_falls_back_to_dirname(self, tmp_path: Path) -> None:
        """没有 frontmatter 时技能名取文件夹名，正文为整个文件。"""
        sub = tmp_path / "no_meta"
        sub.mkdir()
        (sub / "SKILL.md").write_text("just plain markdown body", encoding="utf-8")
        loader = SkillsLoader(tmp_path)
        assert loader.has_skill("no_meta")
        assert "just plain markdown" in loader.load_skill_body("no_meta")

    def test_invalid_yaml_is_skipped(self, tmp_path: Path, capsys) -> None:
        """frontmatter YAML 损坏的技能应被跳过并打印 warning，不影响其他技能。"""
        sub = tmp_path / "broken"
        sub.mkdir()
        (sub / "SKILL.md").write_text(
            "---\nname: broken\n  bad: : indent\n---\nbody",
            encoding="utf-8",
        )
        _write_skill(tmp_path, "good", "ok")
        loader = SkillsLoader(tmp_path)
        names = {s["name"] for s in loader.list_skills()}
        # good 必须加载，broken 必须被跳过
        assert "good" in names
        assert "broken" not in names
        captured = capsys.readouterr()
        assert "broken" in captured.err

    def test_loads_real_greeting_skill(self) -> None:
        """直接加载项目里真实的 skills/greeting，确保现网格式可用。"""
        skills_root = Path(__file__).resolve().parent.parent / "skills"
        if not (skills_root / "greeting" / "SKILL.md").exists():
            return  # 仓库里没这个 fixture 时跳过，避免 CI 在缺文件时挂
        loader = SkillsLoader(skills_root)
        assert loader.has_skill("greeting")
        body = loader.load_skill_body("greeting")
        assert "MiniBot" in body or "问候" in body
