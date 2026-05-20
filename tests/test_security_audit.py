"""tests/test_security_audit.py — Task 8 安全审查。

10 个攻击向量的实际测试，所有都应被拦截。每个用例都有清晰断言 + 命中规则注释。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from minibot.tools import ExecTool, ReadFileTool


# ============================================================
# exec_command — 7 个 shell 注入 / 危险命令向量
# ============================================================


class TestExecCommandAudit:
    @pytest.fixture
    def tool(self, tmp_path: Path) -> ExecTool:
        # 给一个最大化的白名单，模拟\"最坏情况\"——白名单很宽时其他防线能不能拦
        return ExecTool(
            cmd_whitelist=["ls", "cat", "echo", "rm", "chmod", "dd"],
            workspace=tmp_path,
        )

    # 1) rm -rf /  → 黑名单 \brm\s+-rf\b 命中
    def test_v01_rm_rf_root(self, tool: ExecTool) -> None:
        result = tool.execute(command="rm -rf /")
        assert result.startswith("Error")
        assert "forbidden pattern" in result

    # 2) ls && rm -rf /  → 拼接符 && 命中
    def test_v02_ls_and_rm(self, tool: ExecTool) -> None:
        result = tool.execute(command="ls && rm -rf /")
        assert result.startswith("Error")
        assert "&&" in result

    # 3) ls; cat /etc/passwd  → 拼接符 ; 命中（并且黑名单还有 /etc/passwd 兜底）
    def test_v03_ls_semicolon_cat_passwd(self, tool: ExecTool) -> None:
        result = tool.execute(command="ls; cat /etc/passwd")
        assert result.startswith("Error")

    # 4) ls | xargs rm  → 拼接符 | 命中
    def test_v04_ls_pipe_xargs_rm(self, tool: ExecTool) -> None:
        result = tool.execute(command="ls | xargs rm")
        assert result.startswith("Error")
        assert "|" in result

    # 5) $(whoami)  → 拼接符 $( 命中
    def test_v05_dollar_paren(self, tool: ExecTool) -> None:
        result = tool.execute(command="echo $(whoami)")
        assert result.startswith("Error")
        assert "$(" in result

    # 6) `whoami`  → 拼接符 ` 命中
    def test_v06_backtick(self, tool: ExecTool) -> None:
        result = tool.execute(command="echo `whoami`")
        assert result.startswith("Error")
        assert "`" in result

    # 7) ls -la /etc/shadow  → 黑名单 /etc/shadow 命中
    def test_v07_ls_shadow(self, tool: ExecTool) -> None:
        result = tool.execute(command="ls -la /etc/shadow")
        assert result.startswith("Error")
        assert "/etc/shadow" in result


# ============================================================
# read_file — 3 个路径穿越向量
# ============================================================


class TestReadFileAudit:
    # 8) ../../../etc/passwd  → resolve 后落到 /etc/passwd，不在 allowed_paths 内
    def test_v08_dotdot_passwd(self, tmp_path: Path) -> None:
        tool = ReadFileTool(allowed_paths=[tmp_path])
        result = tool.execute(path=str(tmp_path / ".." / ".." / ".." / "etc" / "passwd"))
        assert result.startswith("Error")

    # 9) /etc/passwd (绝对路径) → 显然不在 allowed_paths
    def test_v09_absolute_passwd(self, tmp_path: Path) -> None:
        tool = ReadFileTool(allowed_paths=[tmp_path])
        result = tool.execute(path="/etc/passwd")
        assert result.startswith("Error")

    # 10) symlink → /etc/passwd  → resolve(strict=True) 跟随 symlink 后落到 /etc/passwd
    def test_v10_symlink_to_passwd(self, tmp_path: Path) -> None:
        link = tmp_path / "innocent_looking.txt"
        # 我们不直接 link 到 /etc/passwd（在某些 CI 上不可读），构造一个同等情景
        outside = tmp_path.parent / "outside_target.txt"
        outside.write_text("secret", encoding="utf-8")
        try:
            link.symlink_to(outside)
            tool = ReadFileTool(allowed_paths=[tmp_path])
            result = tool.execute(path=str(link))
            assert result.startswith("Error")
            # 关键：即使 link 本身位于 allowed_paths 内，resolve 后真实路径在外面就拒
        finally:
            outside.unlink(missing_ok=True)


# ============================================================
# 额外覆盖：可能被遗漏的攻击面
# ============================================================


class TestAdditionalAttackSurface:
    def test_newline_injection(self, tmp_path: Path) -> None:
        """换行符注入：echo hi\\nrm -rf / — 拼接符 \\n 已覆盖。"""
        tool = ExecTool(cmd_whitelist=["echo", "rm"], workspace=tmp_path)
        result = tool.execute(command="echo hi\nrm -rf /")
        assert result.startswith("Error")

    def test_redirect_blocked(self, tmp_path: Path) -> None:
        """重定向 > 也算拼接符 — 防止把 LLM 输出导到敏感文件。"""
        tool = ExecTool(cmd_whitelist=["echo"], workspace=tmp_path)
        result = tool.execute(command="echo x > /tmp/evil")
        assert result.startswith("Error")

    def test_fork_bomb(self, tmp_path: Path) -> None:
        """经典 fork bomb :(){...} — 黑名单正则覆盖。"""
        tool = ExecTool(cmd_whitelist=["bash"], workspace=tmp_path)
        result = tool.execute(command=":(){ :|:& };:")
        assert result.startswith("Error")

    def test_sudo_blocked(self, tmp_path: Path) -> None:
        """sudo 黑名单兜底。"""
        tool = ExecTool(cmd_whitelist=["sudo", "ls"], workspace=tmp_path)
        result = tool.execute(command="sudo ls /root")
        assert result.startswith("Error")

    def test_dd_block(self, tmp_path: Path) -> None:
        """dd if=/dev/zero of=/dev/sda — 黑名单 \\bdd\\s+if= 命中。"""
        tool = ExecTool(cmd_whitelist=["dd"], workspace=tmp_path)
        result = tool.execute(command="dd if=/dev/zero of=/dev/sda")
        assert result.startswith("Error")

    def test_write_through_parent_dotdot(self, tmp_path: Path) -> None:
        """写文件 ../ 逃逸：parent resolve 后展开成 tmp_path.parent，不在 allowed_paths。"""
        from minibot.tools import WriteFileTool

        tool = WriteFileTool(allowed_paths=[tmp_path])
        result = tool.execute(path=str(tmp_path / ".." / "evil.txt"), content="x")
        assert result.startswith("Error")
        assert not (tmp_path.parent / "evil.txt").exists()


# ============================================================
# 产生人类可读的结果报告（pytest -s 时打印）
# ============================================================


def test_audit_report_printable(capsys) -> None:
    """打印 10 个向量的命中规则表，方便复制到 PR 描述。"""
    rows = [
        ("01", "rm -rf /", "blacklist: \\brm\\s+-rf\\b"),
        ("02", "ls && rm -rf /", "splice token: &&"),
        ("03", "ls; cat /etc/passwd", "splice token: ;"),
        ("04", "ls | xargs rm", "splice token: |"),
        ("05", "echo $(whoami)", "splice token: $("),
        ("06", "echo `whoami`", "splice token: `"),
        ("07", "ls -la /etc/shadow", "blacklist: /etc/shadow"),
        ("08", "../../../etc/passwd", "path resolve → outside allowed_paths"),
        ("09", "/etc/passwd", "absolute path → outside allowed_paths"),
        ("10", "symlink → /etc/passwd", "resolve(strict=True) follows symlink → blocked"),
    ]
    for n, vec, rule in rows:
        print(f"  [v{n}] {vec:<30} → {rule}")
