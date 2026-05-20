"""tests/test_memory.py — MEMORY.md 读写测试骨架。"""

from __future__ import annotations

from pathlib import Path

import pytest

from minibot.memory import MemoryStore


class TestMemoryStore:
    def test_init_creates_empty_memory_file(self, tmp_path: Path) -> None:
        """首次构造时若 MEMORY.md 不存在，应自动创建带默认 section 的空文件。"""
        # TODO:
        # store = MemoryStore(workspace=tmp_path)
        # assert (tmp_path / "MEMORY.md").exists()
        # for s in store.DEFAULT_SECTIONS:
        #     assert f"## {s}" in store.read_all()
        pytest.skip("TODO")

    def test_read_section_returns_only_section_body(self, tmp_path: Path) -> None:
        """read_section 应只返回目标 section 的正文，不含其他 section 和标题。"""
        # TODO:
        # 准备一个含两个 section 的 MEMORY.md
        # 断言 read_section("user") 不含 "## project" 的内容
        pytest.skip("TODO")

    def test_write_section_replaces_existing(self, tmp_path: Path) -> None:
        """write_section 已存在的 section 时应原地替换，不影响其他 section。"""
        # TODO:
        # 写入 project section 新内容后，user section 应保持原样
        pytest.skip("TODO")

    def test_write_section_appends_when_missing(self, tmp_path: Path) -> None:
        """写入不存在的 section 时应追加到文件末尾，且 list_sections 能列出。"""
        # TODO:
        # store.write_section("custom_topic", "x")
        # assert "custom_topic" in store.list_sections()
        pytest.skip("TODO")

    def test_append_section_preserves_existing_lines(self, tmp_path: Path) -> None:
        """append_section 应保留原有内容，只在末尾加一行。"""
        # TODO:
        # 先 write_section 一段，再 append_section 一行
        # 断言原内容 + 新行都在
        pytest.skip("TODO")

    def test_atomic_write_no_partial_on_crash(self, tmp_path: Path, monkeypatch) -> None:
        """模拟写 tmp 后 rename 前崩溃，原文件应保持完整（原子写保障）。"""
        # TODO:
        # monkeypatch os.replace 抛异常
        # 断言原文件内容未变
        pytest.skip("TODO")

    def test_get_context_block_returns_empty_when_no_content(self, tmp_path: Path) -> None:
        """所有 section 都为空时，get_context_block 应返回空串，避免污染 prompt。"""
        # TODO:
        # store = MemoryStore(workspace=tmp_path)
        # assert store.get_context_block() == ""
        pytest.skip("TODO")

    def test_list_sections_order_matches_file(self, tmp_path: Path) -> None:
        """list_sections 的顺序应和文件中 ## 标题出现的顺序一致。"""
        # TODO
        pytest.skip("TODO")
