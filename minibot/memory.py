"""memory.py — 长期记忆管理。

对应 Nanobot 的 agent/memory.py（精简版）。

MEMORY.md 的格式约定：
    # MEMORY.md

    ## user
    用户是后端工程师，偏好简短回答。

    ## project
    项目 X 的部署文档在 /docs/deploy.md。

    ## feedback
    禁止在生产环境跑 rm -rf。

每个 ## 一级 section 是独立分区，可单独读取/覆盖/追加。
append_entry 会自动在条目前加 ISO-8601 时间戳前缀，便于追溯。

并发安全：用 fcntl 对 MEMORY.md 整体加锁（POSIX 独占锁），同进程多线程亦串行化。
Windows 退化为 threading.Lock。
"""

from __future__ import annotations

import contextlib
import os
import re
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Iterator

# fcntl 仅在 POSIX 可用；Windows 退化为线程锁
try:
    import fcntl  # type: ignore[import-not-found]
    _HAS_FCNTL = True
except ImportError:  # pragma: no cover - Windows fallback
    _HAS_FCNTL = False

_PROCESS_LOCK = threading.RLock()


class MemoryStore:
    """MEMORY.md 的读写管理器。

    线程 + 跨进程串行化（fcntl 独占锁 + 进程内 RLock）。
    """

    DEFAULT_SECTIONS: tuple[str, ...] = ("user", "project", "feedback", "reference")

    def __init__(self, workspace: Path) -> None:
        workspace.mkdir(parents=True, exist_ok=True)
        self.memory_path = workspace / "MEMORY.md"
        # 已知合法的 section 名集合 — _parse_sections 用它过滤 ## 标题：
        # 在集合中的 ## X 视为 section 边界；不在集合中的 ## X 视为
        # section body 里的 Markdown 二级标题，不切分。
        #
        # 初始集合 = DEFAULT_SECTIONS，下面会再从磁盘上已有的 ## 标题里
        # 扫描出 write_section 创建过的自定义 section 加入集合，保证重启
        # 后旧的自定义 section 仍能被识别。
        self._known_sections: set[str] = set(self.DEFAULT_SECTIONS)
        if self.memory_path.exists():
            self._seed_known_sections_from_disk()
        else:
            self.write_all(self._render_sections([(name, "") for name in self.DEFAULT_SECTIONS]))

    def _seed_known_sections_from_disk(self) -> None:
        """启动时把磁盘上已有的 ## 标题登记到 _known_sections。

        这一步只在 __init__ 跑一次，保证旧 MEMORY.md 文件里通过
        write_section 创建的自定义 section（比如 "history"、"zeta"）
        在重启后仍能被识别。注意：如果旧文件因 pre-fix bug 已经被
        污染（body 里混入了 ## X 被误切成 section），重启时会把那个
        X 也当成 known section；该 case 需要人工清理。
        """
        content = self.memory_path.read_text(encoding="utf-8")
        for m in re.finditer(r"^##\s+(.+?)\s*$", content, flags=re.MULTILINE):
            self._known_sections.add(m.group(1).strip())

    # ---------- 文件锁 ----------

    @contextlib.contextmanager
    def _locked(self, exclusive: bool = True) -> Iterator[None]:
        """以独占（写）/共享（读）方式加锁。

        - POSIX：用 fcntl.flock 对 MEMORY.md.lock 文件加锁
        - 其他：退化为 threading.RLock
        """
        with _PROCESS_LOCK:
            if not _HAS_FCNTL:
                yield
                return
            lock_path = self.memory_path.with_suffix(".lock")
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            # 用 "a+" 打开，永不截断
            fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o644)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
                yield
            finally:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                finally:
                    os.close(fd)

    # ---------- 整文件 ----------

    def read_all(self) -> str:
        """读取整份 MEMORY.md 原文（文件不存在返回 ""）。"""
        with self._locked(exclusive=False):
            if not self.memory_path.exists():
                return ""
            return self.memory_path.read_text(encoding="utf-8")

    def read_memory(self) -> str:
        """Task 3 接口：alias of read_all。"""
        return self.read_all()

    def write_all(self, content: str) -> None:
        """覆盖整份 MEMORY.md（原子写 + 加锁）。"""
        with self._locked(exclusive=True):
            self.memory_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self.memory_path.with_suffix(".tmp")
            tmp_path.write_text(content, encoding="utf-8")
            os.replace(tmp_path, self.memory_path)

    # ---------- 按 section 操作 ----------

    def read_section(self, section: str) -> str:
        """读取指定 section 的文本（不含 "## 标题" 行）。section 不存在返回 ""。"""
        for name, body in self._parse_sections(self.read_all()):
            if name == section:
                return body.strip()
        return ""

    def get_section(self, section: str) -> str:
        """Task 3 接口：alias of read_section。"""
        return self.read_section(section)

    def write_section(self, section: str, content: str) -> None:
        """覆盖某个 section 的内容；section 不存在则追加到文件末尾。"""
        with self._locked(exclusive=True):
            # 先登记，让 _parse_sections 在「读出当前文件」时就把要写的
            # section 视为合法 — 否则首次 write_section("custom") 解析
            # 自己刚写过的 section 时会找不到。
            self._known_sections.add(section)
            sections = self._parse_sections(self._read_unlocked())
            normalized = content.strip()
            updated = False
            new_sections: list[tuple[str, str]] = []
            for name, body in sections:
                if name == section:
                    new_sections.append((section, normalized))
                    updated = True
                else:
                    new_sections.append((name, body))
            if not updated:
                new_sections.append((section, normalized))
            self._write_unlocked(self._render_sections(new_sections))

    def update_section(self, section: str, content: str) -> None:
        """Task 3 接口：alias of write_section。"""
        self.write_section(section, content)

    def append_section(self, section: str, line: str) -> None:
        """往某个 section 末尾追加一行（无时间戳）。读改写全程持锁。"""
        self._append_locked(section, line.strip())

    def append_entry(self, section: str, entry: str) -> None:
        """往某个 section 追加一条带 ISO-8601 时间戳的记录。读改写全程持锁。

        格式示例:
            - 2025-01-01T12:34:56  用户偏好简短回答。
        """
        timestamp = datetime.now().isoformat(timespec="seconds")
        line = f"- {timestamp}  {entry.strip()}"
        self._append_locked(section, line)

    def _append_locked(self, section: str, line: str) -> None:
        """读改写一体，在同一把独占锁下完成，避免并发丢数据。"""
        with self._locked(exclusive=True):
            self._known_sections.add(section)
            sections = self._parse_sections(self._read_unlocked())
            updated = False
            new_sections: list[tuple[str, str]] = []
            for name, body in sections:
                if name == section:
                    combined = f"{body}\n{line}".strip() if body else line
                    new_sections.append((section, combined))
                    updated = True
                else:
                    new_sections.append((name, body))
            if not updated:
                new_sections.append((section, line))
            self._write_unlocked(self._render_sections(new_sections))

    def list_sections(self) -> list[str]:
        """按文件中 ## 标题出现顺序返回 section 名列表。"""
        return [name for name, _ in self._parse_sections(self.read_all())]

    # ---------- 给 core.py 拼 system prompt 用 ----------

    def get_context_block(self) -> str:
        """返回要嵌入 system prompt 的记忆块。所有 section 都空时返回 ""。"""
        non_empty_sections = [
            (name, body.strip())
            for name, body in self._parse_sections(self.read_all())
            if body.strip()
        ]
        if not non_empty_sections:
            return ""

        lines = ["# Memory Summary", ""]
        for name, body in non_empty_sections:
            lines.append(f"## {name}")
            lines.append(body)
            lines.append("")
        return "\n".join(lines).strip()

    # ---------- 内部辅助（_unlocked 版本，调用方自己持锁） ----------

    def _read_unlocked(self) -> str:
        if not self.memory_path.exists():
            return ""
        return self.memory_path.read_text(encoding="utf-8")

    def _write_unlocked(self, content: str) -> None:
        self.memory_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.memory_path.with_suffix(".tmp")
        tmp_path.write_text(content, encoding="utf-8")
        os.replace(tmp_path, self.memory_path)

    def _parse_sections(self, content: str) -> list[tuple[str, str]]:
        """把 MEMORY.md 切成 (name, body) 列表。

        关键正确性约束：只有 self._known_sections 里的 ## X 才作为
        section 边界。不在白名单的 ## X 视为 body 里的 Markdown 二级
        标题，不切分。否则用户在 ## user 正文里写一个 `## 早安`，
        re.finditer 会把它当成新 section，下次 write_section("user")
        只覆盖第一段，剩下的 `## 早安\\n...` 留在文件里造成记忆错位。
        """
        # 匹配所有候选 ## 标题，再按 known whitelist 过滤
        matches = [
            m
            for m in re.finditer(r"^##\s+(.+?)\s*$", content, flags=re.MULTILINE)
            if m.group(1).strip() in self._known_sections
        ]
        if not matches:
            return []

        sections: list[tuple[str, str]] = []
        for index, match in enumerate(matches):
            start = match.end()
            end = matches[index + 1].start() if index + 1 < len(matches) else len(content)
            name = match.group(1).strip()
            body = content[start:end].strip("\n")
            sections.append((name, body))
        return sections

    @staticmethod
    def _render_sections(sections: list[tuple[str, str]]) -> str:
        lines = ["# MEMORY.md", ""]
        for name, body in sections:
            lines.append(f"## {name}")
            if body:
                lines.append(body.strip())
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"
