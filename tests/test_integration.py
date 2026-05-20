"""tests/test_integration.py — 端到端集成测试。

mock 掉 Anthropic SDK，验证以下完整链路：
    用户输入 → CLI → core.chat → system prompt 组装 → API 调用
        → tool_use → 工具执行 → 结果回喂 → 最终文本回复

对应 Task 7 的 5 个验收场景：
    1. 普通对话
    2. 工具调用（read_file 模拟 ls）
    3. 跨调用记忆
    4. 技能注入 system prompt
    5. interactive 元命令
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from minibot.core import MiniBotCore


# ---------- 构造假响应 ----------


def _text_response(text: str) -> SimpleNamespace:
    """模拟 Anthropic 一个 stop_reason=end_turn 的纯文本响应。"""
    block = SimpleNamespace(type="text", text=text)
    return SimpleNamespace(content=[block], stop_reason="end_turn")


def _tool_use_response(tool_name: str, tool_input: dict[str, Any], use_id: str = "call_1") -> SimpleNamespace:
    """模拟一个 stop_reason=tool_use 的响应，要求调用某个工具。"""
    block = SimpleNamespace(type="tool_use", name=tool_name, input=tool_input, id=use_id)
    return SimpleNamespace(content=[block], stop_reason="tool_use")


# ---------- fixture：可用的 minibot 实例 ----------


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "skills" / "greeting").mkdir(parents=True)
    (tmp_path / "skills" / "greeting" / "SKILL.md").write_text(
        "---\nname: greeting\ndescription: 问候\nalways: true\n---\n打招呼时要友好。",
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture
def config(workspace: Path) -> dict[str, Any]:
    return {
        "workspace": str(workspace),
        "model": "claude-sonnet-4-5",
        "max_iterations": 5,
        "identity": "你是 MiniBot。",
        "tools": {
            "exec": {"enabled": False, "cmd_whitelist": []},
            "read_file": {"enabled": True, "allowed_paths": [str(workspace)]},
            "write_file": {"enabled": True, "allowed_paths": [str(workspace)]},
        },
        "skills": {"skills_dirs": [str(workspace / "skills")]},
        "memory": {},
        "mcp_servers": {},
        "scheduled_tasks": [],
    }


@pytest.fixture
def core(workspace: Path, config: dict[str, Any]) -> MiniBotCore:
    return MiniBotCore(
        workspace=workspace,
        config=config,
        anthropic_api_key="fake-key",
        model="claude-sonnet-4-5",
        max_iterations=5,
    )


# ---------- 场景 1：普通对话 ----------


def test_scenario_1_plain_chat(core: MiniBotCore) -> None:
    """普通问候 — LLM 直接返回文本，无工具调用。"""
    with patch.object(core._client.messages, "create", return_value=_text_response("你好！我是 MiniBot。")):
        reply = core.chat("你好，介绍一下你自己")
    assert "MiniBot" in reply
    # messages 应有 user + assistant 两轮
    assert len(core.messages) == 2
    assert core.messages[0]["role"] == "user"
    assert core.messages[1]["role"] == "assistant"


# ---------- 场景 2：工具调用 ----------


def test_scenario_2_tool_use(core: MiniBotCore, workspace: Path) -> None:
    """LLM 请求 read_file 工具 → 我们执行 → 结果回喂 → LLM 给出文本回复。"""
    (workspace / "hello.txt").write_text("world", encoding="utf-8")

    call_count = {"n": 0}

    def fake_create(**kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return _tool_use_response("read_file", {"path": str(workspace / "hello.txt")})
        return _text_response("文件内容是 world。")

    with patch.object(core._client.messages, "create", side_effect=fake_create):
        reply = core.chat("读 hello.txt")

    assert "world" in reply
    # 应该有: user → assistant(tool_use) → user(tool_result) → assistant(text)
    assert len(core.messages) == 4
    # 第三条是 tool_result（user role）
    assert core.messages[2]["role"] == "user"
    tool_results = core.messages[2]["content"]
    assert tool_results[0]["type"] == "tool_result"
    assert "world" in tool_results[0]["content"]


# ---------- 场景 3：跨调用记忆 ----------


def test_scenario_3_memory_persists_across_calls(core: MiniBotCore) -> None:
    """写入 memory 后，下一轮 system prompt 应包含这条记忆。"""
    # 第一轮：手动把名字写入 memory（模拟 LLM 决定记忆的副作用）
    core.memory.write_section("user", "用户的名字叫小陈。")

    captured_systems = []

    def fake_create(**kwargs):
        captured_systems.append(kwargs.get("system", ""))
        return _text_response("你叫小陈。")

    with patch.object(core._client.messages, "create", side_effect=fake_create):
        reply = core.chat("你还记得我叫什么吗？")

    assert "小陈" in reply
    # system prompt 里应该带上 memory 内容
    assert "小陈" in captured_systems[0]


# ---------- 场景 4：技能注入 ----------


def test_scenario_4_skill_in_system_prompt(core: MiniBotCore) -> None:
    """always=true 的 greeting 技能应自动出现在 system prompt 里。"""
    captured_systems = []

    def fake_create(**kwargs):
        captured_systems.append(kwargs.get("system", ""))
        return _text_response("你好！")

    with patch.object(core._client.messages, "create", side_effect=fake_create):
        core.chat("用你的问候技能")

    sys_prompt = captured_systems[0]
    assert "### 技能：greeting" in sys_prompt
    assert "打招呼时要友好" in sys_prompt
    # identity 也应该在
    assert "MiniBot" in sys_prompt


# ---------- 场景 5：interactive 元命令 ----------


def test_scenario_5_interactive_quit(core: MiniBotCore, monkeypatch, capsys) -> None:
    """interactive 输入 /memory 后 /quit 应正常打印 memory 并退出。"""
    core.memory.write_section("user", "测试记忆")
    inputs = iter(["/memory", "/quit"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(inputs))

    core.interactive()
    captured = capsys.readouterr()
    assert "测试记忆" in captured.out


def test_scenario_5_interactive_skills_command(core: MiniBotCore, monkeypatch, capsys) -> None:
    """/skills 应列出加载的技能。"""
    inputs = iter(["/skills", "/quit"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(inputs))

    core.interactive()
    captured = capsys.readouterr()
    assert "greeting" in captured.out


# ---------- 防护：max_iterations 不会让模型死循环 ----------


def test_max_iterations_breaks_infinite_tool_loop(core: MiniBotCore, workspace: Path) -> None:
    """LLM 一直要求工具调用 — 应在 max_iterations 后 RuntimeError，而不是无限调用。"""
    (workspace / "f.txt").write_text("x", encoding="utf-8")

    def always_tool(**kwargs):
        return _tool_use_response("read_file", {"path": str(workspace / "f.txt")})

    with patch.object(core._client.messages, "create", side_effect=always_tool):
        with pytest.raises(RuntimeError, match="max_iterations"):
            core.chat("loop forever")


# ---------- 防护：API 重试 ----------


def test_api_retry_3_times_then_fails(core: MiniBotCore) -> None:
    """API 调用失败应重试 3 次后抛 RuntimeError。"""
    mock = MagicMock(side_effect=RuntimeError("network"))
    with patch.object(core._client.messages, "create", mock):
        with pytest.raises(RuntimeError, match="after 3 attempts"):
            core.chat("anything")
    assert mock.call_count == 3
