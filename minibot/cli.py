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
import sys
from pathlib import Path

from minibot.core import MiniBotCore


def build_parser() -> argparse.ArgumentParser:
    """构造命令行解析器，含 chat / interactive / start 三个子命令。"""
    parser = argparse.ArgumentParser(
        prog="minibot",
        description="MiniBot — 轻量级 AI Agent 框架（学习项目）",
    )
    parser.add_argument(
        "--config",
        default="config.json",
        help="config.json 路径（默认 ./config.json）",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    chat_p = sub.add_parser("chat", help="单轮问答")
    chat_p.add_argument("message", help="用户消息")

    sub.add_parser("interactive", help="进入 REPL")
    sub.add_parser("start", help="守护进程模式（含 scheduler）")

    return parser


def cmd_chat(config_path: Path, message: str) -> int:
    """处理 `minibot chat` 子命令：单轮问答后退出。"""
    core: MiniBotCore | None = None
    try:
        core = MiniBotCore.from_config(config_path)
        reply = core.chat(message)
        print(reply)
        return 0
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    finally:
        if core is not None:
            core.shutdown()


def cmd_interactive(config_path: Path) -> int:
    """处理 `minibot interactive` 子命令：REPL 多轮对话。"""
    core: MiniBotCore | None = None
    try:
        core = MiniBotCore.from_config(config_path)
        core.interactive()
        return 0
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    finally:
        if core is not None:
            core.shutdown()


def cmd_start(config_path: Path) -> int:
    """处理 `minibot start` 子命令：守护进程，阻塞等待 cron 触发。"""
    core: MiniBotCore | None = None
    try:
        core = MiniBotCore.from_config(config_path)
        core.start()
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    finally:
        if core is not None:
            core.shutdown()


def main(argv: list[str] | None = None) -> int:
    """CLI 总入口。根据子命令派发到对应处理函数。"""
    parser = build_parser()
    args = parser.parse_args(argv)
    config_path = Path(args.config)

    if args.cmd == "chat":
        return cmd_chat(config_path, args.message)
    if args.cmd == "interactive":
        return cmd_interactive(config_path)
    if args.cmd == "start":
        return cmd_start(config_path)

    parser.print_help()
    return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
