"""memory.py — 长期记忆管理。

对应 Nanobot 的 agent/memory.py（精简版，去掉了 Consolidator / Dream）。

MEMORY.md 的格式约定（受 Claude Code 的 memory 启发）：
    # MEMORY.md

    ## user
    用户是后端工程师，偏好简短回答。

    ## project
    项目 X 的部署文档在 /docs/deploy.md。

    ## feedback
    禁止在生产环境跑 rm -rf。

每个 ## section 是一个独立分区，可单独读取/覆盖/追加，避免整文件重写造成丢失。
"""

from __future__ import annotations

from pathlib import Path


class MemoryStore:
    """MEMORY.md 的读写管理器。

    线程不安全（学习项目，先不上锁；Nanobot 用了 asyncio.Lock 做 per-session 串行）。
    """

    DEFAULT_SECTIONS: tuple[str, ...] = ("user", "project", "feedback", "reference")

    def __init__(self, workspace: Path) -> None:
        """初始化记忆存储。

        Args:
            workspace: 工作目录，MEMORY.md 位于 workspace / "MEMORY.md"。

        TODO:
            - self.memory_path = workspace / "MEMORY.md"
            - 若不存在，创建空文件并写入默认 section 框架
        """
        raise NotImplementedError("TODO: __init__")

    # ---------- 整文件 ----------

    def read_all(self) -> str:
        """读取整份 MEMORY.md 原文。

        Returns:
            文件内容字符串；文件不存在返回 ""。

        TODO: self.memory_path.read_text(encoding="utf-8") 兜底
        """
        raise NotImplementedError("TODO: read_all")

    def write_all(self, content: str) -> None:
        """覆盖整份 MEMORY.md（原子写）。

        Args:
            content: 新的完整内容。

        TODO:
            - 写 tmp 文件 → os.replace 到目标路径
            - 防止半截写入污染记忆
        """
        raise NotImplementedError("TODO: write_all")

    # ---------- 按 section 操作 ----------

    def read_section(self, section: str) -> str:
        """读取指定 section 的文本（不含 "## 标题" 行）。

        Args:
            section: section 名，如 "user" / "project"。

        Returns:
            该 section 下的所有文本（去首尾空行）；section 不存在返回 ""。

        TODO:
            - 用正则切分 ## 标题
            - 取目标 section 到下一个 ## 之间的内容
        """
        raise NotImplementedError("TODO: read_section")

    def write_section(self, section: str, content: str) -> None:
        """覆盖某个 section 的内容；section 不存在则追加到文件末尾。

        Args:
            section: section 名。
            content: 新内容（不带 "## 标题"，会自动加上）。

        TODO:
            - read_all() → 解析所有 section
            - 替换或追加目标 section
            - write_all()
        """
        raise NotImplementedError("TODO: write_section")

    def append_section(self, section: str, line: str) -> None:
        """往某个 section 末尾追加一行。

        Args:
            section: section 名。
            line: 要追加的内容（不必带换行符）。

        典型用法：每次用户给反馈时，append 一条到 feedback section。

        TODO: read_section → 拼接 → write_section
        """
        raise NotImplementedError("TODO: append_section")

    def list_sections(self) -> list[str]:
        """列出当前文件里所有 section 名。

        Returns:
            section 名列表，按出现顺序。

        TODO: 正则提取所有 "## name" 标题
        """
        raise NotImplementedError("TODO: list_sections")

    # ---------- 给 core.py 拼 system prompt 用 ----------

    def get_context_block(self) -> str:
        """返回要嵌入 system prompt 的记忆块。

        通常就是 read_all() 的结果，外加一个说明性标题，例：
            "# Persistent memory\\n\\n<MEMORY.md 内容>"

        Returns:
            可直接拼进 system prompt 的字符串；若 MEMORY 为空返回 ""。

        TODO: 实现包装
        """
        raise NotImplementedError("TODO: get_context_block")
