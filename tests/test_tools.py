"""tests/test_tools.py — 工具基础功能测试。

覆盖目标：
    - ExecTool 能跑白名单内的命令并返回 stdout
    - ReadFileTool 能读 allowed_paths 内的文件，大文件按行截断
    - WriteFileTool 能写文件、原子落盘、备份原文件
    - ToolRegistry 能 register / get_schemas / execute
"""

from __future__ import annotations

from pathlib import Path

import pytest

from minibot.tools import (
    ExecTool,
    ReadFileTool,
    ToolRegistry,
    WriteFileTool,
    validate_allowed_paths,
)


# ============================================================
# ExecTool
# ============================================================


class TestExecTool:
    def test_runs_whitelisted_command(self, tmp_path: Path) -> None:
        tool = ExecTool(cmd_whitelist=["echo"], workspace=tmp_path)
        result = tool.execute(command="echo hello")
        assert "hello" in result
        assert "returncode: 0" in result

    def test_blocks_non_whitelisted_command(self, tmp_path: Path) -> None:
        tool = ExecTool(cmd_whitelist=["echo"], workspace=tmp_path)
        result = tool.execute(command="cat /etc/hosts")
        assert result.startswith("Error")

    def test_timeout_kills_subprocess(self, tmp_path: Path) -> None:
        tool = ExecTool(cmd_whitelist=["sleep"], workspace=tmp_path, timeout_sec=1)
        result = tool.execute(command="sleep 5")
        assert "timed out" in result.lower() or result.startswith("Error")


# ============================================================
# ReadFileTool
# ============================================================


class TestReadFileTool:
    def test_reads_file_in_allowed_dir(self, tmp_path: Path) -> None:
        f = tmp_path / "hello.txt"
        f.write_text("world", encoding="utf-8")
        tool = ReadFileTool(allowed_paths=[tmp_path])
        assert "world" in tool.execute(path=str(f))

    def test_truncates_large_file_by_lines(self, tmp_path: Path) -> None:
        f = tmp_path / "big.txt"
        f.write_text("\n".join(f"line {i}" for i in range(5000)), encoding="utf-8")
        tool = ReadFileTool(allowed_paths=[tmp_path], max_lines=100)
        result = tool.execute(path=str(f))
        # 100 lines kept, rest truncated
        assert "line 99" in result
        assert "line 4999" not in result
        assert "truncated" in result.lower()

    def test_missing_file_returns_error(self, tmp_path: Path) -> None:
        tool = ReadFileTool(allowed_paths=[tmp_path])
        assert tool.execute(path=str(tmp_path / "nope.txt")).startswith("Error")


# ============================================================
# allowed_paths 过宽校验
# ============================================================


class TestBroadAllowedPaths:
    @pytest.mark.parametrize(
        "broad",
        ["/", "/var", "/tmp", "/etc", "/usr", "/opt", "/Users", "/home"],
    )
    def test_read_file_rejects_broad_paths(self, broad: str) -> None:
        with pytest.raises(ValueError, match="too broad"):
            ReadFileTool(allowed_paths=[Path(broad)])

    @pytest.mark.parametrize(
        "broad",
        ["/", "/var", "/tmp", "/etc"],
    )
    def test_write_file_rejects_broad_paths(self, broad: str) -> None:
        with pytest.raises(ValueError, match="too broad"):
            WriteFileTool(allowed_paths=[Path(broad)])

    def test_rejects_user_home(self) -> None:
        home = Path.home()
        with pytest.raises(ValueError, match="too broad"):
            ReadFileTool(allowed_paths=[home])

    def test_allow_broad_paths_override(self, tmp_path: Path) -> None:
        """显式 allow_broad_paths=True 时应允许任意路径（包括 /）。"""
        tool = ReadFileTool(allowed_paths=[Path("/")], allow_broad_paths=True)
        # 仅校验构造不抛；实际读取会被 path 检查约束
        assert Path("/").resolve() in tool.allowed_paths

    def test_workspace_subdirectory_allowed(self, tmp_path: Path) -> None:
        """正常 workspace 子目录不应触发 broad 检查（即使在 /var 之下，
        因为 tmp_path 是具体子路径而非 /var 本身）。"""
        tool = ReadFileTool(allowed_paths=[tmp_path])
        assert tmp_path.resolve() in tool.allowed_paths

    def test_macos_var_symlink_handled(self) -> None:
        """macOS 下 /tmp 与 /var 都 symlink 到 /private/* —— resolve 后的
        路径也必须命中 broad 集合（用户写 "/tmp" 不能用 macOS 路径 quirk
        绕过）。"""
        # /tmp 直接拦截
        with pytest.raises(ValueError, match="too broad"):
            ReadFileTool(allowed_paths=[Path("/tmp")])
        # /private/tmp（macOS resolve 形式）也拦截
        if Path("/private/tmp").exists():
            with pytest.raises(ValueError, match="too broad"):
                ReadFileTool(allowed_paths=[Path("/private/tmp")])

    def test_validate_allowed_paths_helper_directly(self, tmp_path: Path) -> None:
        # 单元层级测试 helper
        assert validate_allowed_paths([tmp_path]) == {tmp_path.resolve()}
        with pytest.raises(ValueError):
            validate_allowed_paths([Path("/")])
        # bypass
        assert validate_allowed_paths(
            [Path("/")], allow_broad=True
        ) == {Path("/").resolve()}


# ============================================================
# WriteFileTool
# ============================================================


class TestWriteFileTool:
    def test_writes_file_atomically(self, tmp_path: Path) -> None:
        tool = WriteFileTool(allowed_paths=[tmp_path])
        result = tool.execute(path=str(tmp_path / "a.md"), content="hi")
        assert not result.startswith("Error")
        assert (tmp_path / "a.md").read_text() == "hi"

    def test_creates_backup_when_overwriting(self, tmp_path: Path) -> None:
        target = tmp_path / "a.md"
        target.write_text("old", encoding="utf-8")
        tool = WriteFileTool(allowed_paths=[tmp_path])
        tool.execute(path=str(target), content="new")
        assert target.read_text() == "new"
        assert (tmp_path / "a.md.bak").exists()
        assert (tmp_path / "a.md.bak").read_text() == "old"

    def test_blocks_forbidden_extension(self, tmp_path: Path) -> None:
        tool = WriteFileTool(allowed_paths=[tmp_path], forbidden_extensions=[".sh"])
        result = tool.execute(path=str(tmp_path / "evil.sh"), content="rm -rf /")
        assert result.startswith("Error")
        assert not (tmp_path / "evil.sh").exists()


# ============================================================
# ToolRegistry
# ============================================================


class TestToolRegistry:
    def test_register_and_execute(self, tmp_path: Path) -> None:
        f = tmp_path / "x.txt"
        f.write_text("abc", encoding="utf-8")
        reg = ToolRegistry()
        reg.register(ReadFileTool(allowed_paths=[tmp_path]))
        result = reg.execute("read_file", {"path": str(f)})
        assert "abc" in result

    def test_unknown_tool_returns_error(self) -> None:
        reg = ToolRegistry()
        assert reg.execute("nope", {}).startswith("Error")

    def test_get_schemas_returns_all_tools(self, tmp_path: Path) -> None:
        reg = ToolRegistry()
        reg.register(ReadFileTool(allowed_paths=[tmp_path]))
        reg.register(WriteFileTool(allowed_paths=[tmp_path]))
        schemas = reg.get_schemas()
        names = {s["name"] for s in schemas}
        assert names == {"read_file", "write_file"}
        for s in schemas:
            assert "input_schema" in s
            assert s["input_schema"]["type"] == "object"
