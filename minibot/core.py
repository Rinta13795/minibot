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

from pathlib import Path
from typing import Any


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
        """初始化核心引擎。

        Args:
            workspace: 工作目录，MEMORY.md / sessions / skills 都基于此路径。
            config: 已解析的 config.json 字典。
            anthropic_api_key: Anthropic API Key（建议从 env 读，不要硬编码）。
            model: Claude 模型 ID。
            max_iterations: tool_use 循环最大轮数，防死循环。

        TODO:
            - 构造 anthropic.Anthropic 客户端
            - 实例化 ToolRegistry（注入 allowed_paths / cmd_whitelist）
            - 实例化 MemoryStore / SkillsLoader / MCPClient / Scheduler
            - 准备 messages 列表（空，等首条 user 消息进来）
        """
        raise NotImplementedError("TODO: __init__")

    @classmethod
    def from_config(cls, config_path: str | Path) -> "MiniBotCore":
        """从 config.json 路径构造实例的便捷方法。

        Args:
            config_path: config.json 文件路径。

        Returns:
            初始化好的 MiniBotCore 实例。

        TODO:
            - 用 json.load 读 config
            - 从 env 读 ANTHROPIC_API_KEY
            - 调用 __init__
        """
        raise NotImplementedError("TODO: from_config")

    # ---------- system prompt 组装 ----------

    def build_system_prompt(self, active_skills: list[str] | None = None) -> str:
        """组装本次请求的 system prompt。

        拼接顺序（对应 Nanobot 的 ContextBuilder）：
            1. identity（你是 MiniBot ...）
            2. AGENTS.md（项目级行为指南）
            3. MEMORY.md（长期记忆摘要）
            4. 激活的 skills 的 SKILL.md 全文

        Args:
            active_skills: 这一轮要挂载的技能名列表；None 表示只挂 always=true 的。

        Returns:
            最终拼好的 system prompt 字符串。

        TODO: 实现拼接逻辑
        """
        raise NotImplementedError("TODO: build_system_prompt")

    # ---------- 三种调用入口 ----------

    def chat(self, user_message: str) -> str:
        """单轮对话：发一条消息，跑完 tool_use 循环，返回最终回复。

        Args:
            user_message: 用户输入。

        Returns:
            助手的最终文本回复。

        TODO:
            - 把 user_message 追加到 self.messages
            - 调用 self._run_tool_loop()
            - 把最终 assistant 消息文本返回
        """
        raise NotImplementedError("TODO: chat")

    def interactive(self) -> None:
        """多轮交互式对话（REPL）。

        在终端循环 input -> chat -> print，直到用户输入 /exit。

        TODO:
            - while True: 读取输入
            - 处理 /exit /clear /memory 等元命令
            - 否则调用 self.chat 并打印结果
        """
        raise NotImplementedError("TODO: interactive")

    def start(self) -> None:
        """守护进程模式：启动 scheduler，等待 cron 任务触发。

        用于「无人值守」场景：定时巡检、定时汇报等。

        TODO:
            - 启动 self.scheduler.run_forever()
            - 注册 SIGINT/SIGTERM 信号优雅退出
        """
        raise NotImplementedError("TODO: start")

    # ---------- 内部：tool_use 循环 ----------

    def _run_tool_loop(self) -> dict[str, Any]:
        """tool_use 主循环（核心算法）。

        伪代码：
            for i in range(max_iterations):
                resp = anthropic.messages.create(messages, tools, system)
                self.messages.append(assistant_block)
                if resp.stop_reason != "tool_use":
                    return resp
                for tc in resp.tool_calls:
                    result = self.tools.execute(tc.name, tc.input)
                    self.messages.append(tool_result_block)
            raise RuntimeError("max_iterations exceeded")

        Returns:
            最终的 LLM 响应（含 stop_reason="end_turn" 的消息）。

        TODO: 实现循环 + 工具分发 + tool_result 回喂
        """
        raise NotImplementedError("TODO: _run_tool_loop")

    def shutdown(self) -> None:
        """关闭所有后台资源（MCP 子进程、scheduler、文件句柄）。

        TODO:
            - self.mcp_client.close_all()
            - self.scheduler.stop()
            - flush 当前 session 到磁盘
        """
        raise NotImplementedError("TODO: shutdown")
