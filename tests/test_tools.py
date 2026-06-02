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

from minibot.tools import DANGEROUS_EXECUTORS, ExecTool, ReadFileTool, ToolRegistry, WriteFileTool


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

    @pytest.mark.parametrize(
        "dangerous",
        ["python3", "bash", "sh", "git", "curl", "wget", "docker", "pip",
         "cat", "grep", "find", "node", "ssh"],
    )
    def test_rejects_dangerous_executor_in_whitelist(
        self, tmp_path: Path, dangerous: str
    ) -> None:
        # 这些命令进白名单会让 ExecTool 失去隔离能力，启动时必须 ValueError
        with pytest.raises(ValueError, match="dangerous executors"):
            ExecTool(cmd_whitelist=["ls", dangerous], workspace=tmp_path)

    def test_safe_whitelist_initializes(self, tmp_path: Path) -> None:
        # 安全命令应允许构造
        tool = ExecTool(cmd_whitelist=["ls", "echo", "pwd"], workspace=tmp_path)
        assert "ls" in tool.cmd_whitelist

    @pytest.mark.parametrize(
        "disguised",
        [
            "/usr/bin/python3",
            "/usr/local/bin/python3",
            "./python3",
            "../bin/python3",
            "/bin/bash",
            "/usr/bin/git",
            "/usr/bin/cat",
            "python3.exe",          # Windows 风格
            "/opt/tools/python3.EXE",
        ],
    )
    def test_rejects_path_prefixed_dangerous_executor(
        self, tmp_path: Path, disguised: str
    ) -> None:
        # 路径前缀和 .exe 后缀都不能绕过 basename 校验
        with pytest.raises(ValueError, match="dangerous executors"):
            ExecTool(cmd_whitelist=["ls", disguised], workspace=tmp_path)

    @pytest.mark.parametrize(
        "case_variant",
        [
            "Python3",
            "PYTHON3",
            "PyThOn3",
            "BASH",
            "Git",
            "CAT",
            "/usr/bin/PYTHON3",
            "/USR/BIN/python3",
            "Python3.EXE",
            "PYTHON3.exe",
        ],
    )
    def test_rejects_case_variants_of_dangerous_executor(
        self, tmp_path: Path, case_variant: str
    ) -> None:
        # macOS HFS+ 与 Windows 都是大小写不敏感，必须按小写归一化比对
        with pytest.raises(ValueError, match="dangerous executors"):
            ExecTool(cmd_whitelist=["ls", case_variant], workspace=tmp_path)

    def test_dangerous_executors_constant_covers_required_set(self) -> None:
        # 安全审计要求的最小拒绝集（如果将来误删，此测试会提醒）
        must_block = {
            "sh", "bash", "python", "python3", "node", "ruby", "perl",
            "git", "docker", "pip", "curl", "wget", "ssh",
            "cat", "grep", "find",
        }
        assert must_block.issubset(DANGEROUS_EXECUTORS)


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
