"""core.py — MiniBot 核心引擎。

职责（参考 Nanobot 的 agent/loop.py + agent/context.py + agent/runner.py）：
    1. 加载 config.json，初始化各子模块（tools/memory/skills/mcp/scheduler）。
    2. 组装 system prompt：identity + AGENTS.md + MEMORY.md + 激活的 skills。
    3. 调用 Anthropic Messages API，进入 tool_use 循环：
           LLM → tool_calls? → 执行工具 → 把结果回喂 → 继续直到 stop。
    4. 把对话历史落盘（简化版 Session），方便下次唤起。

设计理念：
    - 所有 I/O 副作用都放在子模块里，core.py 只编排流程。
    - 安全约束（白名单/路径限制）通过 config 注入到 ToolRegistry，core 本身不判定。
"""

from __future__ import annotations

import json
import os
import signal
import time
from pathlib import Path
from typing import Any

import anthropic

from minibot.memory import MemoryStore
from minibot.mcp_client import MCPClient, MCPServerConfig
from minibot.scheduler import CronJob, Scheduler
from minibot.skills import SkillsLoader
from minibot.tools import ExecTool, ReadFileTool, ToolRegistry, WriteFileTool


class MiniBotCore:
    """框架的总入口，负责把所有子模块粘合起来。

    典型生命周期：
        core = MiniBotCore.from_config("config.json")
        reply = core.chat("帮我读一下 README.md")
        core.shutdown()
    """

    def __init__(
        self,
        workspace: Path,
        config: dict[str, Any],
        anthropic_api_key: str,
        model: str = "claude-sonnet-4-5",
        max_iterations: int = 10,
    ) -> None:
        self.workspace = workspace
        self.config = config
        self.model = model
        self.max_iterations = max_iterations

        # Anthropic client
        self._client = anthropic.Anthropic(api_key=anthropic_api_key)

        # Tool registry
        self.tools = ToolRegistry()
        self._register_tools(config.get("tools", {}), workspace)

        # Memory
        self.memory = MemoryStore(workspace)

        # Skills — 支持 skills_dirs (list) 或 skills_dir (single, 向后兼容)
        skills_cfg = config.get("skills", {})
        skills_dirs_raw = skills_cfg.get("skills_dirs")
        if skills_dirs_raw is None:
            skills_dirs_raw = [skills_cfg.get("skills_dir", "./skills")]
        elif isinstance(skills_dirs_raw, str):
            skills_dirs_raw = [skills_dirs_raw]
        skills_dirs = [self._resolve_path(p, workspace) for p in skills_dirs_raw]
        self.skills = SkillsLoader(skills_dirs)

        # MCP client
        mcp_servers = [
            MCPServerConfig(name=name, command=srv["command"], args=srv.get("args", []))
            for name, srv in config.get("mcp_servers", {}).items()
        ]
        self.mcp_client = MCPClient(servers=mcp_servers)
        if mcp_servers:
            self.mcp_client.start_all()

        # Scheduler
        self.scheduler = Scheduler(
            workspace=workspace,
            on_trigger=lambda job: self.chat(job.prompt),
        )
        for task in config.get("scheduled_tasks", []):
            self.scheduler.add_job(CronJob(
                id=task["id"],
                cron=task["cron"],
                prompt=task["prompt"],
                enabled=task.get("enabled", True),
            ))

        # Multi-turn conversation history
        self.messages: list[dict[str, Any]] = []

        # AGENTS.md — search workspace parent, then cwd
        self._agents_md_path = self._find_agents_md(workspace)

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _resolve_path(path_str: str, workspace: Path) -> Path:
        p = Path(path_str)
        if p.is_absolute():
            return p
        candidate = workspace / p
        if candidate.exists():
            return candidate
        return p  # fall back to cwd-relative; SkillsLoader handles missing dirs

    @staticmethod
    def _find_agents_md(workspace: Path) -> Path:
        for candidate in [workspace.parent / "AGENTS.md", Path("AGENTS.md")]:
            if candidate.exists():
                return candidate
        return workspace.parent / "AGENTS.md"

    def _register_tools(self, tools_cfg: dict[str, Any], workspace: Path) -> None:
        def resolve_paths(raw: list[str]) -> list[Path]:
            result = []
            for s in raw:
                p = Path(s)
                result.append(p if p.is_absolute() else workspace / p)
            return result

        exec_cfg = tools_cfg.get("exec", {})
        if exec_cfg.get("enabled", False):
            self.tools.register(ExecTool(
                cmd_whitelist=exec_cfg.get("cmd_whitelist", []),
                workspace=workspace,
                timeout_sec=exec_cfg.get("timeout_sec", 30),
            ))

        read_cfg = tools_cfg.get("read_file", {})
        if read_cfg.get("enabled", False):
            self.tools.register(ReadFileTool(
                allowed_paths=resolve_paths(read_cfg.get("allowed_paths", [])),
                max_bytes=read_cfg.get("max_bytes", 1_000_000),
            ))

        write_cfg = tools_cfg.get("write_file", {})
        if write_cfg.get("enabled", False):
            self.tools.register(WriteFileTool(
                allowed_paths=resolve_paths(write_cfg.get("allowed_paths", [])),
                forbidden_extensions=write_cfg.get("forbidden_extensions"),
            ))

    # ------------------------------------------------------------------ factory

    @classmethod
    def from_config(cls, config_path: str | Path) -> "MiniBotCore":
        """从 config.json 路径构造实例的便捷方法。"""
        config_path = Path(config_path)
        with open(config_path, encoding="utf-8") as f:
            config = json.load(f)

        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        model = config.get("model", "claude-sonnet-4-5")
        max_iterations = config.get("max_iterations", 10)

        workspace_str = config.get("workspace", "./workspace")
        workspace = Path(workspace_str)
        if not workspace.is_absolute():
            workspace = config_path.parent / workspace_str
        workspace.mkdir(parents=True, exist_ok=True)

        return cls(
            workspace=workspace,
            config=config,
            anthropic_api_key=api_key,
            model=model,
            max_iterations=max_iterations,
        )

    # ------------------------------------------------------------------ system prompt

    def build_system_prompt(self, active_skills: list[str] | None = None) -> str:
        """组装本次请求的 system prompt。

        拼接顺序：
            1. identity（config.json 里的 identity 字段）
            2. AGENTS.md（项目级行为指南）
            3. MEMORY.md 摘要（长期记忆）
            4. 激活的 skills 的 SKILL.md 全文
        """
        parts: list[str] = []

        # 1. Identity
        identity = self.config.get("identity", "You are MiniBot, a helpful AI assistant.")
        parts.append(identity)

        # 2. AGENTS.md
        if self._agents_md_path.exists():
            agents_content = self._agents_md_path.read_text(encoding="utf-8").strip()
            if agents_content:
                parts.append(agents_content)

        # 3. Memory summary
        memory_block = self.memory.get_context_block()
        if memory_block:
            parts.append(memory_block)

        # 4. Skills (always-skills if not specified)
        if active_skills is None:
            active_skills = self.skills.get_always_skills()
        skills_block = self.skills.build_skills_block(active_skills)
        if skills_block:
            parts.append(skills_block)

        return "\n\n".join(parts)

    # ------------------------------------------------------------------ public API

    def chat(self, user_message: str) -> str:
        """单轮对话：发一条消息，跑完 tool_use 循环，返回最终回复。"""
        self.messages.append({"role": "user", "content": user_message})
        response = self._run_tool_loop()

        text_parts: list[str] = []
        for block in response.content:
            if hasattr(block, "text"):
                text_parts.append(block.text)
        return "\n".join(text_parts)

    def interactive(self) -> None:
        """多轮交互式对话（REPL）。"""
        print("MiniBot ready. Commands: /exit  /clear  /memory")
        while True:
            try:
                user_input = input("\nYou: ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nExiting.")
                break

            if not user_input:
                continue
            if user_input == "/exit":
                break
            if user_input == "/clear":
                self.messages = []
                print("Conversation cleared.")
                continue
            if user_input == "/memory":
                print(self.memory.read_all() or "(empty)")
                continue

            try:
                reply = self.chat(user_input)
                print(f"\nMiniBot: {reply}")
            except Exception as exc:
                print(f"\nError: {exc}")

        self.shutdown()

    def start(self) -> None:
        """守护进程模式：启动 scheduler，等待 cron 任务触发。"""
        def _handle_signal(signum, frame):
            print("\nShutting down MiniBot daemon...")
            self.shutdown()
            raise SystemExit(0)

        signal.signal(signal.SIGINT, _handle_signal)
        signal.signal(signal.SIGTERM, _handle_signal)
        print("MiniBot daemon started. Waiting for scheduled tasks...")
        self.scheduler.run_forever()

    # ------------------------------------------------------------------ tool loop

    def _run_tool_loop(self) -> Any:
        """tool_use 主循环。

        反复调用 API → 检查 stop_reason → 若为 tool_use 则执行工具并把结果回喂，
        直到模型返回 end_turn 或达到 max_iterations 上限。
        """
        system_prompt = self.build_system_prompt()
        tool_schemas = self.tools.get_schemas() + self.mcp_client.list_tools()

        for _ in range(self.max_iterations):
            response = self._call_api_with_retry(system_prompt, tool_schemas)

            # Append assistant turn to history
            self.messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason != "tool_use":
                return response

            # Collect and execute all tool calls in this turn
            tool_results: list[dict[str, Any]] = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                if block.name.startswith("mcp_"):
                    result = self.mcp_client.call_tool(block.name, block.input)
                else:
                    result = self.tools.execute(block.name, block.input)

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result,
                })

            self.messages.append({"role": "user", "content": tool_results})

        raise RuntimeError(f"tool_use loop exceeded max_iterations={self.max_iterations}")

    def _call_api_with_retry(self, system_prompt: str, tool_schemas: list[dict[str, Any]]) -> Any:
        """调用 Anthropic API，失败时最多重试 3 次（指数退避）。"""
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                kwargs: dict[str, Any] = {
                    "model": self.model,
                    "max_tokens": 4096,
                    "system": system_prompt,
                    "messages": self.messages,
                }
                if tool_schemas:
                    kwargs["tools"] = tool_schemas
                return self._client.messages.create(**kwargs)
            except Exception as exc:
                last_exc = exc
                if attempt < 2:
                    time.sleep(2 ** attempt)
        raise RuntimeError(f"API call failed after 3 attempts: {last_exc}") from last_exc

    # ------------------------------------------------------------------ shutdown

    def shutdown(self) -> None:
        """关闭所有后台资源（MCP 子进程、scheduler）。"""
        try:
            self.mcp_client.close_all()
        except Exception:
            pass
        try:
            self.scheduler.stop()
        except Exception:
            pass
