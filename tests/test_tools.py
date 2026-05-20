"""tests/test_tools.py — 工具基础功能测试骨架。

覆盖目标：
    - ExecTool 能跑白名单内的命令并返回 stdout
    - ReadFileTool 能读 allowed_paths 内的文件
    - WriteFileTool 能写文件并原子落盘
    - ToolRegistry 能 register / get_schemas / execute

约定：
    - 用 tmp_path fixture 隔离文件系统副作用
    - 不联网，不调真实 LLM
"""

from __future__ import annotations

from pathlib import Path

import pytest

from minibot.tools import ExecTool, ReadFileTool, ToolRegistry, WriteFileTool


# ============================================================
# ExecTool
# ============================================================


class TestExecTool:
    def test_runs_whitelisted_command(self, tmp_path: Path) -> None:
        """白名单内的 echo 命令应能正常执行并返回 stdout。"""
        # TODO:
        # tool = ExecTool(cmd_whitelist=["echo"], workspace=tmp_path)
        # result = tool.execute(command="echo hello")
        # assert "hello" in result
        pytest.skip("TODO: implement ExecTool then enable")

    def test_blocks_non_whitelisted_command(self, tmp_path: Path) -> None:
        """白名单外的命令应被拒绝，返回 Error 开头的字符串。"""
        # TODO:
        # tool = ExecTool(cmd_whitelist=["echo"], workspace=tmp_path)
        # result = tool.execute(command="rm -rf /")
        # assert result.startswith("Error")
        pytest.skip("TODO: implement ExecTool then enable")

    def test_timeout_kills_subprocess(self, tmp_path: Path) -> None:
        """超过 timeout_sec 的命令应被 kill，返回超时错误。"""
        # TODO:
        # tool = ExecTool(cmd_whitelist=["sleep"], workspace=tmp_path, timeout_sec=1)
        # result = tool.execute(command="sleep 5")
        # assert "timeout" in result.lower() or "Error" in result
        pytest.skip("TODO: implement ExecTool then enable")


# ============================================================
# ReadFileTool
# ============================================================


class TestReadFileTool:
    def test_reads_file_in_allowed_dir(self, tmp_path: Path) -> None:
        """读 allowed_paths 内的文件应返回其内容。"""
        # TODO:
        # f = tmp_path / "hello.txt"
        # f.write_text("world", encoding="utf-8")
        # tool = ReadFileTool(allowed_paths=[tmp_path])
        # assert "world" in tool.execute(path=str(f))
        pytest.skip("TODO")

    def test_truncates_large_file(self, tmp_path: Path) -> None:
        """文件超过 max_bytes 时应截断返回。"""
        # TODO: 写一个 2MB 文件，设置 max_bytes=1MB，断言返回长度 <= 1MB + 截断标记
        pytest.skip("TODO")

    def test_missing_file_returns_error(self, tmp_path: Path) -> None:
        """读不存在的文件应返回 Error，不抛异常。"""
        # TODO:
        # tool = ReadFileTool(allowed_paths=[tmp_path])
        # assert tool.execute(path=str(tmp_path / "nope.txt")).startswith("Error")
        pytest.skip("TODO")


# ============================================================
# WriteFileTool
# ============================================================


class TestWriteFileTool:
    def test_writes_file_atomically(self, tmp_path: Path) -> None:
        """写入应通过 tmp + replace 完成，最终文件内容正确。"""
        # TODO:
        # tool = WriteFileTool(allowed_paths=[tmp_path])
        # tool.execute(path=str(tmp_path / "a.md"), content="hi")
        # assert (tmp_path / "a.md").read_text() == "hi"
        pytest.skip("TODO")

    def test_blocks_forbidden_extension(self, tmp_path: Path) -> None:
        """写 .sh / .py 后缀应被拒绝。"""
        # TODO:
        # tool = WriteFileTool(allowed_paths=[tmp_path], forbidden_extensions=[".sh"])
        # result = tool.execute(path=str(tmp_path / "evil.sh"), content="rm -rf /")
        # assert result.startswith("Error")
        pytest.skip("TODO")


# ============================================================
# ToolRegistry
# ============================================================


class TestToolRegistry:
    def test_register_and_execute(self, tmp_path: Path) -> None:
        """register 一个工具后能通过 execute 调用到。"""
        # TODO:
        # reg = ToolRegistry()
        # reg.register(ReadFileTool(allowed_paths=[tmp_path]))
        # ... 写一个临时文件 ...
        # result = reg.execute("read_file", {"path": str(f)})
        # assert "..." in result
        pytest.skip("TODO")

    def test_unknown_tool_returns_error(self) -> None:
        """调用不存在的工具应返回 Error。"""
        # TODO:
        # reg = ToolRegistry()
        # assert reg.execute("nope", {}).startswith("Error")
        pytest.skip("TODO")

    def test_get_schemas_returns_all_tools(self, tmp_path: Path) -> None:
        """get_schemas 返回所有已注册工具的 Anthropic schema。"""
        # TODO:
        # reg = ToolRegistry()
        # reg.register(ReadFileTool(allowed_paths=[tmp_path]))
        # reg.register(WriteFileTool(allowed_paths=[tmp_path]))
        # schemas = reg.get_schemas()
        # names = {s["name"] for s in schemas}
        # assert names == {"read_file", "write_file"}
        pytest.skip("TODO")
