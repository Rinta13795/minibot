"""cli.py — 命令行入口。

对应 Nanobot 的 cli/commands.py。

三种模式：
    minibot chat "你的问题"         # 单轮问答，stdout 打印回复后退出
    minibot interactive             # 进入 REPL，多轮对话
    minibot start                   # 启动守护进程（含 scheduler）

用 argparse 实现，避免引入 click/typer 额外依赖。
"""

from __future__ import annotations

import argparse
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    """构造命令行解析器。

    Returns:
        配置好的 ArgumentParser，支持 chat / interactive / start 三个子命令。

    TODO:
        - parser = ArgumentParser("minibot")
        - parser.add_argument("--config", default="config.json")
        - sub = parser.add_subparsers(dest="cmd", required=True)
        - chat_p = sub.add_parser("chat"); chat_p.add_argument("message")
        - sub.add_parser("interactive")
        - sub.add_parser("start")
    """
    raise NotImplementedError("TODO: build_parser")


def cmd_chat(config_path: Path, message: str) -> int:
    """处理 `minibot chat` 子命令。

    Args:
        config_path: config.json 路径。
        message: 用户消息。

    Returns:
        exit code：0 成功，非 0 失败。

    TODO:
        - core = MiniBotCore.from_config(config_path)
        - print(core.chat(message))
        - core.shutdown()
        - 异常时打印错误并返回 1
    """
    raise NotImplementedError("TODO: cmd_chat")


def cmd_interactive(config_path: Path) -> int:
    """处理 `minibot interactive` 子命令。

    TODO:
        - core = MiniBotCore.from_config(config_path)
        - core.interactive()
        - core.shutdown()
    """
    raise NotImplementedError("TODO: cmd_interactive")


def cmd_start(config_path: Path) -> int:
    """处理 `minibot start` 子命令（守护进程模式）。

    TODO:
        - core = MiniBotCore.from_config(config_path)
        - core.start()  # 阻塞，直到 Ctrl+C
        - core.shutdown()
    """
    raise NotImplementedError("TODO: cmd_start")


def main(argv: list[str] | None = None) -> int:
    """CLI 总入口。

    Args:
        argv: 命令行参数列表；None 表示用 sys.argv。

    Returns:
        进程 exit code。

    TODO:
        - parser = build_parser()
        - args = parser.parse_args(argv)
        - 根据 args.cmd 派发到 cmd_chat / cmd_interactive / cmd_start
    """
    raise NotImplementedError("TODO: main")
