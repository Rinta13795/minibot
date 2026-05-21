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
import logging
import os
import re
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Iterator

_log = logging.getLogger(__name__)

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

    # 持久化白名单的元数据注释。写文件时由 _render_sections 自动生成；
    # 读文件时由 _read_metadata_into_known_sections 提取。绝不通过扫描
    # body 内的 ## X 来推断白名单，因为那样会把 user 在 body 里写的
    # Markdown 二级标题（例如 "## 早安风格"）当成合法 section 加入集合，
    # 后续 read 就会把它切回 section，重新引入 #4 的拆分 bug。
    _SECTIONS_META_PREFIX = "<!-- minibot-sections: "
    _SECTIONS_META_SUFFIX = " -->"

    def __init__(self, workspace: Path) -> None:
        workspace.mkdir(parents=True, exist_ok=True)
        self.memory_path = workspace / "MEMORY.md"
        # 已知合法的 section 名集合 — _parse_sections 用它过滤 ## 标题：
        # 在集合中的 ## X 视为 section 边界；不在集合中的 ## X 视为
        # section body 里的 Markdown 二级标题，不切分。
        self._known_sections: set[str] = set(self.DEFAULT_SECTIONS)
        if self.memory_path.exists():
            self._initialize_known_sections_from_disk()
        else:
            # 新建空骨架（包含元数据行）
            self.write_all(self._render_sections(
                [(name, "") for name in self.DEFAULT_SECTIONS]
            ))

    def _initialize_known_sections_from_disk(self) -> None:
        """启动时恢复 _known_sections。

        优先读元数据注释行——minibot 写入的权威源；它只包含通过
        write_section / append_entry 显式创建的 section 名。不会被
        body 里的 Markdown 二级标题污染。

        没有元数据时（旧 MEMORY.md / 外部手编辑），有两个互斥的失败模式：
          1. 扫描所有 ## X 加入白名单 → pre-fix bug 制造的 body-injected
             subheading 被错误"扶正"为合法 section，bug 复现。
          2. 只信任 DEFAULT_SECTIONS → 用户合法的自定义 section（如 zeta）
             会静默并入前一个默认 section 的 body。

        权衡：选 (2) 杜绝 bug 复发，但**必须显式告知用户**——
          - 落盘备份原文件到 MEMORY.md.legacy-backup
          - 通过 logging.warning 列出被降级的 section 名
          - 用户可对照备份决定是否在元数据注释里手动恢复 section
        """
        content = self.memory_path.read_text(encoding="utf-8")
        if self._read_metadata_into_known_sections(content):
            return

        # 扫描非默认 header 并告警 + 备份
        non_default_headers = sorted({
            m.group(1).strip()
            for m in re.finditer(r"^##\s+(.+?)\s*$", content, flags=re.MULTILINE)
            if m.group(1).strip() not in self.DEFAULT_SECTIONS
        })
        if non_default_headers:
            self._write_legacy_backup_and_warn(content, non_default_headers)

        # 保守解析（默认白名单）后立即写回新格式
        sections = self._parse_sections(content)
        self.write_all(self._render_sections(sections))

    def _write_legacy_backup_and_warn(
        self, content: str, demoted: list[str]
    ) -> None:
        """把原文件备份到 MEMORY.md.legacy-backup 并发 warning。

        备份是 best-effort：写失败不能阻止 MemoryStore 启动，但要继续
        发出 warning 让用户察觉。
        """
        backup_path = self.memory_path.with_name(
            self.memory_path.name + ".legacy-backup"
        )
        try:
            if not backup_path.exists():
                backup_path.write_text(content, encoding="utf-8")
            backup_note = f"backup at {backup_path.name}"
        except Exception as exc:
            backup_note = f"backup FAILED ({exc})"

        msg = (
            "Legacy MEMORY.md detected (no minibot-sections metadata). "
            f"Demoting non-default headers to body content: {demoted}. "
            f"{backup_note}. If any of these were legitimate custom sections, "
            "restore them by adding their names to the "
            f"'{self._SECTIONS_META_PREFIX.strip()}' metadata line."
        )
        _log.warning(msg)
        # 同时打到 stderr — logging 在没人配 handler 时可能不可见
        sys.stderr.write(f"[minibot.memory] {msg}\n")

    def _read_metadata_into_known_sections(self, content: str) -> bool:
        """从文件内容里找元数据行；找到则把名字加入 _known_sections，返回 True。"""
        prefix = self._SECTIONS_META_PREFIX
        suffix = self._SECTIONS_META_SUFFIX
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith(prefix) and stripped.endswith(suffix):
                names_csv = stripped[len(prefix): -len(suffix)]
                for name in names_csv.split(","):
                    name = name.strip()
                    if name:
                        self._known_sections.add(name)
                return True
        return False

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

    def _render_sections(self, sections: list[tuple[str, str]]) -> str:
        # 把当前白名单写进元数据注释行作为权威源。已写入磁盘的所有
        # 合法 section 名（含 DEFAULT_SECTIONS + 自定义）都在这里，重启
        # 读这条注释就能恢复，完全不依赖扫描 ## X 标题。
        meta_csv = ",".join(sorted(self._known_sections))
        meta_line = f"{self._SECTIONS_META_PREFIX}{meta_csv}{self._SECTIONS_META_SUFFIX}"
        lines = ["# MEMORY.md", "", meta_line, ""]
        for name, body in sections:
            lines.append(f"## {name}")
            if body:
                lines.append(body.strip())
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"
