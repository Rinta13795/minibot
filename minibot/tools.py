"""tools.py — 内置工具集合。

对应 Nanobot 的 agent/tools/ 目录，但极简：只实现三个最基础的工具：
    - ExecTool        执行 shell 命令（受 cmd_whitelist 限制）
    - ReadFileTool    读文件（受 allowed_paths 限制）
    - WriteFileTool   写文件（受 allowed_paths 限制 + 禁止覆盖系统目录）

安全设计原则：
    1. 白名单优先：默认拒绝，显式允许才放行。
    2. 路径必须 resolve() 后再判断，防止 ../../etc/passwd 绕过。
    3. 命令做最简单的「可执行文件名」白名单，复杂参数留给上层 prompt 约束。
    4. 所有限制都在 Tool 实例化时通过构造参数注入，便于单测和不同场景复用。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


class Tool(ABC):
    """工具基类（对应 Nanobot agent/tools/base.py 的 Tool ABC）。

    每个工具必须暴露三个属性：name / description / parameters（JSON Schema），
    并实现 execute()。core.py 在 tool_use 循环里按 name 找到工具并调用。
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """工具名（LLM 会通过这个名字调用），如 "exec"。"""
        raise NotImplementedError

    @property
    @abstractmethod
    def description(self) -> str:
        """给 LLM 看的简介，会进 prompt，要简短准确。"""
        raise NotImplementedError

    @property
    @abstractmethod
    def parameters(self) -> dict[str, Any]:
        """参数的 JSON Schema，例：{"type": "object", "properties": {...}}。"""
        raise NotImplementedError

    @abstractmethod
    def execute(self, **kwargs: Any) -> str:
        """实际执行工具的入口。

        Returns:
            字符串结果（会被回喂给 LLM 当 tool_result）；
            失败时返回以 "Error:" 开头的错误信息，不要抛异常。
        """
        raise NotImplementedError

    def to_schema(self) -> dict[str, Any]:
        """转成 Anthropic tools 参数格式。

        Returns:
            {"name": ..., "description": ..., "input_schema": ...}

        TODO: 实现序列化
        """
        raise NotImplementedError("TODO: to_schema")


# ============================================================
# 三个内置工具
# ============================================================


class ExecTool(Tool):
    """执行 shell 命令，受白名单约束。

    安全策略：
        - cmd_whitelist 限制可执行文件名（如 ["ls", "cat", "python3"]）。
        - 解析 shlex.split 后取第 0 个 token 做白名单匹配。
        - 设置 timeout，防止子进程挂死。
        - 不开 shell=True，避免 shell 注入。
    """

    def __init__(
        self,
        cmd_whitelist: list[str],
        workspace: Path,
        timeout_sec: int = 30,
    ) -> None:
        """初始化 exec 工具。

        Args:
            cmd_whitelist: 允许的命令名列表，空列表表示禁用 exec 工具。
            workspace: 子进程的 cwd。
            timeout_sec: 单次执行超时秒数。

        TODO: 保存参数；校验 workspace 存在
        """
        raise NotImplementedError("TODO: ExecTool.__init__")

    @property
    def name(self) -> str:
        return "exec"

    @property
    def description(self) -> str:
        return "Run a shell command from the whitelist and return stdout/stderr."

    @property
    def parameters(self) -> dict[str, Any]:
        """TODO: 返回 {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}"""
        raise NotImplementedError("TODO: parameters")

    def execute(self, **kwargs: Any) -> str:
        """执行命令。

        步骤：
            1. shlex.split(command) → tokens
            2. tokens[0] 必须在 cmd_whitelist 里
            3. subprocess.run(tokens, cwd=workspace, timeout=timeout_sec, capture_output=True)
            4. 拼接 stdout + stderr 返回

        Args:
            command: 完整命令字符串，如 "ls -la /tmp"。

        Returns:
            "stdout: ...\\nstderr: ...\\nreturncode: 0"

        TODO: 实现命令执行 + 异常捕获
        """
        raise NotImplementedError("TODO: ExecTool.execute")

    def _is_whitelisted(self, command: str) -> bool:
        """判断命令是否在白名单内。

        Args:
            command: 用户提交的完整命令字符串。

        Returns:
            True 表示允许执行。

        TODO: shlex.split → tokens[0] in self.cmd_whitelist
        """
        raise NotImplementedError("TODO: _is_whitelisted")


class ReadFileTool(Tool):
    """读取文件内容，受 allowed_paths 限制。"""

    def __init__(self, allowed_paths: list[Path], max_bytes: int = 1_000_000) -> None:
        """初始化读文件工具。

        Args:
            allowed_paths: 允许读取的目录白名单（绝对路径），文件必须在某个白名单目录下。
            max_bytes: 单文件最大读取字节数，超出截断。

        TODO: resolve() 所有 allowed_paths，存为 set
        """
        raise NotImplementedError("TODO: ReadFileTool.__init__")

    @property
    def name(self) -> str:
        return "read_file"

    @property
    def description(self) -> str:
        return "Read a file from an allowed directory and return its text content."

    @property
    def parameters(self) -> dict[str, Any]:
        """TODO: properties={"path": {"type": "string"}}, required=["path"]"""
        raise NotImplementedError("TODO: parameters")

    def execute(self, **kwargs: Any) -> str:
        """读取并返回文件内容。

        步骤：
            1. Path(path).resolve()
            2. _check_path_allowed() → 若不允许，返回 "Error: path not allowed"
            3. open(path).read()[:max_bytes]

        TODO: 实现读文件 + 截断 + UnicodeDecodeError 兜底
        """
        raise NotImplementedError("TODO: ReadFileTool.execute")

    def _check_path_allowed(self, target: Path) -> bool:
        """判断目标路径是否落在某个 allowed_paths 子树内。

        关键：必须用 resolved 之后的绝对路径比较，防 symlink/.. 绕过。

        TODO: 用 Path.is_relative_to() 或手动 commonpath 检查
        """
        raise NotImplementedError("TODO: _check_path_allowed")


class WriteFileTool(Tool):
    """写文件，受 allowed_paths 限制；不允许覆盖系统目录。"""

    def __init__(
        self,
        allowed_paths: list[Path],
        forbidden_extensions: list[str] | None = None,
    ) -> None:
        """初始化写文件工具。

        Args:
            allowed_paths: 允许写入的目录白名单。
            forbidden_extensions: 黑名单后缀，如 [".sh", ".py"] 防写脚本；
                                  None 表示不做后缀限制。

        TODO: 保存参数；resolve()
        """
        raise NotImplementedError("TODO: WriteFileTool.__init__")

    @property
    def name(self) -> str:
        return "write_file"

    @property
    def description(self) -> str:
        return "Write text content to a file inside an allowed directory."

    @property
    def parameters(self) -> dict[str, Any]:
        """TODO: properties={"path","content"} required=["path","content"]"""
        raise NotImplementedError("TODO: parameters")

    def execute(self, **kwargs: Any) -> str:
        """把 content 写入 path。

        步骤：
            1. 路径白名单检查
            2. 扩展名黑名单检查
            3. 原子写：先写 tmp，再 os.replace（参考 Nanobot session_manager.save）

        TODO: 实现原子写
        """
        raise NotImplementedError("TODO: WriteFileTool.execute")


# ============================================================
# 工具注册表
# ============================================================


class ToolRegistry:
    """工具注册表，core.py 通过它分发 tool_use 调用。

    对应 Nanobot 的 agent/tools/registry.py，但只保留最核心功能。
    """

    def __init__(self) -> None:
        """初始化空注册表。

        TODO: self._tools: dict[str, Tool] = {}
        """
        raise NotImplementedError("TODO: __init__")

    def register(self, tool: Tool) -> None:
        """注册一个工具。

        TODO: self._tools[tool.name] = tool
        """
        raise NotImplementedError("TODO: register")

    def get_schemas(self) -> list[dict[str, Any]]:
        """返回所有工具的 Anthropic schemas 列表，传给 messages.create(tools=...)。

        TODO: [t.to_schema() for t in self._tools.values()]
        """
        raise NotImplementedError("TODO: get_schemas")

    def execute(self, name: str, params: dict[str, Any]) -> str:
        """根据 name 找工具并执行，返回字符串结果。

        Args:
            name: 工具名。
            params: LLM 传入的参数字典。

        Returns:
            工具执行结果（字符串）；找不到工具时返回 "Error: tool not found"。

        TODO: 实现查找 + 调用 + 异常兜底
        """
        raise NotImplementedError("TODO: execute")
