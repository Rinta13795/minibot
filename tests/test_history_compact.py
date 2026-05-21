"""tests/test_history_compact.py — messages 滑动窗口截断测试。

代码审查严重问题 #3：messages 无限增长撞 context window。
本文件验证 _compact_messages 在不同对话模式下都能安全截断，且不会
留下孤儿 tool_result（即被丢弃的 tool_use 对应的 tool_result）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from minibot.core import MiniBotCore


@pytest.fixture
def core(tmp_path: Path) -> MiniBotCore:
    config = {
        "workspace": str(tmp_path),
        "model": "claude-sonnet-4-5",
        "max_iterations": 5,
        "identity": "test",
        "tools": {
            "exec": {"enabled": False, "cmd_whitelist": []},
            "read_file": {"enabled": False, "allowed_paths": []},
            "write_file": {"enabled": False, "allowed_paths": []},
        },
        "skills": {"skills_dirs": []},
        "memory": {},
        "mcp_servers": {},
        "scheduled_tasks": [],
    }
    return MiniBotCore(
        workspace=tmp_path,
        config=config,
        anthropic_api_key="fake-key",
        max_history_messages=10,
    )


def test_short_conversation_not_truncated(core: MiniBotCore) -> None:
    core.messages = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]
    core._compact_messages()
    assert len(core.messages) == 2


def test_long_text_conversation_truncated_to_fresh_user_boundary(
    core: MiniBotCore,
) -> None:
    # 30 条交替的 user/assistant 文本消息（无 tool_use）
    msgs: list[dict[str, Any]] = []
    for i in range(15):
        msgs.append({"role": "user", "content": f"msg-{i}"})
        msgs.append({"role": "assistant", "content": f"reply-{i}"})
    core.messages = msgs

    core._compact_messages()

    # 应被截断到接近 max_history_messages=10 的尾部
    assert len(core.messages) <= 10
    # 第一条必须是 fresh user 输入
    assert core.messages[0]["role"] == "user"
    assert isinstance(core.messages[0]["content"], str)


def test_truncation_does_not_leave_orphan_tool_result(
    core: MiniBotCore,
) -> None:
    """关键正确性：截断不能让 kept[0] 是 user(tool_result) 而对应的
    assistant(tool_use) 已被丢弃——Anthropic 会立刻返回 HTTP 400。"""
    # 构造一段含 tool_use 的对话：
    #   user(str) → assistant(tool_use) → user(tool_result) → assistant(text)
    # 重复多次让总长超过 max_history_messages
    msgs: list[dict[str, Any]] = []
    for i in range(6):
        msgs.append({"role": "user", "content": f"task-{i}"})
        msgs.append({
            "role": "assistant",
            "content": [{"type": "tool_use", "id": f"call-{i}", "name": "x", "input": {}}],
        })
        msgs.append({
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": f"call-{i}", "content": "ok"}],
        })
        msgs.append({"role": "assistant", "content": f"final-{i}"})
    core.messages = msgs

    core._compact_messages()

    # kept[0] 必须是 fresh user 输入（str content），不能是 tool_result
    first = core.messages[0]
    assert first["role"] == "user"
    assert isinstance(first["content"], str), (
        f"truncation left an orphan tool_result as first kept message: "
        f"role={first['role']}, content_type={type(first['content']).__name__}"
    )


def test_truncation_no_safe_boundary_keeps_all(core: MiniBotCore) -> None:
    """极端情况：一次 tool_use 循环产生超过 max_history_messages 条消息
    （都是 assistant + user(tool_result)，没有 fresh user 输入夹在中间）
    → 不应截断，等下一轮新 user 输入再压缩。"""
    msgs: list[dict[str, Any]] = [{"role": "user", "content": "task"}]  # 仅一条 fresh
    # 之后塞 30 条交替的 assistant(tool_use) / user(tool_result)
    for i in range(15):
        msgs.append({
            "role": "assistant",
            "content": [{"type": "tool_use", "id": f"call-{i}", "name": "x", "input": {}}],
        })
        msgs.append({
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": f"call-{i}", "content": "r"}],
        })
    core.messages = list(msgs)

    core._compact_messages()

    # 理想切点 = len - 10 = 21（在 tool 循环中间）。从 21 向后找 fresh user，
    # 但后面全是 tool_use / tool_result，找不到——不截断。
    assert len(core.messages) == len(msgs)


def test_compact_idempotent(core: MiniBotCore) -> None:
    msgs: list[dict[str, Any]] = []
    for i in range(15):
        msgs.append({"role": "user", "content": f"u{i}"})
        msgs.append({"role": "assistant", "content": f"a{i}"})
    core.messages = msgs

    core._compact_messages()
    first_pass = list(core.messages)
    core._compact_messages()
    assert core.messages == first_pass


def test_config_max_history_messages_passed_through(tmp_path: Path) -> None:
    """from_config 必须把 config["max_history_messages"] 透传给实例。"""
    import json
    config = {
        "workspace": str(tmp_path),
        "model": "test-model",
        "max_iterations": 3,
        "max_history_messages": 7,
        "max_tool_result_chars": 1234,
        "tools": {},
        "skills": {"skills_dirs": []},
        "memory": {},
        "mcp_servers": {},
    }
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps(config), encoding="utf-8")

    core = MiniBotCore.from_config(cfg_path)
    assert core.max_history_messages == 7
    assert core.max_tool_result_chars == 1234


# ============================================================
# 单条工具结果截断
# ============================================================


def test_tool_result_under_cap_unchanged(core: MiniBotCore) -> None:
    core.max_tool_result_chars = 1000
    small = "short output"
    assert core._truncate_tool_result(small) == small


def test_tool_result_over_cap_truncated_with_marker(core: MiniBotCore) -> None:
    core.max_tool_result_chars = 500
    big = "A" * 50_000 + "MIDDLE_MARKER" + "B" * 50_000
    out = core._truncate_tool_result(big)
    assert len(out) < len(big)
    assert len(out) <= core.max_tool_result_chars + 300  # 头尾 + 提示
    assert "truncated" in out.lower()
    # 头部应保留
    assert out.startswith("A" * 10)
    # 尾部应保留
    assert out.endswith("B" * 10)
    # 中间应被截掉
    assert "MIDDLE_MARKER" not in out


def test_aggregate_cap_no_op_when_under_limit(core: MiniBotCore) -> None:
    core.max_aggregate_tool_result_chars = 10_000
    results = [
        {"type": "tool_result", "tool_use_id": "a", "content": "x" * 1000},
        {"type": "tool_result", "tool_use_id": "b", "content": "y" * 1000},
    ]
    out = core._enforce_aggregate_cap(results)
    assert out == results  # 完全不变


def test_aggregate_cap_shrinks_each_result_when_over_limit(
    core: MiniBotCore,
) -> None:
    """10 个工具各返回 20K 字符 = 200K 聚合，超过 80K aggregate cap
    必须把每个 result 进一步截到 ~cap / N = 8K 内。"""
    core.max_aggregate_tool_result_chars = 80_000
    core.max_tool_result_chars = 20_000  # 单条 cap 限不住聚合
    results = [
        {"type": "tool_result", "tool_use_id": f"id-{i}", "content": "X" * 20_000}
        for i in range(10)
    ]
    out = core._enforce_aggregate_cap(results)
    # 每条 tool_use_id 都必须仍有对应 tool_result（API 1:1 约束）
    assert len(out) == 10
    assert [r["tool_use_id"] for r in out] == [r["tool_use_id"] for r in results]
    # 聚合大小应在 cap 附近（含每条的省略提示开销）
    total = sum(len(r["content"]) for r in out)
    assert total <= core.max_aggregate_tool_result_chars * 2  # 含 marker 开销
    # 每条都应被截短
    for r in out:
        assert len(r["content"]) < 20_000
        assert "truncated" in r["content"].lower()


def test_aggregate_cap_preserves_minimum_per_result(core: MiniBotCore) -> None:
    """N 极大时 cap / N 会非常小；下限 200 字符保证每个 tool_use_id
    至少有可读响应，不会被压成空串。"""
    core.max_aggregate_tool_result_chars = 100  # 故意设很小
    results = [
        {"type": "tool_result", "tool_use_id": f"id-{i}", "content": "X" * 5_000}
        for i in range(50)
    ]
    out = core._enforce_aggregate_cap(results)
    assert len(out) == 50
    # 每条应有内容（至少 truncation marker），不能空
    for r in out:
        assert isinstance(r["content"], str)
        assert len(r["content"]) > 0


def test_tool_result_truncation_in_run_tool_loop(core: MiniBotCore) -> None:
    """端到端：tool 返回巨型字符串时，appended 到 messages 的 tool_result
    content 必须在 cap 范围内（含截断提示几百字符），不能让单条消息撑爆
    context window。"""
    from types import SimpleNamespace
    from unittest.mock import patch

    core.max_tool_result_chars = 200

    # 在 ToolRegistry 里塞一个返回巨大字符串的假工具
    class HugeTool:
        @property
        def name(self) -> str:
            return "huge"

        @property
        def description(self) -> str:
            return "returns a huge blob"

        @property
        def parameters(self) -> dict:
            return {"type": "object", "properties": {}}

        def execute(self, **_kwargs) -> str:
            return "X" * 100_000

        def to_schema(self) -> dict:
            return {"name": self.name, "description": "", "input_schema": self.parameters}

    core.tools.register(HugeTool())

    def fake_create(**_kwargs):
        # 第一次返回 tool_use；第二次基于 tool_result 返回 text
        if not getattr(fake_create, "called", False):
            fake_create.called = True
            block = SimpleNamespace(
                type="tool_use", name="huge", input={}, id="call_huge"
            )
            return SimpleNamespace(content=[block], stop_reason="tool_use")
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text="done")],
            stop_reason="end_turn",
        )

    with patch.object(core._client.messages, "create", side_effect=fake_create):
        core.chat("trigger huge tool")

    # 找到 tool_result 消息
    tool_msgs = [
        m for m in core.messages
        if m["role"] == "user" and isinstance(m["content"], list)
    ]
    assert len(tool_msgs) == 1
    content_blocks = tool_msgs[0]["content"]
    assert len(content_blocks) == 1
    result_text = content_blocks[0]["content"]
    # 必须远小于原始 100KB
    assert len(result_text) <= core.max_tool_result_chars + 300
    assert "truncated" in result_text.lower()
