"""tests/test_memory.py — MEMORY.md 读写测试。"""

from __future__ import annotations

import re
import threading
from pathlib import Path

import pytest

from minibot.memory import MemoryStore


class TestMemoryStore:
    def test_init_creates_empty_memory_file(self, tmp_path: Path) -> None:
        store = MemoryStore(workspace=tmp_path)
        assert (tmp_path / "MEMORY.md").exists()
        for s in store.DEFAULT_SECTIONS:
            assert f"## {s}" in store.read_all()

    def test_read_section_returns_only_section_body(self, tmp_path: Path) -> None:
        store = MemoryStore(workspace=tmp_path)
        store.write_section("user", "alice")
        store.write_section("project", "minibot")
        assert store.read_section("user") == "alice"
        assert "minibot" not in store.read_section("user")

    def test_write_section_replaces_existing(self, tmp_path: Path) -> None:
        store = MemoryStore(workspace=tmp_path)
        store.write_section("user", "alice")
        store.write_section("project", "minibot")
        store.write_section("project", "minibot v2")
        assert store.read_section("user") == "alice"
        assert store.read_section("project") == "minibot v2"

    def test_write_section_appends_when_missing(self, tmp_path: Path) -> None:
        store = MemoryStore(workspace=tmp_path)
        store.write_section("custom_topic", "x")
        assert "custom_topic" in store.list_sections()
        assert store.read_section("custom_topic") == "x"

    def test_append_section_preserves_existing_lines(self, tmp_path: Path) -> None:
        store = MemoryStore(workspace=tmp_path)
        store.write_section("project", "line1")
        store.append_section("project", "line2")
        body = store.read_section("project")
        assert "line1" in body and "line2" in body

    def test_append_entry_adds_timestamp(self, tmp_path: Path) -> None:
        store = MemoryStore(workspace=tmp_path)
        store.append_entry("feedback", "用户偏好简短回答")
        body = store.read_section("feedback")
        # ISO-8601 like 2025-01-01T12:34:56
        assert re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", body)
        assert "用户偏好简短回答" in body

    def test_get_context_block_returns_empty_when_no_content(self, tmp_path: Path) -> None:
        store = MemoryStore(workspace=tmp_path)
        assert store.get_context_block() == ""

    def test_get_context_block_includes_non_empty_sections(self, tmp_path: Path) -> None:
        store = MemoryStore(workspace=tmp_path)
        store.write_section("user", "alice")
        block = store.get_context_block()
        assert "alice" in block
        assert "## user" in block

    def test_list_sections_order_matches_file(self, tmp_path: Path) -> None:
        store = MemoryStore(workspace=tmp_path)
        store.write_section("user", "u")
        store.write_section("project", "p")
        store.write_section("zeta", "z")
        sections = store.list_sections()
        # zeta 是新追加，应在末尾
        assert sections.index("zeta") > sections.index("project")

    def test_empty_file_does_not_crash(self, tmp_path: Path) -> None:
        memory = tmp_path / "MEMORY.md"
        memory.write_text("", encoding="utf-8")
        store = MemoryStore(workspace=tmp_path)
        # 空文件 — read_section 应安全返回空串
        assert store.read_section("user") == ""
        assert store.list_sections() == []

    def test_section_with_chinese_name(self, tmp_path: Path) -> None:
        store = MemoryStore(workspace=tmp_path)
        store.write_section("用户偏好", "中文 section 名")
        assert "用户偏好" in store.list_sections()
        assert store.read_section("用户偏好") == "中文 section 名"

    def test_section_name_with_spaces(self, tmp_path: Path) -> None:
        store = MemoryStore(workspace=tmp_path)
        store.write_section("my topic", "body")
        assert "my topic" in store.list_sections()
        assert store.read_section("my topic") == "body"

    def test_concurrent_writes_serialized(self, tmp_path: Path) -> None:
        """20 个线程并发 append_entry，最终应留下 20 条记录（无丢失）。"""
        store = MemoryStore(workspace=tmp_path)
        store.write_section("feedback", "")

        def worker(i: int) -> None:
            store.append_entry("feedback", f"entry-{i}")

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        body = store.read_section("feedback")
        for i in range(20):
            assert f"entry-{i}" in body, f"lost entry-{i}"

    def test_aliases(self, tmp_path: Path) -> None:
        """Task 3 接口（read_memory / get_section / update_section）应与原接口等价。"""
        store = MemoryStore(workspace=tmp_path)
        store.update_section("user", "alice")
        assert store.get_section("user") == "alice"
        assert "alice" in store.read_memory()
