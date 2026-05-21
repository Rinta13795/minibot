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

    def test_body_with_unknown_h2_heading_is_not_split(self, tmp_path: Path) -> None:
        """body 里出现 `## 小标题`（非已知 section 名）不能被误切成新 section。"""
        store = MemoryStore(workspace=tmp_path)
        body_with_h2 = (
            "我喜欢的格式如下：\n"
            "## 早安风格\n"
            "你好呀\n"
            "## 晚安风格\n"
            "晚安"
        )
        store.write_section("user", body_with_h2)

        # 写入后 read 应拿回完整 body（含两个 ## 子标题）
        assert store.read_section("user") == body_with_h2
        # list_sections 不应把 "早安风格" / "晚安风格" 当成独立 section
        sections = store.list_sections()
        assert "早安风格" not in sections
        assert "晚安风格" not in sections

    def test_body_h2_survives_subsequent_write_to_other_section(
        self, tmp_path: Path
    ) -> None:
        """body 里有 `## 小标题` 时，写其他 section 不能把它撕掉。

        这是代码审查指出的核心 race window：原实现下，write_section("user")
        在解析阶段把 body 内的 ## X 当 section 切走，再次写时只覆盖第一段，
        造成「## X\\n...」内容残留为孤儿 section。
        """
        store = MemoryStore(workspace=tmp_path)
        store.write_section("user", "前缀\n## 中间标题\n后缀")
        # 再写另一个合法 section
        store.write_section("project", "p")
        # 再写 user，验证原来的 ## 中间标题 还在
        store.write_section("user", "前缀\n## 中间标题\n后缀")

        assert store.read_section("user") == "前缀\n## 中间标题\n后缀"
        assert store.read_section("project") == "p"
        assert "中间标题" not in store.list_sections()

    def test_h2_matching_known_section_still_splits(self, tmp_path: Path) -> None:
        """边角情况：body 里写一个文本恰好等于已知 section 名（如 ## project）。

        这种情况下白名单方案仍会切分——这是已知的不可避免的歧义，必须由调
        用方避免（或对内容做转义）。本测试只是把当前预期行为锁定下来，便于
        将来如改成更稳健的结构化格式时及时发现。
        """
        store = MemoryStore(workspace=tmp_path)
        store.write_section("user", "起头\n## project\n冒充")
        # 当前行为：## project 会被识别为 section 边界
        assert "## project" not in store.read_section("user")

    def test_known_section_seeded_from_disk_on_reopen(self, tmp_path: Path) -> None:
        """write_section("zeta") 写入后，新实例重新打开 MEMORY.md，
        仍能识别 zeta 是合法 section。"""
        store = MemoryStore(workspace=tmp_path)
        store.write_section("zeta", "z-content")
        # 模拟进程重启
        store2 = MemoryStore(workspace=tmp_path)
        assert "zeta" in store2.list_sections()
        assert store2.read_section("zeta") == "z-content"

    def test_body_h2_survives_process_restart(self, tmp_path: Path) -> None:
        """关键回归：body 里写了 `## 早安风格` 后进程重启，重新加载
        MEMORY.md 也不能把 `## 早安风格` 当成 section 切走。

        早期的 _seed_known_sections_from_disk 实现会扫描所有 ## 标题
        并加入白名单，导致 body 里的二级标题被错误地视为合法 section
        →重启后 read_section("user") 只拿到 subheading 之前的内容。
        正确实现下，权威白名单只来自元数据注释，不依赖磁盘上的 ## X。
        """
        body = "我的写作偏好：\n## 早安风格\n阳光\n## 晚安风格\n安静"
        store = MemoryStore(workspace=tmp_path)
        store.write_section("user", body)
        # 第一次 read 已经在 PR 中验证过
        assert store.read_section("user") == body

        # 模拟进程重启
        store2 = MemoryStore(workspace=tmp_path)
        assert "早安风格" not in store2.list_sections()
        assert "晚安风格" not in store2.list_sections()
        assert store2.read_section("user") == body

    def test_legacy_file_migration_writes_metadata_with_defaults_only(
        self, tmp_path: Path
    ) -> None:
        """旧文件无元数据时，迁移采取保守策略：只信任 DEFAULT_SECTIONS。

        这是有意为之——不扫描所有 ## X 加入白名单可避免 pre-fix bug
        制造的 body-injected subheadings 被错误地"扶正"为 section。
        代价是旧文件里自定义 section（zeta）会变成前一个默认 section
        的 body 内容。
        """
        memory_path = tmp_path / "MEMORY.md"
        memory_path.write_text(
            "# MEMORY.md\n\n## user\nalice\n\n## zeta\nz\n",
            encoding="utf-8",
        )
        store = MemoryStore(workspace=tmp_path)
        # zeta 不再被视为合法 section
        assert "zeta" not in store.list_sections()
        # zeta 的内容被并入 user 的 body
        assert "## zeta" in store.read_section("user")
        assert "z" in store.read_section("user")
        # 文件已重写为带元数据的新格式
        new_content = memory_path.read_text(encoding="utf-8")
        assert MemoryStore._SECTIONS_META_PREFIX in new_content

    def test_legacy_file_with_body_injection_does_not_promote_to_section(
        self, tmp_path: Path
    ) -> None:
        """关键回归：legacy 文件里 body 已含 `## 早安` 时（无论是 pre-fix
        bug 制造的还是手编辑的），打开后**不能**把它当成新 section。
        这正是 Codex review 指出的「legacy migration reintroduces the
        reviewed split bug」场景。"""
        memory_path = tmp_path / "MEMORY.md"
        memory_path.write_text(
            "# MEMORY.md\n\n"
            "## user\n"
            "我的偏好：\n"
            "## 早安风格\n"
            "你好\n\n"
            "## project\n"
            "p\n",
            encoding="utf-8",
        )
        store = MemoryStore(workspace=tmp_path)
        # 早安风格 不应进入白名单
        assert "早安风格" not in store.list_sections()
        # project 仍是合法 section
        assert "project" in store.list_sections()
        # 元数据写入后再启动，行为仍稳定
        store2 = MemoryStore(workspace=tmp_path)
        assert "早安风格" not in store2.list_sections()
