"""mcp_client.py — MCP（Model Context Protocol）客户端。

对应 Nanobot 的 agent/tools/mcp.py。

MCP 是一种「外挂工具服务器」协议：
    - 每个 MCP Server 是一个独立子进程，通过 stdin/stdout 用 JSON-RPC 通讯。
    - Server 在握手时声明它提供的工具列表（list_tools），
      运行时由 client 通过 call_tool 调用。

我们的极简实现只支持 stdio 传输，不支持 SSE/WebSocket。

config.json 里的配置示例：
    "mcp_servers": {
        "filesystem": {
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
        }
    }
"""

from __future__ import annotations

import json
import subprocess
from typing import Any


class MCPServerConfig:
    """单个 MCP Server 的启动配置（值对象）。"""

    def __init__(self, name: str, command: str, args: list[str]) -> None:
        self.name = name
        self.command = command
        self.args = args


class MCPClient:
    """管理多个 MCP Server 子进程，把它们暴露的工具桥接到 ToolRegistry。"""

    def __init__(self, servers: list[MCPServerConfig], timeout_sec: int = 30) -> None:
        self._servers = servers
        self._processes: dict[str, subprocess.Popen] = {}
        self._tool_cache: dict[str, list[dict[str, Any]]] = {}
        self._request_id = 0
        self.timeout_sec = timeout_sec

    def start_all(self) -> None:
        """启动所有 MCP Server 子进程并完成 initialize 握手。"""
        for srv in self._servers:
            proc = subprocess.Popen(
                [srv.command] + srv.args,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
            )
            self._processes[srv.name] = proc

            # initialize handshake
            self._send_request(srv.name, "initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "minibot", "version": "0.1.0"},
            })

            # fetch tool list
            result = self._send_request(srv.name, "tools/list", {})
            self._tool_cache[srv.name] = result.get("tools", [])

    def close_all(self) -> None:
        """关闭所有子进程。"""
        for name, proc in self._processes.items():
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        self._processes = {}

    # ---------- JSON-RPC ----------

    def _send_request(self, server_name: str, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """向指定 Server 发送一条 JSON-RPC 请求并阻塞等待响应。"""
        proc = self._processes.get(server_name)
        if proc is None:
            raise RuntimeError(f"MCP server '{server_name}' not started")

        self._request_id += 1
        body = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": method,
            "params": params,
        }
        assert proc.stdin is not None
        proc.stdin.write(json.dumps(body) + "\n")
        proc.stdin.flush()

        assert proc.stdout is not None
        while True:
            line = proc.stdout.readline()
            if not line:
                raise RuntimeError(f"MCP server '{server_name}' closed connection unexpectedly")
            resp = json.loads(line.strip())
            if resp.get("id") == self._request_id:
                if "error" in resp:
                    raise RuntimeError(f"MCP error from '{server_name}': {resp['error']}")
                return resp.get("result", {})

    # ---------- 对外 API ----------

    def list_tools(self) -> list[dict[str, Any]]:
        """汇总所有 MCP Server 的工具，转成 Anthropic tool schema 列表。

        工具名加 mcp_<server>_ 前缀，避免与内置工具同名。
        """
        schemas: list[dict[str, Any]] = []
        for server_name, tools in self._tool_cache.items():
            for tool in tools:
                schemas.append({
                    "name": f"mcp_{server_name}_{tool['name']}",
                    "description": tool.get("description", ""),
                    "input_schema": tool.get("inputSchema", {"type": "object", "properties": {}}),
                })
        return schemas

    def call_tool(self, prefixed_name: str, arguments: dict[str, Any]) -> str:
        """调用一个 MCP 工具，返回字符串结果。

        prefixed_name 形如 "mcp_filesystem_read_file"。
        """
        # Format: mcp_<server>_<tool>  (server name may not contain underscores by convention)
        parts = prefixed_name.split("_", 2)
        if len(parts) < 3 or parts[0] != "mcp":
            return f"Error: invalid MCP tool name '{prefixed_name}'"

        server_name = parts[1]
        tool_name = parts[2]

        try:
            result = self._send_request(server_name, "tools/call", {
                "name": tool_name,
                "arguments": arguments,
            })
        except Exception as exc:
            return f"Error: MCP call failed: {exc}"

        content = result.get("content", [])
        texts = [c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"]
        return "\n".join(texts) if texts else str(result)
