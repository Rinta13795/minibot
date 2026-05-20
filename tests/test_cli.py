"""tests/test_cli.py — CLI 子命令解析和派发测试。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from minibot import cli


class TestParser:
    def test_chat_requires_message(self) -> None:
        parser = cli.build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["chat"])  # 缺 message

    def test_chat_message_parsed(self) -> None:
        args = cli.build_parser().parse_args(["chat", "你好"])
        assert args.cmd == "chat"
        assert args.message == "你好"

    def test_interactive_subcommand(self) -> None:
        args = cli.build_parser().parse_args(["interactive"])
        assert args.cmd == "interactive"

    def test_start_subcommand(self) -> None:
        args = cli.build_parser().parse_args(["start"])
        assert args.cmd == "start"

    def test_config_default(self) -> None:
        args = cli.build_parser().parse_args(["chat", "x"])
        assert args.config == "config.json"

    def test_config_override(self) -> None:
        args = cli.build_parser().parse_args(["--config", "/tmp/foo.json", "chat", "x"])
        assert args.config == "/tmp/foo.json"

    def test_missing_subcommand_errors(self) -> None:
        parser = cli.build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args([])


class TestDispatch:
    def test_main_dispatches_to_chat(self, capsys) -> None:
        with patch.object(cli, "MiniBotCore") as mock_cls:
            mock = MagicMock()
            mock.chat.return_value = "hello back"
            mock_cls.from_config.return_value = mock
            exit_code = cli.main(["chat", "hi"])
        assert exit_code == 0
        captured = capsys.readouterr()
        assert "hello back" in captured.out

    def test_main_dispatches_to_interactive(self) -> None:
        with patch.object(cli, "MiniBotCore") as mock_cls:
            mock = MagicMock()
            mock_cls.from_config.return_value = mock
            exit_code = cli.main(["interactive"])
        assert exit_code == 0
        mock.interactive.assert_called_once()

    def test_chat_handles_exception(self, capsys) -> None:
        with patch.object(cli, "MiniBotCore") as mock_cls:
            mock_cls.from_config.side_effect = RuntimeError("api key missing")
            exit_code = cli.main(["chat", "hi"])
        assert exit_code == 1
        captured = capsys.readouterr()
        assert "api key missing" in captured.err

    def test_shutdown_called_on_chat_success(self) -> None:
        with patch.object(cli, "MiniBotCore") as mock_cls:
            mock = MagicMock()
            mock.chat.return_value = "ok"
            mock_cls.from_config.return_value = mock
            cli.main(["chat", "hi"])
        mock.shutdown.assert_called_once()

    def test_shutdown_called_on_chat_failure(self) -> None:
        with patch.object(cli, "MiniBotCore") as mock_cls:
            mock = MagicMock()
            mock.chat.side_effect = RuntimeError("boom")
            mock_cls.from_config.return_value = mock
            cli.main(["chat", "hi"])
        mock.shutdown.assert_called_once()

    def test_start_keyboard_interrupt_exits_0(self) -> None:
        with patch.object(cli, "MiniBotCore") as mock_cls:
            mock = MagicMock()
            mock.start.side_effect = KeyboardInterrupt
            mock_cls.from_config.return_value = mock
            exit_code = cli.main(["start"])
        assert exit_code == 0
