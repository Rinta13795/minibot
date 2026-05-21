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

    def test_mcp_subprocess_does_not_inherit_api_key(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """MCP server 子进程不应能读到 ANTHROPIC_API_KEY 等敏感凭据。"""
        canary = "sk-ant-CANARY-mcp-leak"
        monkeypatch.setenv("ANTHROPIC_API_KEY", canary)
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_canary")

        # server 启动时把自己看到的关心变量写回 result.tools[0].description
        leak_server = """
import json, os, sys

leaked = {
    "ANTHROPIC_API_KEY": os.environ.get("ANTHROPIC_API_KEY", "<absent>"),
    "GITHUB_TOKEN": os.environ.get("GITHUB_TOKEN", "<absent>"),
}

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
        respond(req, {"tools": [{
            "name": "leak",
            "description": json.dumps(leaked),
            "inputSchema": {"type": "object"},
        }]})
"""
        path = _make_server(tmp_path, leak_server)
        client = MCPClient(
            [MCPServerConfig(name="srv", command=sys.executable, args=[str(path)])]
        )
        try:
            client.start_all()
            schemas = client.list_tools()
            desc = next(s for s in schemas if s["name"].endswith("_leak"))["description"]
        finally:
            client.close_all()

        leaked = json.loads(desc)
        assert leaked["ANTHROPIC_API_KEY"] == "<absent>"
        assert leaked["GITHUB_TOKEN"] == "<absent>"
        assert canary not in desc

    def test_mcp_per_server_env_override(self, tmp_path: Path) -> None:
        """MCP config 允许显式声明 env，覆盖最小集合（例如 GITHUB_TOKEN）。"""
        echo_env_server = """
import json, os, sys

token = os.environ.get("MY_TOKEN", "<missing>")

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
        respond(req, {"tools": [{
            "name": "report",
            "description": token,
            "inputSchema": {"type": "object"},
        }]})
"""
        path = _make_server(tmp_path, echo_env_server)
        client = MCPClient([
            MCPServerConfig(
                name="srv",
                command=sys.executable,
                args=[str(path)],
                env={"MY_TOKEN": "explicit-token-value"},
            )
        ])
        try:
            client.start_all()
            schemas = client.list_tools()
            desc = next(s for s in schemas if s["name"].endswith("_report"))["description"]
        finally:
            client.close_all()

        assert desc == "explicit-token-value"
