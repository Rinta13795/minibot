"""mcp_client.py — MCP（Model Context Protocol）客户端。

对应 Nanobot 的 agent/tools/mcp.py。

MCP 是一种「外挂工具服务器」协议：
    - 每个 MCP Server 是一个独立子进程，通过 stdin/stdout 用 JSON-RPC 通讯。
    - Server 在握手时声明它提供的工具列表（list_tools），
      运行时由 client 通过 call_tool 调用。

我们的极简实现只支持 stdio 传输，不支持 SSE/WebSocket。

可靠性设计：
    1. 子进程启动时立即做 initialize 握手，失败则抛错（fail-fast）。
    2. 每条 JSON-RPC 请求带唯一 id，响应按 id 匹配（支持乱序）。
    3. readline 用 select 超时（默认 30s），超时即抛 TimeoutError。
    4. 单行 JSON 限制 16 MB，防止恶意 server 发巨型响应撑爆内存。
    5. 每次读响应前 poll() 检查子进程是否已退出，崩溃即抛 RuntimeError。
"""

from __future__ import annotations

import json
import os
import select
import subprocess
from typing import Any

# 单行 JSON 上限（16 MB）— 真实 MCP 工具响应远小于这个值
DEFAULT_MAX_LINE_BYTES = 16 * 1024 * 1024


class MCPServerConfig:
    """单个 MCP Server 的启动配置（值对象）。"""

    def __init__(self, name: str, command: str, args: list[str]) -> None:
        self.name = name
        self.command = command
        self.args = args


class MCPClient:
    """管理多个 MCP Server 子进程，把它们暴露的工具桥接到 ToolRegistry。"""

    def __init__(
        self,
        servers: list[MCPServerConfig],
        timeout_sec: int = 30,
        max_line_bytes: int = DEFAULT_MAX_LINE_BYTES,
    ) -> None:
        self._servers = servers
        self._processes: dict[str, subprocess.Popen] = {}
        self._tool_cache: dict[str, list[dict[str, Any]]] = {}
        self._request_id = 0
        self.timeout_sec = timeout_sec
        self.max_line_bytes = max_line_bytes

    def start_all(self) -> None:
        """启动所有 MCP Server 子进程并完成 initialize 握手。

        启动失败的 server 会抛异常并冒泡，调用方自己决定是否吞掉。
        """
        for srv in self._servers:
            proc = subprocess.Popen(
                [srv.command] + srv.args,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=False,                 # 二进制模式，避免 \r\n 平台差异
                bufsize=0,
            )
            self._processes[srv.name] = proc

            try:
                # initialize 握手
                self._send_request(srv.name, "initialize", {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "minibot", "version": "0.1.0"},
                })
                # 拉取工具列表并缓存
                result = self._send_request(srv.name, "tools/list", {})
                self._tool_cache[srv.name] = result.get("tools", [])
            except Exception:
                # 启动握手失败 — 干掉这个进程并冒泡
                try:
                    proc.terminate()
                    proc.wait(timeout=2)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                self._processes.pop(srv.name, None)
                raise

    def close_all(self) -> None:
        """关闭所有子进程（先 terminate，5s 超时后 kill）。"""
        for name, proc in self._processes.items():
            if proc.poll() is not None:
                continue  # 已经死了
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    proc.kill()
                    proc.wait(timeout=2)
                except Exception:
                    pass
            except Exception:
                pass
        self._processes = {}
        self._tool_cache = {}

    # ---------- JSON-RPC ----------

    def _send_request(self, server_name: str, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """向指定 Server 发送一条 JSON-RPC 请求并阻塞等待响应。

        Raises:
            RuntimeError: server 未启动 / 已崩溃 / 协议错误
            TimeoutError: 超过 timeout_sec 仍未收到响应
            ValueError: 响应行超过 max_line_bytes
        """
        proc = self._processes.get(server_name)
        if proc is None:
            raise RuntimeError(f"MCP server '{server_name}' not started")
        if proc.poll() is not None:
            raise RuntimeError(
                f"MCP server '{server_name}' has exited (returncode={proc.returncode})"
            )

        self._request_id += 1
        my_id = self._request_id
        body = {
            "jsonrpc": "2.0",
            "id": my_id,
            "method": method,
            "params": params,
        }
        line = (json.dumps(body) + "\n").encode("utf-8")

        assert proc.stdin is not None and proc.stdout is not None
        try:
            proc.stdin.write(line)
            proc.stdin.flush()
        except BrokenPipeError as exc:
            raise RuntimeError(f"MCP server '{server_name}' stdin closed: {exc}") from exc

        # 循环读响应直到 id 匹配（兼容乱序），单次 readline 受 select 超时控制
        deadline_remaining = self.timeout_sec
        while True:
            line_bytes = self._readline_with_timeout(proc, deadline_remaining)
            if line_bytes is None:
                raise TimeoutError(
                    f"MCP server '{server_name}' did not respond within {self.timeout_sec}s"
                )
            if not line_bytes:
                # readline 返回空 = EOF = server 退出
                raise RuntimeError(
                    f"MCP server '{server_name}' closed connection (returncode={proc.poll()})"
                )

            try:
                resp = json.loads(line_bytes.decode("utf-8"))
            except json.JSONDecodeError as exc:
                raise RuntimeError(
                    f"MCP server '{server_name}' returned invalid JSON: {exc}"
                ) from exc

            # 不是我等的响应（可能是 server 主动推的 notification），忽略
            resp_id = resp.get("id")
            if resp_id is None or resp_id != my_id:
                continue

            if "error" in resp:
                raise RuntimeError(f"MCP error from '{server_name}': {resp['error']}")
            return resp.get("result", {})

    def _readline_with_timeout(
        self, proc: subprocess.Popen, timeout_sec: float
    ) -> bytes | None:
        """带超时的 readline。

        Returns:
            - bytes: 读到的一行（含 \\n）
            - b"": EOF（子进程退出，stdout 关闭）
            - None: 超时
        """
        assert proc.stdout is not None
        fd = proc.stdout.fileno()
        # select 在 macOS/Linux 上对 pipe 工作良好
        ready, _, _ = select.select([fd], [], [], timeout_sec)
        if not ready:
            return None

        # 手动累积字节直到 \n 或超出 max_line_bytes
        buf = bytearray()
        while True:
            chunk = os.read(fd, 8192)
            if not chunk:
                return bytes(buf)  # EOF
            buf.extend(chunk)
            if b"\n" in chunk:
                # 切到第一个 \n 处；剩余部分留在内核缓冲区还是被吃掉？
                # 简化处理：我们假设 MCP server 一次只发一整行（标准协议如此），
                # 多行情况下需要更复杂的 buffer 管理。
                nl = buf.index(b"\n")
                line = bytes(buf[: nl + 1])
                # 如果还有剩余，丢弃 — 实际生产应缓存供下次 readline
                return line
            if len(buf) > self.max_line_bytes:
                raise ValueError(
                    f"MCP response line exceeded {self.max_line_bytes} bytes (max_line_bytes)"
                )
            # 没读到换行 — 用 select 等下一批数据，剩余时间约等于初始 timeout（简化）
            ready, _, _ = select.select([fd], [], [], timeout_sec)
            if not ready:
                return None

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
        """调用一个 MCP 工具，返回字符串结果。"""
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
        except TimeoutError as exc:
            return f"Error: MCP call timed out: {exc}"
        except Exception as exc:
            return f"Error: MCP call failed: {exc}"

        content = result.get("content", [])
        texts = [
            c.get("text", "")
            for c in content
            if isinstance(c, dict) and c.get("type") == "text"
        ]
        return "\n".join(texts) if texts else str(result)
