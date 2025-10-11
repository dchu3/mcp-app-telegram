from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from mcp_app_telegram.admin_cli import (
    AdminCli,
    AdminLogBuffer,
    CommandError,
    PromptState,
)
from mcp_app_telegram.admin_state import (
    AdminState,
    AdminStateRepository,
    TokenAdminRecord,
    TokenThresholds,
)
from mcp_app_telegram.arb.profiles import ArbProfile
from mcp_app_telegram.infra.store import PairMetadata


def _build_cli(
    log_buffer: AdminLogBuffer | None = None,
    repository: AdminStateRepository | MagicMock | None = None,
) -> AdminCli:
    store = MagicMock()
    scheduler = MagicMock()
    fetcher = MagicMock()
    profile_service = MagicMock()
    signal_service = MagicMock()
    repo = repository or MagicMock()

    return AdminCli(
        state=AdminState(),
        repository=repo,
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


def test_token_view_prints_records(capsys: pytest.CaptureFixture[str]) -> None:
    repository = MagicMock()
    metadata = PairMetadata(
        pair_key="chain:base/quote@dex",
        symbols="ABC/XYZ",
        base_symbol="ABC",
        quote_symbol="XYZ",
        base_address="0xabc",
        quote_address="0xxyz",
        dex_id="dex",
        fee_tiers=("0.30",),
    )
    thresholds = TokenThresholds(min_liquidity_usd=1000.0)
    repository.list_tokens.return_value = (
        [
            (metadata.pair_key, TokenAdminRecord(metadata=metadata, thresholds=thresholds))
        ],
        1,
    )

    cli = _build_cli(repository=repository)
    cli._token_view(SimpleNamespace(rows=5, offset=0))

    output = capsys.readouterr().out.strip().splitlines()
    assert output[0].startswith("Stored tokens (showing 1 of 1")
    assert "chain:base/quote@dex" in output[1]
    assert "symbols=ABC/XYZ" in output[1]
    assert "thresholds={'min_liquidity_usd': 1000.0}" in output[1]


def test_token_view_rejects_non_positive_rows() -> None:
    cli = _build_cli(repository=MagicMock())
    with pytest.raises(CommandError):
        cli._token_view(SimpleNamespace(rows=0, offset=0))


def test_token_view_rejects_negative_offset() -> None:
    cli = _build_cli(repository=MagicMock())
    with pytest.raises(CommandError):
        cli._token_view(SimpleNamespace(rows=5, offset=-1))


def test_token_view_handles_empty_repository(capsys: pytest.CaptureFixture[str]) -> None:
    repository = MagicMock()
    repository.list_tokens.return_value = ([], 0)

    cli = _build_cli(repository=repository)
    cli._token_view(SimpleNamespace(rows=10, offset=0))

    output = capsys.readouterr().out.strip()
    assert output == "No persisted tokens in admin repository."


def test_token_view_reports_additional_entries(capsys: pytest.CaptureFixture[str]) -> None:
    repository = MagicMock()
    record = TokenAdminRecord(metadata=None, thresholds=TokenThresholds())
    repository.list_tokens.return_value = ([("pair-1", record)], 3)

    cli = _build_cli(repository=repository)
    cli._token_view(SimpleNamespace(rows=1, offset=0))

    output = capsys.readouterr().out.strip().splitlines()
    assert output[-1].startswith("... 2 more entries available. Rerun with --offset 1.")


@pytest.mark.asyncio
async def test_token_view_with_real_repository(tmp_path, capsys: pytest.CaptureFixture[str]) -> None:
    repo_path = tmp_path / "admin_state.db"
    repository = AdminStateRepository(repo_path)

    state = AdminState()
    keys = [
        ("chain:alpha/usdc@dex", "ALPHA/USDC"),
        ("chain:beta/usdc@dex", "BETA/USDC"),
        ("chain:gamma/usdc@dex", "GAMMA/USDC"),
    ]
    for idx, (pair_key, symbols) in enumerate(keys):
        state.tokens[pair_key] = TokenAdminRecord(
            metadata=PairMetadata(
                pair_key=pair_key,
                symbols=symbols,
                base_symbol=symbols.split("/")[0],
                quote_symbol="USDC",
                base_address=f"0x{idx + 1:02x}",
                quote_address="0x9999",
                dex_id="dex",
                fee_tiers=("0.30",),
            ),
            thresholds=TokenThresholds(min_volume_24h_usd=1000.0 + idx),
        )

    repository.save(state)

    cli = _build_cli(repository=repository)
    await cli._handle_token(["view", "--rows", "2", "--offset", "1"])

    lines = capsys.readouterr().out.strip().splitlines()
    assert lines[0] == "Stored tokens (showing 2 of 3, offset=1, limit=2):"
    assert "chain:beta/usdc@dex" in lines[1]
    assert "chain:gamma/usdc@dex" in lines[2]


def test_token_view_table_output(capsys: pytest.CaptureFixture[str]) -> None:
    repository = MagicMock()
    metadata = PairMetadata(
        pair_key="base:table/usdc@dex",
        symbols="TABLE/USDC",
        base_symbol="TABLE",
        quote_symbol="USDC",
        base_address="0x1",
        quote_address="0x2",
        dex_id="dex",
        fee_tiers=("0.30",),
    )
    record = TokenAdminRecord(metadata=metadata, thresholds=TokenThresholds())
    repository.list_tokens.return_value = ([(metadata.pair_key, record)], 1)

    fetcher = MagicMock()
    fetcher.get_effective_thresholds.return_value = TokenThresholds(
        min_liquidity_usd=250000.0,
        min_volume_24h_usd=500000.0,
        min_txns_24h=750,
    )

    cli = _build_cli(repository=repository)
    cli._fetcher = fetcher
    cli._token_view(SimpleNamespace(rows=10, offset=0, table=True))

    lines = capsys.readouterr().out.strip().splitlines()
    assert "Pair" in lines[1]
    assert "TABLE@dex" in lines[3]
    assert "TABLE/USDC" in lines[3]
    assert "250.00K" in lines[3]
    assert "750" in lines[3]
