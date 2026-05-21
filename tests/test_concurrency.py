"""tests/test_concurrency.py — chat() 并发安全测试。

scheduler 触发的定时任务回调会在 threading.Timer 线程里调用 core.chat，
而用户的 interactive REPL 在主线程同时也会调用 chat()。若 messages 列表
没有互斥保护，两条对话的 user/assistant 块会交错，导致下一次 API 请求
出现非法 tool_use 序列或上下文污染。

本文件用并发 chat() 调用 + 慢响应模拟 race window，断言锁让对话保持原子。
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from minibot.core import MiniBotCore


def _text_response(text: str) -> SimpleNamespace:
    block = SimpleNamespace(type="text", text=text)
    return SimpleNamespace(content=[block], stop_reason="end_turn")


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
        max_iterations=5,
    )


def test_chat_lock_serializes_concurrent_calls(core: MiniBotCore) -> None:
    """两个线程同时 chat()，messages 必须保持严格的 user→assistant 配对，
    不能出现 user→user→assistant→assistant 的交错。"""
    barrier = threading.Barrier(2)
    call_count = {"n": 0}
    call_lock = threading.Lock()

    def slow_create(*_args, **_kwargs):
        # 关键 race window：在响应返回前停顿，让另一个线程有机会插队 append
        with call_lock:
            call_count["n"] += 1
            n = call_count["n"]
        time.sleep(0.05)
        return _text_response(f"reply-{n}")

    with patch.object(core._client.messages, "create", side_effect=slow_create):
        def worker(msg: str) -> None:
            barrier.wait()  # 让两个线程同时进入 chat()
            core.chat(msg)

        t1 = threading.Thread(target=worker, args=("hello-1",))
        t2 = threading.Thread(target=worker, args=("hello-2",))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

    # 4 条 message：user1, assistant1, user2, assistant2（顺序由谁先抢到锁决定）
    assert len(core.messages) == 4
    # 严格交替：偶数索引 user，奇数索引 assistant
    for i, m in enumerate(core.messages):
        expected = "user" if i % 2 == 0 else "assistant"
        assert m["role"] == expected, f"messages[{i}] role={m['role']}, expected {expected}"


def test_clear_during_concurrent_chat_does_not_corrupt(core: MiniBotCore) -> None:
    """REPL 用户输入 /clear 与 scheduler 触发的 chat() 并发，clear 必须等
    chat() 跑完再清空，而不是把 chat 的 user-block 留下后清掉 assistant。"""
    started = threading.Event()
    finished = threading.Event()

    def slow_create(*_args, **_kwargs):
        started.set()
        # 阻塞，给主线程发起 /clear 的窗口
        time.sleep(0.1)
        return _text_response("done")

    def clear_under_lock() -> None:
        # 模拟 interactive 里 /clear 分支的行为（已加锁）
        with core._chat_lock:
            core.messages = []
        finished.set()

    with patch.object(core._client.messages, "create", side_effect=slow_create):
        chat_thread = threading.Thread(target=core.chat, args=("hello",))
        chat_thread.start()
        started.wait(timeout=1.0)
        # chat 正在执行中，发起 clear
        clear_thread = threading.Thread(target=clear_under_lock)
        clear_thread.start()
        chat_thread.join(timeout=2.0)
        clear_thread.join(timeout=2.0)

    assert finished.is_set()
    # chat 正常完成（user+assistant 两条），随后 clear 把整个列表清空
    assert core.messages == []
