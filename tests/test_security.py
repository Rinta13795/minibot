"""tests/test_security.py — 安全机制测试。

重点验证「白名单 / 路径限制 / 黑名单」是否真的拦得住已知攻击向量：
    - 路径遍历（../../etc/passwd）
    - 符号链接逃逸（symlink → 系统目录）
    - shell 注入（command injection via ;、&&、$()、`` 等）
    - 后缀绕过（写 .sh / .py 脚本）
    - 黑名单兜底（rm -rf / shutdown / chmod 777 / dd）
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from minibot.tools import ExecTool, ReadFileTool, WriteFileTool


# ============================================================
# 路径遍历 & symlink 逃逸
# ============================================================


class TestPathTraversal:
    def test_read_blocks_dotdot_escape(self, tmp_path: Path) -> None:
        tool = ReadFileTool(allowed_paths=[tmp_path])
        result = tool.execute(path=str(tmp_path / ".." / ".." / "etc" / "passwd"))
        assert result.startswith("Error")

    def test_read_blocks_absolute_path_outside(self, tmp_path: Path) -> None:
        tool = ReadFileTool(allowed_paths=[tmp_path])
        assert tool.execute(path="/etc/passwd").startswith("Error")

    def test_read_blocks_symlink_escape(self, tmp_path: Path) -> None:
        outside = tmp_path.parent / "outside_secret.txt"
        outside.write_text("xxx", encoding="utf-8")
        try:
            link = tmp_path / "link.txt"
            link.symlink_to(outside)
            tool = ReadFileTool(allowed_paths=[tmp_path])
            result = tool.execute(path=str(link))
            assert result.startswith("Error")
        finally:
            outside.unlink(missing_ok=True)

    def test_write_blocks_dotdot_escape(self, tmp_path: Path) -> None:
        tool = WriteFileTool(allowed_paths=[tmp_path])
        result = tool.execute(path=str(tmp_path / ".." / "evil.txt"), content="x")
        assert result.startswith("Error")
        assert not (tmp_path.parent / "evil.txt").exists()


# ============================================================
# Shell 注入
# ============================================================


class TestShellInjection:
    def test_exec_blocks_semicolon_chain(self, tmp_path: Path) -> None:
        canary = tmp_path / "canary"
        canary.write_text("alive", encoding="utf-8")
        tool = ExecTool(cmd_whitelist=["echo"], workspace=tmp_path)
        result = tool.execute(command=f"echo hi; rm -rf {canary}")
        assert result.startswith("Error")
        assert canary.exists()

    def test_exec_blocks_double_amp(self, tmp_path: Path) -> None:
        tool = ExecTool(cmd_whitelist=["echo"], workspace=tmp_path)
        result = tool.execute(command="echo hi && rm -rf /")
        assert result.startswith("Error")

    def test_exec_blocks_dollar_paren(self, tmp_path: Path) -> None:
        tool = ExecTool(cmd_whitelist=["echo"], workspace=tmp_path)
        result = tool.execute(command="echo $(whoami)")
        assert result.startswith("Error")

    def test_exec_blocks_backtick(self, tmp_path: Path) -> None:
        tool = ExecTool(cmd_whitelist=["echo"], workspace=tmp_path)
        result = tool.execute(command="echo `whoami`")
        assert result.startswith("Error")

    def test_exec_blocks_pipe(self, tmp_path: Path) -> None:
        tool = ExecTool(cmd_whitelist=["ls"], workspace=tmp_path)
        result = tool.execute(command="ls | rm")
        assert result.startswith("Error")

    def test_exec_blacklist_rm_rf(self, tmp_path: Path) -> None:
        """即使白名单允许，rm -rf 也被黑名单兜底。"""
        tool = ExecTool(cmd_whitelist=["rm"], workspace=tmp_path)
        result = tool.execute(command="rm -rf /tmp/whatever")
        assert result.startswith("Error")

    def test_exec_blacklist_chmod_777(self, tmp_path: Path) -> None:
        tool = ExecTool(cmd_whitelist=["chmod"], workspace=tmp_path)
        result = tool.execute(command="chmod 777 /etc")
        assert result.startswith("Error")

    def test_exec_disabled_when_whitelist_empty(self, tmp_path: Path) -> None:
        tool = ExecTool(cmd_whitelist=[], workspace=tmp_path)
        assert tool.execute(command="ls").startswith("Error")


# ============================================================
# 后缀黑名单
# ============================================================


class TestForbiddenExtensions:
    def test_blocks_dot_sh(self, tmp_path: Path) -> None:
        tool = WriteFileTool(allowed_paths=[tmp_path], forbidden_extensions=[".sh"])
        assert tool.execute(path=str(tmp_path / "a.sh"), content="x").startswith("Error")

    def test_blocks_dot_py(self, tmp_path: Path) -> None:
        tool = WriteFileTool(allowed_paths=[tmp_path], forbidden_extensions=[".py"])
        assert tool.execute(path=str(tmp_path / "a.py"), content="x").startswith("Error")

    def test_case_insensitive(self, tmp_path: Path) -> None:
        tool = WriteFileTool(allowed_paths=[tmp_path], forbidden_extensions=[".sh"])
        assert tool.execute(path=str(tmp_path / "A.SH"), content="x").startswith("Error")


# ============================================================
# 边界 & 异常
# ============================================================


class TestEdgeCases:
    def test_empty_allowed_paths_blocks_read(self, tmp_path: Path) -> None:
        f = tmp_path / "x.txt"
        f.write_text("y", encoding="utf-8")
        tool = ReadFileTool(allowed_paths=[])
        assert tool.execute(path=str(f)).startswith("Error")

    def test_empty_allowed_paths_blocks_write(self, tmp_path: Path) -> None:
        tool = WriteFileTool(allowed_paths=[])
        assert tool.execute(path=str(tmp_path / "a.txt"), content="x").startswith("Error")

    def test_workspace_not_in_allowed_paths_is_not_implicitly_granted(
        self, tmp_path: Path
    ) -> None:
        f = tmp_path / "x.txt"
        f.write_text("y", encoding="utf-8")
        tool = ReadFileTool(allowed_paths=[])
        assert tool.execute(path=str(f)).startswith("Error")
