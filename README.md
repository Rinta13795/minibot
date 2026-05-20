# MiniBot

> 极简 AI Agent 框架 — Nanobot 的精简学习版（约 1/30 代码量）。

MiniBot 把 Nanobot 的核心架构剥到只剩骨架：上下文组装、工具循环、长期记忆、技能加载、MCP 桥接、定时调度。读完这份代码你就能讲清楚一个 Agent 框架到底由什么组成。

后续可以照着这个骨架长出运维助手、客服 Bot、知识库问答等具体应用。

---

## 1. 架构图

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                                                                              │
│  ┌────────────┐    ┌──────────────────────────────────────────────────────┐  │
│  │   cli.py   │ ─▶ │                       core.py                        │  │
│  │            │    │              （编排 / tool_use 循环）                 │  │
│  │ chat       │    │                                                      │  │
│  │ interactive│    │   ┌──────────────────────────────────────────────┐   │  │
│  │ start      │    │   │   build_system_prompt()                      │   │  │
│  └────────────┘    │   │   ↓                                          │   │  │
│                    │   │   Anthropic.messages.create(messages, tools) │   │  │
│                    │   │   ↓                                          │   │  │
│                    │   │   stop_reason == "tool_use" ?  yes ──┐       │   │  │
│                    │   │           │ no                       │       │   │  │
│                    │   │           ▼                          ▼       │   │  │
│                    │   │       return reply         ToolRegistry.exec │   │  │
│                    │   │                                    │         │   │  │
│                    │   │                                    └────┐    │   │  │
│                    │   └─────────────────────────────────────────┼───┘   │  │
│                    │                                             │       │  │
│                    │                                             ▼       │  │
│                    │                              (tool_result 回喂到 messages) │
│                    └──────────────────────────────────────────────────────┘  │
│                                          │                                   │
│         ┌───────────────┬────────────────┼────────────────┬──────────────┐   │
│         ▼               ▼                ▼                ▼              ▼   │
│  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐ ┌─────────────┐ ┌──────────┐│
│  │  tools.py   │ │  memory.py  │ │  skills.py  │ │mcp_client.py│ │scheduler ││
│  │             │ │             │ │             │ │             │ │   .py    ││
│  │ ExecTool    │ │ MEMORY.md   │ │ skills/*/   │ │ stdio JSON  │ │ cron.json││
│  │ ReadFile    │ │ 按 section  │ │ SKILL.md    │ │ -RPC 子进程 │ │ croniter ││
│  │ WriteFile   │ │ 读写        │ │ frontmatter │ │             │ │ 定时触发 ││
│  │ +白名单     │ │             │ │ 注入 prompt │ │             │ │          ││
│  └─────────────┘ └─────────────┘ └─────────────┘ └─────────────┘ └──────────┘│
│                                                                              │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. 数据流（一次对话的完整链路）

```
用户输入 "帮我读 README.md"
    │
    ▼
cli.cmd_chat → core.chat(message)
    │
    ▼
core 把 message 追加到 self.messages（user 消息块）
    │
    ▼
core.build_system_prompt():
    identity ＋ AGENTS.md ＋ memory.get_context_block() ＋ skills.build_skills_block(active)
    │
    ▼
core._run_tool_loop():
    for i in range(max_iter):
        resp = anthropic.messages.create(
            system=system_prompt,
            messages=self.messages,
            tools=tools.get_schemas() + mcp.list_tools(),
        )
        self.messages.append(assistant_block)         ← 含 tool_use
        if resp.stop_reason != "tool_use":
            return resp                               ← 跳出循环
        for tc in resp.tool_calls:
            if tc.name.startswith("mcp_"):
                result = mcp.call_tool(tc.name, tc.input)
            else:
                result = tools.execute(tc.name, tc.input)
            self.messages.append(tool_result_block)   ← 把结果回喂
    │
    ▼
返回最终 assistant 文本
    │
    ▼
cli 打印到 stdout
```

---

## 3. 模块职责一览

| 模块            | 行数目标 | 职责                                                                 |
| --------------- | -------- | -------------------------------------------------------------------- |
| `core.py`       | ~200     | 串联其它模块；组装 system prompt；跑 tool_use 循环                     |
| `tools.py`      | ~250     | 三个内置工具（exec/read_file/write_file）+ Tool 基类 + ToolRegistry  |
| `memory.py`     | ~150     | 读写 MEMORY.md，按 section 切分                                      |
| `skills.py`     | ~120     | 扫描 `skills/*/SKILL.md`，解析 frontmatter，按需注入 system prompt   |
| `mcp_client.py` | ~200     | 启动 MCP 子进程，stdin/stdout JSON-RPC，桥接外部工具                  |
| `scheduler.py`  | ~150     | cron 任务持久化与触发；触发时调用 core.chat                          |
| `cli.py`        | ~80      | argparse 子命令：chat / interactive / start                          |

设计原则：
- **单一职责**：每个模块只解决一件事，只通过 `core.py` 互相串联。
- **安全前置**：白名单（`cmd_whitelist`）和路径约束（`allowed_paths`）在工具实例化时就注入，不依赖运行时再判断。
- **接口先行**：所有公共方法有 type hints + docstring，先把接口定下来，实现可以后补。

---

## 4. config.json 完整示例

```json
{
  "workspace": "./workspace",
  "model": "claude-sonnet-4-5",
  "max_iterations": 10,

  "identity": "你是 MiniBot，一个谨慎、礼貌、擅长查文档的助手。",

  "tools": {
    "exec": {
      "enabled": true,
      "cmd_whitelist": ["ls", "cat", "grep", "find", "python3", "git"],
      "timeout_sec": 30
    },
    "read_file": {
      "enabled": true,
      "allowed_paths": [
        "./workspace",
        "./docs"
      ],
      "max_bytes": 1000000
    },
    "write_file": {
      "enabled": true,
      "allowed_paths": ["./workspace"],
      "forbidden_extensions": [".sh", ".py", ".exe"]
    }
  },

  "skills": {
    "skills_dir": "./skills",
    "disabled": []
  },

  "memory": {
    "memory_file": "./workspace/MEMORY.md"
  },

  "mcp_servers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "./workspace"]
    }
  },

  "scheduled_tasks": [
    {
      "id": "morning_brief",
      "cron": "0 9 * * *",
      "prompt": "汇报昨天 workspace 里新增的文件",
      "enabled": true
    }
  ]
}
```

环境变量（不放进 config）：

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

---

## 5. 快速上手（实现完成后）

```bash
pip install -r requirements.txt

# 单轮
python -m minibot --config config.json chat "你好"

# REPL
python -m minibot --config config.json interactive

# 守护进程（含 cron）
python -m minibot --config config.json start
```

---

## 6. 目录结构

```
minibot/
├── README.md                  ← 你正在读的文件
├── AGENTS.md                  ← 项目级行为指南（会被注入 system prompt）
├── requirements.txt
├── config.example.json
├── minibot/                   ← Python 包
│   ├── __init__.py
│   ├── __main__.py
│   ├── core.py
│   ├── tools.py
│   ├── memory.py
│   ├── skills.py
│   ├── mcp_client.py
│   ├── scheduler.py
│   └── cli.py
├── skills/                    ← 用户自定义技能
│   └── greeting/
│       └── SKILL.md
└── tests/
    ├── test_tools.py
    ├── test_memory.py
    └── test_security.py
```

---

## 7. 后续怎么改造成具体项目

| 目标项目     | 怎么改造                                                                       |
| ------------ | ------------------------------------------------------------------------------ |
| 运维助手     | tools 加 ssh 工具；scheduler 加每日巡检；skills 加 incident-response/         |
| 客服 Bot     | tools 替换为「查订单/查物流」HTTP 工具；memory 按用户 ID 隔离；接 channels    |
| 知识库问答   | tools 加 vector_search；skills 加 retrieval-qa/；read_file 指向知识库目录     |

---

## 8. 学习路径建议

1. 先读 `core.py` 的 docstring，理解骨架。
2. 读 `tools.py`，看「工具」是怎么抽象的。
3. 读 `memory.py` 和 `skills.py`，理解 system prompt 怎么被「丰富」起来。
4. 读 `mcp_client.py` 和 `scheduler.py`，看怎么把外部能力（工具/时间）接入。
5. 最后回到 `core._run_tool_loop()`，把整条数据流串起来。
