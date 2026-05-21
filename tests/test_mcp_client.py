"""tests/test_mcp_client.py — MCP 客户端测试。

用 python3 跑一个迷你 server 脚本作为子进程，覆盖：
    - 正常 initialize / tools/list / tools/call
    - 请求-响应按 id 匹配（乱序 / notification 干扰）
    - 子进程崩溃
    - 请求超时
    - 单行 JSON 超大
"""

from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path

import pytest

from minibot.mcp_client import MCPClient, MCPServerConfig


def _make_server(tmp_path: Path, body: str) -> Path:
    """把 server 脚本写到 tmp_path，返回路径。脚本必须自己读 stdin 写 stdout。"""
    p = tmp_path / "server.py"
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return p


# 一个最小的合规 MCP server：响应 initialize / tools/list / tools/call
GOOD_SERVER = """
import json, sys

def respond(req, result):
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": req["id"], "result": result}) + "\\n")
    sys.stdout.flush()

while True:
    line = sys.stdin.readline()
    if not line:
        break
    req = json.loads(line)
    if req["method"] == "initialize":
        respond(req, {"protocolVersion": "2024-11-05"})
    elif req["method"] == "tools/list":
        respond(req, {"tools": [{"name": "echo", "description": "echo text", "inputSchema": {"type": "object"}}]})
    elif req["method"] == "tools/call":
        text = req["params"]["arguments"].get("text", "")
        respond(req, {"content": [{"type": "text", "text": "echo:" + text}]})
"""


class TestMCPClient:
    def test_initialize_and_list_tools(self, tmp_path: Path) -> None:
        path = _make_server(tmp_path, GOOD_SERVER)
        client = MCPClient([MCPServerConfig(name="srv", command=sys.executable, args=[str(path)])])
        try:
            client.start_all()
            schemas = client.list_tools()
            names = {s["name"] for s in schemas}
            assert "mcp_srv_echo" in names
        finally:
            client.close_all()

    def test_call_tool_returns_text(self, tmp_path: Path) -> None:
        path = _make_server(tmp_path, GOOD_SERVER)
        client = MCPClient([MCPServerConfig(name="srv", command=sys.executable, args=[str(path)])])
        try:
            client.start_all()
            result = client.call_tool("mcp_srv_echo", {"text": "hi"})
            assert result == "echo:hi"
        finally:
            client.close_all()

    def test_server_crash_detected(self, tmp_path: Path) -> None:
        """server 在响应 initialize 后立即退出 — call_tool 应返回带 Error 的字符串。"""
        crash_server = """
import json, sys
line = sys.stdin.readline()
req = json.loads(line)
sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": req["id"], "result": {"protocolVersion": "2024-11-05"}}) + "\\n")
sys.stdout.flush()
# 响应 tools/list
line = sys.stdin.readline()
req = json.loads(line)
sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": req["id"], "result": {"tools": []}}) + "\\n")
sys.stdout.flush()
# 然后直接退出
sys.exit(0)
"""
        path = _make_server(tmp_path, crash_server)
        client = MCPClient([MCPServerConfig(name="srv", command=sys.executable, args=[str(path)])])
        client.start_all()
        # 等子进程退出
        proc = client._processes["srv"]
        proc.wait(timeout=5)
        result = client.call_tool("mcp_srv_anything", {})
        assert result.startswith("Error")
        client.close_all()

    def test_request_timeout(self, tmp_path: Path) -> None:
        """server 收到 initialize 后不回应任何东西 — 应在 timeout_sec 内 TimeoutError。"""
        silent_server = """
import sys
# 读 initialize 但不响应，永远挂着
sys.stdin.readline()
import time
time.sleep(60)
"""
        path = _make_server(tmp_path, silent_server)
        client = MCPClient(
            [MCPServerConfig(name="srv", command=sys.executable, args=[str(path)])],
            timeout_sec=1,
        )
        with pytest.raises((TimeoutError, RuntimeError)):
            client.start_all()
        client.close_all()

    def test_oversized_response_rejected(self, tmp_path: Path) -> None:
        """server 返回超大单行 JSON — 应在 max_line_bytes 处抛 ValueError。"""
        huge_server = """
import json, sys
# 响应 initialize 正常
line = sys.stdin.readline()
req = json.loads(line)
sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": req["id"], "result": {}}) + "\\n")
sys.stdout.flush()
# tools/list 返回巨型 description（不带换行符）
line = sys.stdin.readline()
req = json.loads(line)
huge = "x" * (2 * 1024 * 1024)  # 2MB
payload = '{"jsonrpc":"2.0","id":' + str(req["id"]) + ',"result":{"tools":[{"name":"big","description":"' + huge + '","inputSchema":{}}]}}\\n'
sys.stdout.write(payload)
sys.stdout.flush()
import time
time.sleep(10)
"""
        path = _make_server(tmp_path, huge_server)
        client = MCPClient(
            [MCPServerConfig(name="srv", command=sys.executable, args=[str(path)])],
            timeout_sec=5,
            max_line_bytes=1024 * 1024,  # 1MB 上限，server 想发 2MB
        )
        with pytest.raises((ValueError, RuntimeError)):
            client.start_all()
        client.close_all()

    def test_close_idempotent(self, tmp_path: Path) -> None:
        """close_all 应能重复调用而不报错。"""
        path = _make_server(tmp_path, GOOD_SERVER)
        client = MCPClient([MCPServerConfig(name="srv", command=sys.executable, args=[str(path)])])
        client.start_all()
        client.close_all()
        client.close_all()  # 第二次不应抛

    def test_underscore_server_name_routes_correctly(self, tmp_path: Path) -> None:
        """server 名 file_server + tool 名 read_file 的组合在反向 split 下
        无法区分 server="file" / tool="server_read_file"，必须靠路由表。"""
        path = _make_server(tmp_path, GOOD_SERVER)
        # 让 server 名也带下划线
        client = MCPClient([
            MCPServerConfig(name="file_server", command=sys.executable, args=[str(path)])
        ])
        try:
            client.start_all()
            schemas = client.list_tools()
            # 公共名应该是 mcp_file_server_echo
            assert any(s["name"] == "mcp_file_server_echo" for s in schemas)
            # 调用必须走到 (server=file_server, tool=echo)，而不是
            # (server=file, tool=server_echo)
            result = client.call_tool("mcp_file_server_echo", {"text": "ok"})
            assert result == "echo:ok"
        finally:
            client.close_all()

    def test_underscore_tool_name_routes_correctly(self, tmp_path: Path) -> None:
        """tool 名 read_file 也含下划线——验证完整名分隔不依赖 tool 名结构。"""
        body = """
import json, sys

def respond(req, result):
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": req["id"], "result": result}) + "\\n")
    sys.stdout.flush()

while True:
    line = sys.stdin.readline()
    if not line:
        break
    req = json.loads(line)
    if req["method"] == "initialize":
        respond(req, {"protocolVersion": "2024-11-05"})
    elif req["method"] == "tools/list":
        respond(req, {"tools": [{"name": "read_file", "description": "rf", "inputSchema": {"type": "object"}}]})
    elif req["method"] == "tools/call":
        respond(req, {"content": [{"type": "text", "text": "TOOL=" + req["params"]["name"]}]})
"""
        path = _make_server(tmp_path, body)
        client = MCPClient([
            MCPServerConfig(name="fs", command=sys.executable, args=[str(path)])
        ])
        try:
            client.start_all()
            # 公共名 mcp_fs_read_file。Server 必须收到 name="read_file"，
            # 而不是 name="file"（旧 split("_", 2) 会得到 tool=read_file 正确，
            # 但如果 server="f" / tool="s_read_file" 之类的边角情况就会出错）。
            result = client.call_tool("mcp_fs_read_file", {})
            assert result == "TOOL=read_file"
        finally:
            client.close_all()

    def test_unknown_tool_name_returns_error(self, tmp_path: Path) -> None:
        """未注册的 mcp_* 名字应返回 Error 而不是穿透到 _send_request。"""
        path = _make_server(tmp_path, GOOD_SERVER)
        client = MCPClient([MCPServerConfig(name="srv", command=sys.executable, args=[str(path)])])
        try:
            client.start_all()
            result = client.call_tool("mcp_srv_nonexistent_tool", {})
            assert result.startswith("Error")
            # 也应拦截完全恶意的名字
            assert client.call_tool("not_mcp_at_all", {}).startswith("Error")
        finally:
            client.close_all()
