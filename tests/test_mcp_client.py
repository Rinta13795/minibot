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

    def test_multiple_responses_in_one_write_are_not_dropped(self, tmp_path: Path) -> None:
        """server 一次 stdout.write 输出两条完整 JSON-RPC 响应；之前的实现
        只返回第一行、丢弃第二行，导致后续 tools/call 永远等不到响应。"""
        batch_server = """
import json, sys

def handle(req):
    if req["method"] == "initialize":
        return {"protocolVersion": "2024-11-05"}
    if req["method"] == "tools/list":
        # 提前把 tools/call 的响应也一起塞进同一次 write
        # 我们假设 client 后续会用 id=3 调用 tools/call
        list_resp = {"jsonrpc":"2.0","id":req["id"],"result":{"tools":[{"name":"ping","description":"p","inputSchema":{"type":"object"}}]}}
        # 提前塞一条 id=3 的响应进同一次 write
        early_call_resp = {"jsonrpc":"2.0","id":3,"result":{"content":[{"type":"text","text":"PONG"}]}}
        payload = json.dumps(list_resp) + "\\n" + json.dumps(early_call_resp) + "\\n"
        sys.stdout.write(payload)
        sys.stdout.flush()
        return None  # 已手动写出
    if req["method"] == "tools/call":
        # 因为响应已经在 tools/list 时同批发出，这里啥也不发
        return None

while True:
    line = sys.stdin.readline()
    if not line:
        break
    req = json.loads(line)
    result = handle(req)
    if result is not None:
        sys.stdout.write(json.dumps({"jsonrpc":"2.0","id":req["id"],"result":result}) + "\\n")
        sys.stdout.flush()
"""
        path = _make_server(tmp_path, batch_server)
        # 给 tools/call 较短的超时——如果 buffer 修复成功，应立刻从 buffer 读到预存的响应
        client = MCPClient(
            [MCPServerConfig(name="srv", command=sys.executable, args=[str(path)])],
            timeout_sec=3,
        )
        try:
            client.start_all()
            # 第二次请求（tools/call）必须能从 buffer 中拿到 server 提前塞的 id=3 响应
            result = client.call_tool("mcp_srv_ping", {})
            assert result == "PONG"
        finally:
            client.close_all()

    def test_response_plus_notification_in_one_write(self, tmp_path: Path) -> None:
        """常见场景：响应紧跟着一条 notification（id=None），两者在同一次
        write 里。buffer 必须保留 notification 行，再于下一次 readline 中被读出
        并按 id 不匹配规则跳过——而不是把它整个吃掉导致后续响应错位。"""
        notif_server = """
import json, sys

def respond(req, result):
    notif = {"jsonrpc":"2.0","method":"server/log","params":{"msg":"noisy"}}
    resp = {"jsonrpc":"2.0","id":req["id"],"result":result}
    sys.stdout.write(json.dumps(resp) + "\\n" + json.dumps(notif) + "\\n")
    sys.stdout.flush()

while True:
    line = sys.stdin.readline()
    if not line:
        break
    req = json.loads(line)
    if req["method"] == "initialize":
        respond(req, {"protocolVersion": "2024-11-05"})
    elif req["method"] == "tools/list":
        respond(req, {"tools":[{"name":"echo","description":"e","inputSchema":{"type":"object"}}]})
    elif req["method"] == "tools/call":
        respond(req, {"content":[{"type":"text","text":"OK"}]})
"""
        path = _make_server(tmp_path, notif_server)
        client = MCPClient(
            [MCPServerConfig(name="srv", command=sys.executable, args=[str(path)])],
            timeout_sec=3,
        )
        try:
            client.start_all()
            # 上一次 initialize 后，notification 已留在 buffer。
            # tools/list 必须跳过 notification、读到自己的响应。
            schemas = client.list_tools()
            assert any(s["name"] == "mcp_srv_echo" for s in schemas)
            # 再来一次 tools/call，同样验证 buffer 工作
            result = client.call_tool("mcp_srv_echo", {})
            assert result == "OK"
        finally:
            client.close_all()
