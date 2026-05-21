"""tests/test_tools.py — 工具基础功能测试。

覆盖目标：
    - ExecTool 能跑白名单内的命令并返回 stdout
    - ReadFileTool 能读 allowed_paths 内的文件，大文件按行截断
    - WriteFileTool 能写文件、原子落盘、备份原文件
    - ToolRegistry 能 register / get_schemas / execute
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from minibot.tools import (
    ExecTool,
    ReadFileTool,
    ToolRegistry,
    WriteFileTool,
    safe_subprocess_env,
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

    def test_safe_subprocess_env_excludes_sensitive_vars(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # 父进程持有敏感凭据
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-leak-canary")
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "aws-canary")
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_canary")
        monkeypatch.setenv("DATABASE_URL", "postgres://u:p@h/d")

        env = safe_subprocess_env(tmp_path)

        assert "ANTHROPIC_API_KEY" not in env
        assert "AWS_SECRET_ACCESS_KEY" not in env
        assert "GITHUB_TOKEN" not in env
        assert "DATABASE_URL" not in env
        # 但执行所需的最小环境必须存在
        assert "PATH" in env
        assert env["HOME"] == str(tmp_path.resolve())
        assert env["LANG"] == "C.UTF-8"

    def test_exec_subprocess_does_not_leak_api_key(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # 端到端：ExecTool 真的跑子进程，stdout 里不应含 canary。
        # 用 ls 列 /tmp 的内容 + 把环境变量名也设成 canary 字符串验证。
        canary = "sk-ant-CANARY-XYZ-leak"
        monkeypatch.setenv("ANTHROPIC_API_KEY", canary)

        # 借助 Python 子进程直接验证 ExecTool 传给 subprocess.run 的 env。
        # 我们 monkeypatch subprocess.run 截获 env 参数。
        captured: dict = {}
        import minibot.tools as tools_module
        real_run = tools_module.subprocess.run

        def fake_run(*args, **kwargs):
            captured["env"] = kwargs.get("env")
            return real_run(*args, **kwargs)

        monkeypatch.setattr(tools_module.subprocess, "run", fake_run)

        tool = ExecTool(cmd_whitelist=["ls"], workspace=tmp_path)
        tool.execute(command="ls")

        env = captured["env"]
        assert env is not None, "ExecTool must pass explicit env to subprocess.run"
        assert "ANTHROPIC_API_KEY" not in env
        assert canary not in env.values()


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
