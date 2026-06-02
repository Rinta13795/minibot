"""tests/test_tools.py — 工具基础功能测试。

覆盖目标：
    - ExecTool 能跑白名单内的命令并返回 stdout
    - ReadFileTool 能读 allowed_paths 内的文件，大文件按行截断
    - WriteFileTool 能写文件、原子落盘、备份原文件
    - ToolRegistry 能 register / get_schemas / execute
"""

from __future__ import annotations

from pathlib import Path

from minibot.tools import ExecTool, ReadFileTool, ToolRegistry, WriteFileTool


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

    def test_blocks_oversize_content(self, tmp_path: Path) -> None:
        """超过 max_content_bytes 的写入应被拒绝，文件不应创建。"""
        tool = WriteFileTool(allowed_paths=[tmp_path], max_content_bytes=100)
        target = tmp_path / "big.txt"
        result = tool.execute(path=str(target), content="X" * 200)
        assert result.startswith("Error")
        assert "too large" in result.lower()
        assert not target.exists()

    def test_allows_content_at_size_limit(self, tmp_path: Path) -> None:
        """正好等于 max_content_bytes 的内容应被允许。"""
        tool = WriteFileTool(allowed_paths=[tmp_path], max_content_bytes=100)
        target = tmp_path / "ok.txt"
        result = tool.execute(path=str(target), content="X" * 100)
        assert not result.startswith("Error")
        assert target.read_text() == "X" * 100

    def test_size_check_uses_utf8_byte_count_not_char_count(
        self, tmp_path: Path
    ) -> None:
        """中文字符在 UTF-8 下占 3 字节。30 个中文 = 90 字节，应通过 cap=90。
        但 31 个中文 = 93 字节，应被拦截。"""
        tool = WriteFileTool(allowed_paths=[tmp_path], max_content_bytes=90)
        # 30 个 "中" = 90 字节，刚好通过
        result_ok = tool.execute(
            path=str(tmp_path / "ok.txt"), content="中" * 30
        )
        assert not result_ok.startswith("Error"), result_ok
        # 31 个 "中" = 93 字节，超过
        result_over = tool.execute(
            path=str(tmp_path / "over.txt"), content="中" * 31
        )
        assert result_over.startswith("Error")
        assert "too large" in result_over.lower()
        assert not (tmp_path / "over.txt").exists()

    def test_size_limit_zero_disables_check(self, tmp_path: Path) -> None:
        """max_content_bytes=0 时关闭大小检查（向后兼容）。"""
        tool = WriteFileTool(allowed_paths=[tmp_path], max_content_bytes=0)
        target = tmp_path / "any.txt"
        result = tool.execute(path=str(target), content="X" * 10_000)
        assert not result.startswith("Error")
        assert target.exists()

    def test_oversize_existing_file_blocks_backup_amplification(
        self, tmp_path: Path
    ) -> None:
        """现有文件超过 max_content_bytes 时，写入必须被拒——否则
        shutil.copy2 会把这个大文件复制到 .bak，凭空放大磁盘占用，
        新 content 再小也阻止不了。"""
        target = tmp_path / "huge.txt"
        # 用工具旁路直接创建一个 200KB 的现有文件
        target.write_bytes(b"X" * 200_000)

        tool = WriteFileTool(allowed_paths=[tmp_path], max_content_bytes=100_000)
        result = tool.execute(path=str(target), content="tiny")

        assert result.startswith("Error")
        assert "existing file too large" in result.lower()
        # .bak 不应被创建
        assert not (tmp_path / "huge.txt.bak").exists()
        # 原文件未被修改
        assert target.read_bytes() == b"X" * 200_000

    def test_oversize_existing_file_no_block_when_backup_disabled(
        self, tmp_path: Path
    ) -> None:
        """如果 make_backup=False，没有 shutil.copy2 放大风险，
        允许覆写大现有文件（新 content 仍受 max_content_bytes 限制）。"""
        target = tmp_path / "huge.txt"
        target.write_bytes(b"X" * 200_000)

        tool = WriteFileTool(
            allowed_paths=[tmp_path],
            max_content_bytes=100_000,
            make_backup=False,
        )
        result = tool.execute(path=str(target), content="replacement")

        assert not result.startswith("Error"), result
        assert target.read_text() == "replacement"
        assert not (tmp_path / "huge.txt.bak").exists()

    def test_oversize_existing_check_skipped_when_cap_zero(
        self, tmp_path: Path
    ) -> None:
        """cap=0（关闭检查）时，大现有文件也允许备份+写入（用户显式选择）。"""
        target = tmp_path / "huge.txt"
        target.write_bytes(b"X" * 200_000)

        tool = WriteFileTool(allowed_paths=[tmp_path], max_content_bytes=0)
        result = tool.execute(path=str(target), content="replacement")

        assert not result.startswith("Error"), result
        assert (tmp_path / "huge.txt.bak").exists()
        assert (tmp_path / "huge.txt.bak").read_bytes() == b"X" * 200_000


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
