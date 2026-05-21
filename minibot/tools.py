"""tools.py — 内置工具集合。

对应 Nanobot 的 agent/tools/ 目录，但极简：只实现三个最基础的工具：
    - ExecTool        执行 shell 命令（受 cmd_whitelist 限制 + 黑名单 + 拼接符防护）
    - ReadFileTool    读文件（受 allowed_paths 限制，符号链接 resolve 后再校验）
    - WriteFileTool   写文件（受 allowed_paths 限制 + 写前备份 + 原子落盘）

安全设计原则：
    1. 白名单优先：默认拒绝，显式允许才放行。
    2. 黑名单兜底：即使白名单允许，遇到危险模式（rm -rf / shutdown / dd 等）仍拒绝。
    3. 命令拼接符检测：&&、||、;、|、$()、`` 一律拒绝，防止白名单绕过。
    4. 路径必须 resolve() 后再判断，防止 ../../etc/passwd 和 symlink 绕过。
    5. 写文件前备份原文件（同目录 .bak），失败可回滚。
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import tempfile
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


# ---- 安全常量 ----

# 即使命令本身在白名单内，命令参数里出现下列模式也整体拒绝
DEFAULT_BLACKLIST_PATTERNS: tuple[str, ...] = (
    r"\brm\s+-rf\b",       # rm -rf
    r"\brm\s+-fr\b",       # rm -fr
    r"\bshutdown\b",        # shutdown
    r"\breboot\b",          # reboot
    r"\bpoweroff\b",        # poweroff
    r"\bhalt\b",            # halt
    r"\bchmod\s+777\b",     # chmod 777
    r"\bchmod\s+-R\s+777\b",
    r"\bdd\s+if=",          # dd if=...
    r"\bmkfs\b",            # mkfs.*
    r"\b:\(\)\s*\{",        # fork bomb :(){
    r":\(\)\{",             # 紧凑 fork bomb
    r"\bsudo\b",            # sudo
    r"\bsu\s",              # su -
    r"/etc/passwd",
    r"/etc/shadow",
)

# 命令字符串里出现以下任一字符/序列，都视作拼接攻击，直接拒
COMMAND_SPLICE_TOKENS: tuple[str, ...] = (
    "&&", "||", ";", "|", "$(", "`", ">", "<", "\n", "&",
)

# 禁止进入 cmd_whitelist 的解释器/shell/包管理器/容器/网络/版本控制工具。
# 这些命令即使在 shell=False 下也能通过自身能力穿透白名单：
#   - python3 / node / ruby / perl / php   → 任意代码执行、读环境变量、网络
#   - sh / bash / zsh                       → 直接 shell 执行器
#   - git                                   → 支持 `git -c alias.x=!cmd` shell alias、hooks
#   - docker                                → 容器逃逸、挂载宿主、提权
#   - pip / pip3                            → 安装包会执行构建代码
#   - curl / wget / ssh / scp / nc          → 数据外传、内网访问、payload 下载
# 文件读取类（cat / grep / find）单独拒绝，因为它们能绕过 ReadFileTool.allowed_paths。
def _executable_basename(name: str) -> str:
    """从命令/路径中提取可执行文件名用于安全比较。

    将 "/usr/bin/python3"、"./python3"、"..\\python3.exe" 等都归一为
    "python3"（Windows 上同时去掉 .exe）。这是防止
    DANGEROUS_EXECUTORS 字面量集合校验被路径前缀绕过的关键。
    """
    base = os.path.basename(name.strip())
    if base.lower().endswith(".exe"):
        base = base[:-4]
    return base


DANGEROUS_EXECUTORS: frozenset[str] = frozenset({
    "sh", "bash", "zsh", "dash", "ksh", "fish",
    "python", "python3", "node", "ruby", "perl", "php", "lua",
    "git", "docker", "podman", "kubectl",
    "pip", "pip3", "npm", "pnpm", "yarn", "gem",
    "curl", "wget", "ssh", "scp", "sftp", "nc", "ncat", "telnet",
    "cat", "grep", "egrep", "fgrep", "rgrep", "find", "fd",
    "head", "tail", "less", "more", "view", "vi", "vim", "nano", "emacs",
    "awk", "sed", "xargs", "tee",
    "eval", "exec", "env",
})


class Tool(ABC):
    """工具基类（对应 Nanobot agent/tools/base.py 的 Tool ABC）。

    每个工具必须暴露三个属性：name / description / parameters（JSON Schema），
    并实现 execute()。core.py 在 tool_use 循环里按 name 找到工具并调用。
    """

    @property
    @abstractmethod
    def name(self) -> str:
        raise NotImplementedError

    @property
    @abstractmethod
    def description(self) -> str:
        raise NotImplementedError

    @property
    @abstractmethod
    def parameters(self) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def execute(self, **kwargs: Any) -> str:
        raise NotImplementedError

    def to_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.parameters,
        }


# ============================================================
# ExecTool
# ============================================================


class ExecTool(Tool):
    """执行 shell 命令，受白名单 + 黑名单 + 拼接符防护三层约束。

    安全策略：
        1. cmd_whitelist 限制可执行文件名（如 ["ls", "cat", "python3"]）。
        2. 整条命令对 COMMAND_SPLICE_TOKENS 做字符串检查，任一命中即拒绝。
        3. blacklist_patterns 是兜底正则，命中即拒绝（即使白名单允许）。
        4. shlex.split 后取 tokens[0] 做白名单匹配。
        5. subprocess.run(shell=False) — 不进 shell，& | ; 不被解释。
        6. timeout 防卡死。
    """

    def __init__(
        self,
        cmd_whitelist: list[str],
        workspace: Path,
        timeout_sec: int = 30,
        blacklist_patterns: list[str] | None = None,
    ) -> None:
        whitelist_set = set(cmd_whitelist)
        # 启动时拒绝危险解释器/shell/网络/版本控制工具进入白名单。
        # 这些命令能从内部穿透 shell=False、拼接符防护、文件路径白名单。
        #
        # 必须按 basename 比较，否则攻击者可以用 "/usr/bin/python3" 或
        # "./python3" 绕过字面量集合检查（构造期不命中，但 execute() 阶段
        # tokens[0] 仍等于该路径，进入白名单后被实际执行）。
        unsafe = {
            entry for entry in whitelist_set
            if _executable_basename(entry) in DANGEROUS_EXECUTORS
        }
        if unsafe:
            raise ValueError(
                "cmd_whitelist contains dangerous executors that can bypass "
                f"sandboxing: {sorted(unsafe)}. Use dedicated tools instead "
                "(ReadFileTool for file reads, etc.)."
            )
        self.cmd_whitelist = whitelist_set
        self.workspace = workspace.resolve()
        self.timeout_sec = timeout_sec
        patterns = list(DEFAULT_BLACKLIST_PATTERNS)
        if blacklist_patterns:
            patterns.extend(blacklist_patterns)
        self._blacklist = [re.compile(p, re.IGNORECASE) for p in patterns]
        if not self.workspace.exists() or not self.workspace.is_dir():
            raise ValueError(f"workspace does not exist: {self.workspace}")

    @property
    def name(self) -> str:
        return "exec"

    @property
    def description(self) -> str:
        return "Run a shell command from the whitelist and return stdout/stderr."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Command string to execute.",
                }
            },
            "required": ["command"],
        }

    def execute(self, **kwargs: Any) -> str:
        command = kwargs.get("command")
        if not isinstance(command, str) or not command.strip():
            return "Error: missing command"

        # 1) splice token check on raw string — 阻止 echo$(rm -rf /) 等绕过
        for tok in COMMAND_SPLICE_TOKENS:
            if tok in command:
                return f"Error: command contains forbidden token '{tok}'"

        # 2) blacklist regex 检查整条命令
        for pat in self._blacklist:
            if pat.search(command):
                return f"Error: command matches forbidden pattern '{pat.pattern}'"

        # 3) shlex 拆分 + 白名单
        try:
            tokens = shlex.split(command)
        except ValueError as exc:
            return f"Error: invalid command: {exc}"
        if not tokens:
            return "Error: empty command"
        if not self.cmd_whitelist:
            return "Error: exec tool disabled (whitelist empty)"
        if tokens[0] not in self.cmd_whitelist:
            return f"Error: command '{tokens[0]}' not in whitelist"
        # 运行时再按 basename 检查一次，兜底以防 cmd_whitelist 通过未来某种
        # 渠道（动态修改、绕过 __init__）进入危险状态。
        if _executable_basename(tokens[0]) in DANGEROUS_EXECUTORS:
            return f"Error: command '{tokens[0]}' resolves to a dangerous executor"

        # 4) 真正执行
        try:
            result = subprocess.run(
                tokens,
                cwd=self.workspace,
                timeout=self.timeout_sec,
                capture_output=True,
                text=True,
                shell=False,
            )
        except subprocess.TimeoutExpired:
            return f"Error: command timed out after {self.timeout_sec}s"
        except FileNotFoundError:
            return f"Error: executable not found: {tokens[0]}"
        except Exception as exc:  # pragma: no cover - defensive
            return f"Error: command failed: {exc}"

        stdout = result.stdout.rstrip()
        stderr = result.stderr.rstrip()
        return "\n".join([
            f"stdout: {stdout}" if stdout else "stdout:",
            f"stderr: {stderr}" if stderr else "stderr:",
            f"returncode: {result.returncode}",
        ])

    def _is_whitelisted(self, command: str) -> bool:
        try:
            tokens = shlex.split(command)
        except ValueError:
            return False
        return bool(tokens) and tokens[0] in self.cmd_whitelist


# ============================================================
# ReadFileTool
# ============================================================


class ReadFileTool(Tool):
    """读取文件内容，受 allowed_paths 限制。大文件按行截断。"""

    DEFAULT_MAX_LINES = 1000

    def __init__(
        self,
        allowed_paths: list[Path],
        max_bytes: int = 1_000_000,
        max_lines: int | None = None,
    ) -> None:
        self.allowed_paths = {path.resolve() for path in allowed_paths}
        self.max_bytes = max_bytes
        self.max_lines = max_lines if max_lines is not None else self.DEFAULT_MAX_LINES

    @property
    def name(self) -> str:
        return "read_file"

    @property
    def description(self) -> str:
        return "Read a file from an allowed directory and return its text content (first N lines for large files)."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path to read.",
                }
            },
            "required": ["path"],
        }

    def execute(self, **kwargs: Any) -> str:
        path = kwargs.get("path")
        if not isinstance(path, str) or not path.strip():
            return "Error: missing path"

        # resolve(strict=True) 会跟随 symlink 并要求文件存在 — 这是 symlink 防护的关键。
        # 不存在时回退到 strict=False，让下面的 exists 检查统一返回 "file not found"。
        try:
            target = Path(path).resolve(strict=True)
        except FileNotFoundError:
            target = Path(path).resolve(strict=False)
            if not self._check_path_allowed(target):
                return "Error: path not allowed"
            return "Error: file not found"
        except Exception as exc:
            return f"Error: failed to resolve path: {exc}"

        if not self._check_path_allowed(target):
            return "Error: path not allowed"
        if not target.is_file():
            return "Error: file not found"

        # 按行读，截到 max_lines；同时受 max_bytes 兜底（防止单行超大）。
        try:
            lines: list[str] = []
            total_bytes = 0
            truncated_lines = False
            truncated_bytes = False
            with target.open("rb") as fh:
                for idx, raw in enumerate(fh):
                    if idx >= self.max_lines:
                        truncated_lines = True
                        break
                    total_bytes += len(raw)
                    if total_bytes > self.max_bytes:
                        truncated_bytes = True
                        break
                    lines.append(raw.decode("utf-8", errors="replace"))
        except Exception as exc:
            return f"Error: failed to read file: {exc}"

        body = "".join(lines)
        notes = []
        if truncated_lines:
            notes.append(f"[truncated: showing first {self.max_lines} lines]")
        if truncated_bytes:
            notes.append(f"[truncated: exceeded {self.max_bytes} bytes]")
        if notes:
            body += "\n\n" + " ".join(notes)
        return body

    def _check_path_allowed(self, target: Path) -> bool:
        if not self.allowed_paths:
            return False
        return any(target.is_relative_to(base) for base in self.allowed_paths)


# ============================================================
# WriteFileTool
# ============================================================


class WriteFileTool(Tool):
    """写文件，受 allowed_paths 限制；写前备份原文件，原子替换。"""

    def __init__(
        self,
        allowed_paths: list[Path],
        forbidden_extensions: list[str] | None = None,
        make_backup: bool = True,
    ) -> None:
        self.allowed_paths = {path.resolve() for path in allowed_paths}
        self.forbidden_extensions = {
            ext.lower() if ext.startswith(".") else "." + ext.lower()
            for ext in (forbidden_extensions or []) if ext
        }
        self.make_backup = make_backup

    @property
    def name(self) -> str:
        return "write_file"

    @property
    def description(self) -> str:
        return "Write text content to a file inside an allowed directory (creates .bak backup if file exists)."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Target file path.",
                },
                "content": {
                    "type": "string",
                    "description": "UTF-8 text content to write.",
                },
            },
            "required": ["path", "content"],
        }

    def execute(self, **kwargs: Any) -> str:
        path = kwargs.get("path")
        content = kwargs.get("content")
        if not isinstance(path, str) or not path.strip():
            return "Error: missing path"
        if not isinstance(content, str):
            return "Error: missing content"

        # 对父目录做 resolve（文件本身可能尚不存在），用父目录做白名单校验。
        target = Path(path)
        try:
            parent_resolved = target.parent.resolve(strict=False)
        except Exception as exc:
            return f"Error: failed to resolve path: {exc}"
        target_resolved = parent_resolved / target.name

        if not self.allowed_paths:
            return "Error: path not allowed (no allowed_paths configured)"
        if not any(target_resolved.is_relative_to(base) for base in self.allowed_paths):
            return "Error: path not allowed"

        # 后缀黑名单（大小写不敏感）
        suffix = target_resolved.suffix.lower()
        if suffix in self.forbidden_extensions:
            return f"Error: forbidden file extension '{suffix}'"

        target_resolved.parent.mkdir(parents=True, exist_ok=True)

        # 备份原文件
        backup_path: Path | None = None
        if self.make_backup and target_resolved.exists():
            backup_path = target_resolved.with_suffix(target_resolved.suffix + ".bak")
            try:
                shutil.copy2(target_resolved, backup_path)
            except Exception as exc:
                return f"Error: failed to create backup: {exc}"

        # 原子写入
        tmp_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=target_resolved.parent,
                prefix=f".{target_resolved.name}.",
                suffix=".tmp",
                delete=False,
            ) as tmp_file:
                tmp_file.write(content)
                tmp_name = tmp_file.name
            os.replace(tmp_name, target_resolved)
        except Exception as exc:
            if tmp_name:
                Path(tmp_name).unlink(missing_ok=True)
            return f"Error: failed to write file: {exc}"

        suffix_msg = f" (backup: {backup_path.name})" if backup_path else ""
        return f"Wrote {len(content)} characters to {target_resolved}{suffix_msg}"


# ============================================================
# 工具注册表
# ============================================================


class ToolRegistry:
    """工具注册表，core.py 通过它分发 tool_use 调用。"""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get_schemas(self) -> list[dict[str, Any]]:
        return [tool.to_schema() for tool in self._tools.values()]

    def execute(self, name: str, params: dict[str, Any]) -> str:
        tool = self._tools.get(name)
        if tool is None:
            return f"Error: tool '{name}' not found"
        try:
            return tool.execute(**params)
        except Exception as exc:  # pragma: no cover - defensive
            return f"Error: tool execution failed: {exc}"
