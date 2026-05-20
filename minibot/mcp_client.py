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

import subprocess
from typing import Any


class MCPServerConfig:
    """单个 MCP Server 的启动配置（值对象）。"""

    def __init__(self, name: str, command: str, args: list[str]) -> None:
        """记录启动一个 MCP Server 所需的全部信息。

        Args:
            name: 服务器名（用作工具名前缀，如 "mcp_filesystem_read_file"）。
            command: 可执行命令，如 "npx" / "python3"。
            args: 命令行参数。

        TODO: 简单 assign
        """
        raise NotImplementedError("TODO: __init__")


class MCPClient:
    """管理多个 MCP Server 子进程，把它们暴露的工具桥接到 ToolRegistry。"""

    def __init__(self, servers: list[MCPServerConfig], timeout_sec: int = 30) -> None:
        """初始化 MCP 客户端。

        Args:
            servers: 要启动的 MCP 服务器列表。
            timeout_sec: 单次 RPC 调用超时。

        TODO:
            - self._servers = servers
            - self._processes: dict[str, subprocess.Popen] = {}
            - self._tool_cache: dict[str, list[dict]] = {}  # server_name -> tools
            - self._request_id = 0
        """
        raise NotImplementedError("TODO: __init__")

    def start_all(self) -> None:
        """启动所有 MCP Server 子进程并完成 initialize 握手。

        TODO:
            - 遍历 servers，用 subprocess.Popen 启动每个进程
            - stdin/stdout 配 PIPE，stderr 走 DEVNULL（或单独日志）
            - 发送 initialize JSON-RPC 请求，等握手响应
            - 发送 tools/list 拉取工具清单，存到 self._tool_cache
        """
        raise NotImplementedError("TODO: start_all")

    def close_all(self) -> None:
        """关闭所有子进程。

        TODO:
            - 给每个 process.stdin 发送 shutdown 通知
            - process.terminate() + wait(timeout)
            - 超时则 kill
        """
        raise NotImplementedError("TODO: close_all")

    # ---------- JSON-RPC ----------

    def _send_request(self, server_name: str, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """向指定 Server 发送一条 JSON-RPC 请求并阻塞等待响应。

        Args:
            server_name: 目标 server 名（必须已 start）。
            method: JSON-RPC 方法名，如 "tools/call"。
            params: 参数对象。

        Returns:
            响应的 "result" 字段。

        TODO:
            - self._request_id += 1
            - body = {"jsonrpc":"2.0","id":...,"method":...,"params":...}
            - proc.stdin.write(json.dumps(body) + "\\n") + flush
            - 循环 readline，找到 id 匹配的响应
            - 解析 "result" / "error"，error 时 raise
        """
        raise NotImplementedError("TODO: _send_request")

    # ---------- 对外 API ----------

    def list_tools(self) -> list[dict[str, Any]]:
        """汇总所有 MCP Server 的工具，转成 Anthropic tool schema 列表。

        Returns:
            [{"name": "mcp_filesystem_read_file", "description": ..., "input_schema": ...}, ...]
            注意：name 加 mcp_<server>_ 前缀，避免和内置工具同名。

        TODO:
            - 遍历 self._tool_cache
            - 每个工具构造前缀化的 schema
        """
        raise NotImplementedError("TODO: list_tools")

    def call_tool(self, prefixed_name: str, arguments: dict[str, Any]) -> str:
        """调用一个 MCP 工具，返回字符串结果。

        Args:
            prefixed_name: 形如 "mcp_filesystem_read_file"。
            arguments: 工具参数。

        Returns:
            工具返回的文本内容（多个 content 块合并）。

        TODO:
            - 从 prefixed_name 解析出 server_name 和 original_tool_name
            - _send_request("tools/call", {"name": ..., "arguments": ...})
            - 把 content 列表里的 text 块拼起来返回
        """
        raise NotImplementedError("TODO: call_tool")
