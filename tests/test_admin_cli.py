from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest

from mcp_app_telegram.admin_cli import (
    AdminCli,
    AdminLogBuffer,
    CommandError,
    PromptState,
)
from mcp_app_telegram.admin_state import AdminState
from mcp_app_telegram.arb.profiles import ArbProfile


def _build_cli(log_buffer: AdminLogBuffer | None = None) -> AdminCli:
    store = MagicMock()
    scheduler = MagicMock()
    fetcher = MagicMock()
    profile_service = MagicMock()
    signal_service = MagicMock()

    return AdminCli(
        state=AdminState(),
        repository=MagicMock(),
        store=store,
        scheduler=scheduler,
        fetcher=fetcher,
        profile_service=profile_service,
        signal_service=signal_service,
        stop_callback=lambda: None,
        baseline_profile=ArbProfile(),
        log_buffer=log_buffer,
        prompt_state=PromptState(),
    )


def test_admin_log_buffer_tail_limits_lines() -> None:
    buffer = AdminLogBuffer(capacity=3)
    buffer.setFormatter(logging.Formatter("%(message)s"))

    for idx in range(5):
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname=__file__,
            lineno=idx,
            msg=f"line-{idx}",
            args=(),
            exc_info=None,
        )
        buffer.handle(record)

    assert buffer.tail(2) == ["line-3", "line-4"]
    assert buffer.tail(10) == ["line-2", "line-3", "line-4"]


def test_log_command_reads_from_buffer(capsys: pytest.CaptureFixture[str]) -> None:
    buffer = AdminLogBuffer(capacity=10)
    buffer.setFormatter(logging.Formatter("%(levelname)s:%(message)s"))
    for idx in range(4):
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname=__file__,
            lineno=idx,
            msg=f"entry-{idx}",
            args=(),
            exc_info=None,
        )
        buffer.handle(record)

    cli = _build_cli(log_buffer=buffer)
    cli._handle_log(["2"])
    output = capsys.readouterr().out.strip().splitlines()
    assert output == ["INFO:entry-2", "INFO:entry-3"]


def test_log_command_rejects_invalid_count() -> None:
    cli = _build_cli(log_buffer=AdminLogBuffer())
    with pytest.raises(CommandError):
        cli._handle_log(["not-a-number"])
