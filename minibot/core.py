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
        max_history_messages: int = 40,
        max_tool_result_chars: int = 20_000,
        max_aggregate_tool_result_chars: int = 80_000,
    ) -> None:
        self.workspace = workspace
        self.config = config
        self.model = model
        self.max_iterations = max_iterations
        # 滑动窗口上限——超过会在每次 API 调用前触发 _compact_messages。
        # interactive 长会话 / 巨型工具输出 / 多次 MCP 大响应都会堆积
        # messages，最终撞 Anthropic 的 context window 限额报错。
        self.max_history_messages = max_history_messages
        # 单条工具结果字符上限。仅按消息数滑动窗口不够——一次 MCP 响应
        # 最大可达 16MB（MCP 自身的 max_line_bytes），单条就能撑爆 context
        # window。ReadFileTool 内部已有 max_bytes，但 ExecTool stdout 和
        # MCP call_tool 结果不受限。在这里统一截断作为最后一道兜底。
        self.max_tool_result_chars = max_tool_result_chars
        # 单次 assistant 转响里**所有** tool_result 字符总和的上限。
        # 模型一次可以发起多个 tool_use 块，全部 tool_result 进同一条
        # user message——单条 cap 限不住聚合（N × per_cap 仍可任意大）。
        # 注：tool_use_id 必须 1:1 对应 tool_result，不能跳过；超额时只能
        # 把每个 result 进一步截到 cap / N。
        self.max_aggregate_tool_result_chars = max_aggregate_tool_result_chars

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
        max_history_messages = config.get("max_history_messages", 40)
        max_tool_result_chars = config.get("max_tool_result_chars", 20_000)
        max_aggregate_tool_result_chars = config.get(
            "max_aggregate_tool_result_chars", 80_000
        )

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
            max_history_messages=max_history_messages,
            max_tool_result_chars=max_tool_result_chars,
            max_aggregate_tool_result_chars=max_aggregate_tool_result_chars,
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
        """多轮交互式对话（REPL）。

        元命令：
            /quit, /exit   退出
            /clear         清空对话历史
            /memory        打印 MEMORY.md
            /skills        列出已加载的技能（包含 always 标记）
            /help          打印帮助
        """
        print("MiniBot ready. Commands: /quit  /clear  /memory  /skills  /help")
        while True:
            try:
                user_input = input("\nYou: ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nExiting.")
                break

            if not user_input:
                continue
            if user_input in ("/quit", "/exit"):
                break
            if user_input == "/clear":
                self.messages = []
                print("Conversation cleared.")
                continue
            if user_input == "/memory":
                print(self.memory.read_all() or "(empty)")
                continue
            if user_input == "/skills":
                skills = self.skills.list_skills()
                if not skills:
                    print("(no skills loaded)")
                else:
                    for s in skills:
                        mark = "[always]" if s.get("always") else "        "
                        print(f"  {mark} {s['name']:<20} {s.get('description', '')}")
                continue
            if user_input == "/help":
                print("Commands: /quit  /clear  /memory  /skills  /help")
                continue

            try:
                reply = self.chat(user_input)
                print(f"\nMiniBot: {reply}")
            except Exception as exc:
                print(f"\nError: {exc}")

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
            # 每次 API 调用前做滑动窗口截断，避免 messages 无限增长撞
            # Anthropic context window。只在每个迭代开始截断，保证 messages
            # 此刻处于「完整对话单元」状态（user 输入，或 user(tool_result) 收尾）。
            self._compact_messages()
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

                # 单条工具结果再大也别让它独自撑爆 context window。
                result = self._truncate_tool_result(result)

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result,
                })

            # 再施加 aggregate cap：单条 cap 不够防 N × per_cap 聚合爆量。
            tool_results = self._enforce_aggregate_cap(tool_results)
            self.messages.append({"role": "user", "content": tool_results})

        raise RuntimeError(f"tool_use loop exceeded max_iterations={self.max_iterations}")

    def _truncate_text(self, text: str, cap: int) -> str:
        """把 text 截到 ≤ cap 字符以内，保留头尾两端 + 省略提示。

        严格保证 `len(output) <= cap`——这是 _enforce_aggregate_cap 算
        per-result 预算时的关键不变式。否则当 cap 很小时（极大 N 下的
        cap/N），marker 字符串本身的长度可能反而超过 cap，让聚合截断
        无法守住 max_aggregate_tool_result_chars。

        策略：
          - cap 够大 → 头尾两段（3:1）+ 省略 marker
          - cap 装不下 marker → 直接硬截
          - 末尾再做一次 len(out) > cap 的兜底，截到原文前 cap 字符
        """
        if not isinstance(text, str) or cap <= 0 or len(text) <= cap:
            return text

        # 选最短可读 marker（短 marker 让 head/tail 留更多预算）。
        # 含 "truncated" 关键字便于 grep/测试。
        marker_template = "\n[... truncated {} chars ...]\n"
        sample_marker = marker_template.format(len(text))
        marker_len = len(sample_marker)

        if marker_len + 4 >= cap:
            # cap 太小，连 marker 都装不下 → 硬截
            return text[:cap]

        body_budget = cap - marker_len
        head_len = body_budget * 3 // 4
        tail_len = body_budget - head_len
        head = text[:head_len]
        tail = text[-tail_len:] if tail_len > 0 else ""
        omitted = len(text) - head_len - tail_len
        out = f"{head}{marker_template.format(omitted)}{tail}"
        # 安全兜底：理论上不会触发，但保证 len(out) <= cap 是 aggregate
        # 算法依赖的硬约束
        if len(out) > cap:
            return text[:cap]
        return out

    def _truncate_tool_result(self, result: str) -> str:
        """单条工具结果超过 max_tool_result_chars 时截断。"""
        return self._truncate_text(result, self.max_tool_result_chars)

    def _enforce_aggregate_cap(
        self, tool_results: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """一次 turn 内全部 tool_result 聚合**严格** ≤ max_aggregate_tool_result_chars。

        模型可以在同一个 assistant 回复里发出多个 tool_use 块，所有
        tool_result 块进同一条 user message——单条 cap 限不住聚合。

        关键约束：
          - tool_use_id 与 tool_result 必须 1:1 配对（少了 API 400），
            所以永远不能跳过任何 tool_result，只能进一步截短。
          - 不能给 per_budget 设 floor（之前的 max(cap//n, 200) 让
            N*200 > cap），否则极大 N 时会反向超额 cap。
          - 依赖 _truncate_text 严格 ≤ cap 的不变式来保证总和。

        算法：
          1. 算原始 total；不超 cap 则直接返回
          2. 否则 per_budget = cap // n（**无 floor**）
          3. 每个 result 用 _truncate_text 截到 ≤ per_budget
          4. 总和 = sum(min(L_i, per_budget)) ≤ n * per_budget ≤ cap ✓
        """
        cap = self.max_aggregate_tool_result_chars
        if cap <= 0 or not tool_results:
            return tool_results

        def _content_len(r: dict[str, Any]) -> int:
            c = r.get("content")
            return len(c) if isinstance(c, str) else 0

        total = sum(_content_len(r) for r in tool_results)
        if total <= cap:
            return tool_results

        n = len(tool_results)
        # 严格 = cap // n。N 极大时 per_budget 会很小，但 _truncate_text
        # 保证 ≤ cap，所以总和始终 ≤ N * (cap//N) ≤ cap。
        per_budget = max(cap // n, 1)  # 至少 1，避免传入 0 时 hardcut 为空
        adjusted: list[dict[str, Any]] = []
        for r in tool_results:
            content = r.get("content")
            if isinstance(content, str) and len(content) > per_budget:
                content = self._truncate_text(content, per_budget)
            adjusted.append({**r, "content": content})
        return adjusted

    def _compact_messages(self) -> None:
        """滑动窗口截断：当 messages 超过 max_history_messages 时丢弃旧消息。

        关键正确性约束：Anthropic API 要求 assistant 的 tool_use 块与紧接
        其后的 user tool_result 块必须配对。简单 messages[-N:] 切片可能让
        kept[0] 是一条 user(tool_result) 但对应的 assistant(tool_use) 已被
        丢弃，下次 API 请求立即 HTTP 400。

        正确策略：从理想切点开始向后扫描，找第一条「fresh user 输入」
        （role=user 且 content 是字符串，不是 tool_result 列表）作为安全
        切点；之前的全部丢弃。这样保留的窗口永远从一个完整对话回合开始。

        如果窗口里找不到任何 fresh user（极端情况：一次 tool_use 循环
        产生超过 max_history_messages 条消息），不截断——下一轮 chat 调用
        的新 user 输入会重新提供安全切点。
        """
        if len(self.messages) <= self.max_history_messages:
            return

        # 理想切点：保留尾部 max_history_messages 条
        cut = len(self.messages) - self.max_history_messages

        # 向后找第一条 fresh user 输入
        while cut < len(self.messages):
            msg = self.messages[cut]
            if msg.get("role") == "user" and isinstance(msg.get("content"), str):
                break
            cut += 1

        if cut <= 0 or cut >= len(self.messages):
            return  # 无可丢弃 / 无安全切点
        self.messages = self.messages[cut:]

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
