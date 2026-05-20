"""tests/test_security.py — 安全机制测试骨架。

重点验证「白名单 / 路径限制」是否真的拦得住已知的攻击向量：
    - 路径遍历（../../etc/passwd）
    - 符号链接逃逸（symlink → 系统目录）
    - shell 注入（command injection via ;、&、$()）
    - 后缀绕过（写 .sh 脚本）

这是 MiniBot 最关键的一组测试。实现时务必让这些用例先红、后绿。
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
        """读取 ../../etc/passwd 形式的路径应被 _check_path_allowed 拦下。"""
        # TODO:
        # tool = ReadFileTool(allowed_paths=[tmp_path])
        # result = tool.execute(path=str(tmp_path / ".." / ".." / "etc" / "passwd"))
        # assert result.startswith("Error")
        pytest.skip("TODO")

    def test_read_blocks_absolute_path_outside(self, tmp_path: Path) -> None:
        """绝对路径若不在 allowed_paths 内应被拒。"""
        # TODO:
        # tool = ReadFileTool(allowed_paths=[tmp_path])
        # assert tool.execute(path="/etc/passwd").startswith("Error")
        pytest.skip("TODO")

    def test_read_blocks_symlink_escape(self, tmp_path: Path) -> None:
        """symlink 指向 allowed_paths 之外的目标应被 resolve 后拒绝。"""
        # TODO:
        # outside = Path("/tmp/secret.txt"); outside.write_text("xxx")
        # link = tmp_path / "link.txt"; link.symlink_to(outside)
        # tool = ReadFileTool(allowed_paths=[tmp_path])
        # assert tool.execute(path=str(link)).startswith("Error")
        pytest.skip("TODO")

    def test_write_blocks_dotdot_escape(self, tmp_path: Path) -> None:
        """写文件同样要拦 ../ 逃逸。"""
        # TODO:
        # tool = WriteFileTool(allowed_paths=[tmp_path])
        # result = tool.execute(path=str(tmp_path / ".." / "evil.txt"), content="x")
        # assert result.startswith("Error")
        pytest.skip("TODO")


# ============================================================
# Shell 注入
# ============================================================


class TestShellInjection:
    def test_exec_blocks_semicolon_chain(self, tmp_path: Path) -> None:
        """`echo hi; rm -rf /` 这种串联命令，第二段 rm 不在白名单，应整体拒绝或只跑 echo。

        实现建议：因为 ExecTool 用 shlex.split + 不开 shell=True，
        ';' 会被当成普通参数传给 echo，根本不会执行 rm。
        测试目标是验证「绝对没有 rm 被执行」。
        """
        # TODO:
        # tool = ExecTool(cmd_whitelist=["echo"], workspace=tmp_path)
        # tool.execute(command="echo hi; rm -rf /tmp/test_canary")
        # assert (tmp_path / "test_canary").exists() is False  # canary 不存在=未被 rm
        pytest.skip("TODO")

    def test_exec_blocks_backtick_substitution(self, tmp_path: Path) -> None:
        """`echo $(whoami)` 应被当成字面量传给 echo，不发生命令替换。"""
        # TODO:
        # tool = ExecTool(cmd_whitelist=["echo"], workspace=tmp_path)
        # result = tool.execute(command="echo $(whoami)")
        # assert "$(whoami)" in result   # 字面量出现说明没被解释
        pytest.skip("TODO")

    def test_exec_blocks_pipe(self, tmp_path: Path) -> None:
        """管道 `ls | rm` 应失败：rm 不在白名单，且 | 不会被 shell 解释。"""
        # TODO
        pytest.skip("TODO")

    def test_exec_disabled_when_whitelist_empty(self, tmp_path: Path) -> None:
        """cmd_whitelist=[] 时任何命令都应被拒。"""
        # TODO:
        # tool = ExecTool(cmd_whitelist=[], workspace=tmp_path)
        # assert tool.execute(command="ls").startswith("Error")
        pytest.skip("TODO")


# ============================================================
# 后缀黑名单
# ============================================================


class TestForbiddenExtensions:
    def test_blocks_dot_sh(self, tmp_path: Path) -> None:
        """forbidden_extensions=[.sh] 时不能写 .sh 文件。"""
        # TODO
        pytest.skip("TODO")

    def test_blocks_dot_py(self, tmp_path: Path) -> None:
        """forbidden_extensions=[.py] 时不能写 .py 文件。"""
        # TODO
        pytest.skip("TODO")

    def test_case_insensitive(self, tmp_path: Path) -> None:
        """.SH / .Sh 应同样被拦（大小写不敏感）。"""
        # TODO
        pytest.skip("TODO")


# ============================================================
# 边界 & 异常
# ============================================================


class TestEdgeCases:
    def test_empty_allowed_paths_blocks_everything(self, tmp_path: Path) -> None:
        """allowed_paths=[] 时所有读写都应拒绝。"""
        # TODO
        pytest.skip("TODO")

    def test_workspace_not_in_allowed_paths_is_not_implicitly_granted(self, tmp_path: Path) -> None:
        """workspace 路径本身不应被「隐式信任」，必须显式出现在 allowed_paths 中。"""
        # TODO:
        # 验证：workspace=tmp_path, ReadFileTool(allowed_paths=[]) 读 tmp_path 内文件依然失败
        pytest.skip("TODO")
