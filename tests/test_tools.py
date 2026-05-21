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

    def test_blocks_symlink_write_prevents_leak(self, tmp_path: Path) -> None:
        # symlink 指向 allowed_paths 外的敏感文件
        external = tmp_path.parent / "secret.txt"
        external.write_text("sensitive-data", encoding="utf-8")
        link = tmp_path / "link.txt"
        link.symlink_to(external)

        tool = WriteFileTool(allowed_paths=[tmp_path])
        result = tool.execute(path=str(link), content="overwrite")

        assert result.startswith("Error")
        # 外部文件内容不应被复制到 allowed 目录下的备份
        assert not (tmp_path / "link.txt.bak").exists()
        # 外部文件未被修改
        assert external.read_text() == "sensitive-data"

    def test_blocks_symlink_at_backup_path_prevents_write(self, tmp_path: Path) -> None:
        # target 文件存在（普通文件），但 .bak 路径已是指向外部的 symlink
        target = tmp_path / "a.txt"
        target.write_text("original", encoding="utf-8")

        external = tmp_path.parent / "external.txt"
        external.write_text("external-data", encoding="utf-8")
        bak_link = tmp_path / "a.txt.bak"
        bak_link.symlink_to(external)

        tool = WriteFileTool(allowed_paths=[tmp_path])
        result = tool.execute(path=str(target), content="new-content")

        assert result.startswith("Error")
        # 外部文件不应被覆盖
        assert external.read_text() == "external-data"
        # 目标文件也不应被修改
        assert target.read_text() == "original"


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
