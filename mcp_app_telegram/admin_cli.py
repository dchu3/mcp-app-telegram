"""Interactive admin console for runtime management."""

from __future__ import annotations

import argparse
import asyncio
import logging
import shlex
from collections import deque
from dataclasses import dataclass, field
from threading import Lock
from typing import Optional, Sequence

from .admin_state import (
    AdminState,
    AdminStateRepository,
    TokenAdminRecord,
    TokenThresholds,
)
from .arb.profiles import ArbProfile, ProfileService
from .arb.signals import ArbSignalService
from .infra.scheduler import CentralScheduler
from .infra.store import InMemoryStore, PairMetadata
from .market.fetcher import MarketDataFetcher


def _format_number(value: Optional[float]) -> str:
    if value is None:
        return "-"
    abs_value = abs(value)
    if abs_value >= 1_000_000_000:
        return f"{value / 1_000_000_000:.2f}B"
    if abs_value >= 1_000_000:
        return f"{value / 1_000_000:.2f}M"
    if abs_value >= 1_000:
        return f"{value / 1_000:.2f}K"
    return f"{value:.0f}"


def _format_integer(value: Optional[int]) -> str:
    return "-" if value is None else str(value)


def _format_pair_label(pair_key: str, metadata: Optional[PairMetadata]) -> str:
    """Return a concise pair label such as TOKEN@dex for table output."""

    def _first_symbol(symbols: str) -> str:
        head = symbols.split("/")[0].strip()
        return head or symbols.strip()

    base_symbol = ""
    dex_id = ""
    if metadata:
        base_symbol = (metadata.base_symbol or "").strip()
        if not base_symbol and metadata.symbols:
            base_symbol = _first_symbol(metadata.symbols)
        dex_id = (metadata.dex_id or "").strip()
        if base_symbol:
            return f"{base_symbol}@{dex_id}" if dex_id else base_symbol
        if metadata.symbols:
            fallback = _first_symbol(metadata.symbols)
            if fallback:
                return f"{fallback}@{dex_id}" if dex_id else fallback

    prefix, _, suffix = pair_key.partition("@")
    dex_id = suffix.strip()
    base_fragment = prefix.split(":")[-1]
    if "/" in base_fragment:
        base_fragment = base_fragment.split("/")[0]
    base_fragment = base_fragment.strip()
    if base_fragment and dex_id:
        return f"{base_fragment}@{dex_id}"
    if base_fragment:
        return base_fragment
    return pair_key


_LOGGER = logging.getLogger(__name__)


class CommandError(RuntimeError):
    """Raised when a CLI command fails validation."""


@dataclass
class PromptState:
    """Shared prompt metadata used to keep the REPL readable during logging."""

    lock: Lock = field(default_factory=Lock)
    prompt: str = ""
    active: bool = False

    def set_prompt(self, prompt: str) -> None:
        with self.lock:
            self.prompt = prompt
            self.active = True

    def clear(self) -> None:
        with self.lock:
            self.prompt = ""
            self.active = False


class PromptAwareStreamHandler(logging.StreamHandler):
    """Stream handler that redraws the admin prompt after log output."""

    def __init__(self, *, prompt_state: PromptState, stream=None) -> None:  # pragma: no cover - IO heavy
        super().__init__(stream)
        self._state = prompt_state

    def emit(self, record: logging.LogRecord) -> None:  # pragma: no cover - IO heavy
        try:
            msg = self.format(record)
        except Exception:
            self.handleError(record)
            return

        try:
            with self._state.lock:
                prompt = self._state.prompt if self._state.active else None
            if prompt:
                try:
                    import readline

                    buffer = readline.get_line_buffer()
                except Exception:  # pragma: no cover - best effort
                    buffer = ""
                self.stream.write("\r")
                self.stream.write(msg + "\n")
                self.stream.write(f"{prompt}{buffer}")
            else:
                self.stream.write(msg + "\n")
            self.flush()
        except Exception:
            self.handleError(record)


class AdminLogBuffer(logging.Handler):
    """In-memory ring buffer retaining recent log lines for the CLI."""

    def __init__(self, *, capacity: int = 500) -> None:
        super().__init__()
        self._capacity = capacity
        self._records: deque[str] = deque(maxlen=capacity)
        self._lock = Lock()

    def emit(self, record: logging.LogRecord) -> None:  # pragma: no cover - trivial
        try:
            message = self.format(record)
        except Exception:
            message = record.getMessage()
        with self._lock:
            self._records.append(message)

    def tail(self, count: int) -> list[str]:
        with self._lock:
            if count <= 0:
                return []
            return list(self._records)[-count:]


class CliArgumentParser(argparse.ArgumentParser):
    """Argument parser that raises exceptions instead of exiting."""

    def error(self, message: str) -> None:  # pragma: no cover - passthrough
        raise CommandError(message)

    def exit(self, status: int = 0, message: Optional[str] = None) -> None:  # pragma: no cover
        if message:
            raise CommandError(message.strip())
        raise CommandError("command aborted")


class AdminCli:
    """Simple asynchronous REPL for runtime admin actions."""

    def __init__(
        self,
        *,
        state: AdminState,
        repository: AdminStateRepository,
        store: InMemoryStore,
        scheduler: CentralScheduler,
        fetcher: MarketDataFetcher,
        profile_service: ProfileService,
        signal_service: ArbSignalService,
        stop_callback,
        baseline_profile: ArbProfile,
        log_buffer: Optional[AdminLogBuffer] = None,
        prompt_state: Optional[PromptState] = None,
        quiet_mode: bool = False,
    ) -> None:
        self._state = state
        self._repository = repository
        self._store = store
        self._scheduler = scheduler
        self._fetcher = fetcher
        self._profile_service = profile_service
        self._signal_service = signal_service
        self._stop_callback = stop_callback
        self._baseline_profile = baseline_profile
        self._closing = False
        self._log_buffer = log_buffer
        self._prompt_state = prompt_state or PromptState()
        self._quiet_mode = quiet_mode

    async def run(self) -> None:
        print("[admin] CLI ready. Type 'help' for a command list.\n")
        if self._quiet_mode:
            print(
                "[admin] Info-level logging is suppressed; use 'log' to inspect recent output"
            )
        while not self._closing:
            try:
                line = await self._readline("admin> ")
            except (EOFError, KeyboardInterrupt):
                print()
                await self._shutdown()
                break
            if line is None:
                continue
            line = line.strip()
            if not line:
                continue
            try:
                await self._dispatch(line)
            except CommandError as exc:
                if exc.args and exc.args[0]:
                    print(f"Error: {exc.args[0]}")
            except Exception as exc:  # pragma: no cover - defensive guard
                _LOGGER.exception("Admin CLI command failed")
                print(f"Unexpected error: {exc}")

    async def _dispatch(self, line: str) -> None:
        parts = shlex.split(line)
        if not parts:
            return
        cmd = parts[0].lower()
        args = parts[1:]
        if cmd in {"quit", "exit"}:
            await self._shutdown()
        elif cmd == "help":
            self._print_help()
        elif cmd in {"token", "tokens"}:
            await self._handle_token(args)
        elif cmd == "settings":
            await self._handle_settings(args)
        elif cmd in {"arb-profile", "profile"}:
            await self._handle_profile(args)
        elif cmd == "log":
            self._handle_log(args)
        else:
            raise CommandError(f"unknown command '{cmd}'")

    async def _handle_token(self, argv: Sequence[str]) -> None:
        if not argv or argv[0] in {"list", "ls"}:
            await self._list_tokens()
            return

        parser = CliArgumentParser(prog="token", add_help=False)
        subparsers = parser.add_subparsers(dest="command")
        subparsers.required = True

        add_parser = subparsers.add_parser("add", add_help=False)
        add_parser.add_argument("pair_key")
        add_parser.add_argument("--symbols", required=True)
        add_parser.add_argument("--base-symbol", required=True)
        add_parser.add_argument("--quote-symbol", required=True)
        add_parser.add_argument("--base-address", required=True)
        add_parser.add_argument("--quote-address")
        add_parser.add_argument("--dex-id")
        add_parser.add_argument("--fee-tier", action="append", dest="fee_tiers", default=[])
        add_parser.add_argument("--min-liquidity", type=float)
        add_parser.add_argument("--min-volume", type=float)
        add_parser.add_argument("--min-txns", type=int)

        view_parser = subparsers.add_parser("view", add_help=False)
        view_parser.add_argument("--rows", type=int, default=10)
        view_parser.add_argument("--offset", type=int, default=0)
        view_parser.add_argument("--table", action="store_true")

        thresholds_parser = subparsers.add_parser("set-thresholds", add_help=False)
        thresholds_parser.add_argument("pair_key")
        thresholds_parser.add_argument("--min-liquidity", type=float)
        thresholds_parser.add_argument("--min-volume", type=float)
        thresholds_parser.add_argument("--min-txns", type=int)
        thresholds_parser.add_argument("--clear", action="store_true")

        remove_parser = subparsers.add_parser("remove", add_help=False)
        remove_parser.add_argument("pair_key")

        ns = parser.parse_args(argv)
        if ns.command == "add":
            await self._token_add(ns)
        elif ns.command == "view":
            self._token_view(ns)
        elif ns.command == "set-thresholds":
            await self._token_set_thresholds(ns)
        elif ns.command == "remove":
            await self._token_remove(ns.pair_key)

    async def _handle_settings(self, argv: Sequence[str]) -> None:
        if not argv or argv[0] in {"show", "ls"}:
            self._print_settings()
            return

        parser = CliArgumentParser(prog="settings", add_help=False)
        subparsers = parser.add_subparsers(dest="command")
        subparsers.required = True

        global_parser = subparsers.add_parser("set-global", add_help=False)
        global_parser.add_argument("--min-liquidity", type=float)
        global_parser.add_argument("--min-volume", type=float)
        global_parser.add_argument("--min-txns", type=int)
        global_parser.add_argument("--clear", action="store_true")

        mev_parser = subparsers.add_parser("set-mev", add_help=False)
        mev_parser.add_argument("--bps", type=float, required=True)

        ns = parser.parse_args(argv)
        if ns.command == "set-global":
            await self._settings_set_global(ns)
        elif ns.command == "set-mev":
            self._settings_set_mev(ns.bps)

    async def _handle_profile(self, argv: Sequence[str]) -> None:
        if not argv or argv[0] in {"show", "ls"}:
            profile = self._profile_service.get_default()
            self._print_profile(profile)
            return

        parser = CliArgumentParser(prog="arb-profile", add_help=False)
        subparsers = parser.add_subparsers(dest="command")
        subparsers.required = True

        set_parser = subparsers.add_parser("set", add_help=False)
        set_parser.add_argument("--min-net-bps", type=float)
        set_parser.add_argument("--min-net-eur", type=float)
        set_parser.add_argument("--test-size-eur", type=float)
        set_parser.add_argument("--slippage-cap-bps", type=float)
        set_parser.add_argument("--cooldown-seconds", type=int)

        reset_parser = subparsers.add_parser("reset", add_help=False)
        reset_parser.add_argument("--to-baseline", action="store_true")

        ns = parser.parse_args(argv)
        if ns.command == "set":
            await self._profile_set(ns)
        elif ns.command == "reset":
            self._profile_reset()

    async def _token_add(self, ns) -> None:
        pair_key = ns.pair_key.strip()
        if not pair_key:
            raise CommandError("pair_key must not be empty")

        metadata = PairMetadata(
            pair_key=pair_key,
            symbols=ns.symbols.strip(),
            base_symbol=ns.base_symbol.strip(),
            quote_symbol=ns.quote_symbol.strip(),
            base_address=ns.base_address.strip(),
            quote_address=ns.quote_address.strip() if getattr(ns, "quote_address", None) else None,
            dex_id=ns.dex_id.strip() if getattr(ns, "dex_id", None) else None,
            fee_tiers=tuple(tier.strip() for tier in ns.fee_tiers if tier and tier.strip()),
        )

        await self._store.upsert_pair_metadata(metadata)
        await self._store.ensure_pair_in_scan_set(pair_key)
        await self._scheduler.trigger_refresh()

        record = self._state.tokens.get(pair_key, TokenAdminRecord())
        record.metadata = metadata

        thresholds = TokenThresholds(
            min_liquidity_usd=ns.min_liquidity,
            min_volume_24h_usd=ns.min_volume,
            min_txns_24h=ns.min_txns,
        )
        if thresholds.to_dict():
            self._fetcher.set_token_thresholds(pair_key, thresholds)
            record.thresholds = thresholds

        self._state.tokens[pair_key] = record
        self._save_state()
        print(f"[admin] Added token '{pair_key}'")

    def _token_view(self, ns) -> None:
        rows = ns.rows
        if rows is not None and rows <= 0:
            raise CommandError("--rows must be greater than zero")

        offset = getattr(ns, "offset", 0)
        if offset < 0:
            raise CommandError("--offset must be zero or greater")

        records, total = self._repository.list_tokens(limit=rows, offset=offset)
        if total == 0:
            print("No persisted tokens in admin repository.")
            return

        if offset >= total:
            print(
                f"No tokens available at offset {offset}; total stored tokens: {total}"
            )
            return

        limit_desc = "all" if rows is None else str(rows)
        shown = len(records)
        print(
            f"Stored tokens (showing {shown} of {total}, offset={offset}, limit={limit_desc}):"
        )
        if getattr(ns, "table", False):
            self._print_token_table(records)
        else:
            for pair_key, record in records:
                parts = [f"  - {pair_key}"]
                metadata = record.metadata
                if metadata is not None:
                    parts.append(f"symbols={metadata.symbols}")
                    parts.append(f"base={metadata.base_symbol}")
                    parts.append(f"quote={metadata.quote_symbol}")
                    if metadata.dex_id:
                        parts.append(f"dex={metadata.dex_id}")
                thresholds = record.thresholds.to_dict()
                if thresholds:
                    parts.append(f"thresholds={thresholds}")
                print(" | ".join(parts))

        if rows is not None and (offset + shown) < total:
            next_offset = offset + shown
            remaining = total - next_offset
            print(
                f"... {remaining} more entr{'y' if remaining == 1 else 'ies'} available."
                f" Rerun with --offset {next_offset}."
            )

    def _print_token_table(self, records: list[tuple[str, TokenAdminRecord]]) -> None:
        headers = ("Pair", "Symbols", "Min Liquidity", "Min Volume", "Min Txns")
        rows: list[tuple[str, str, str, str, str]] = []
        for pair_key, record in records:
            metadata = record.metadata
            symbols = metadata.symbols if metadata else pair_key
            thresholds = record.thresholds
            if not thresholds.to_dict():
                effective = self._fetcher.get_effective_thresholds(pair_key)
            else:
                effective = thresholds
            rows.append(
                (
                    _format_pair_label(pair_key, metadata),
                    symbols,
                    _format_number(effective.min_liquidity_usd),
                    _format_number(effective.min_volume_24h_usd),
                    _format_integer(effective.min_txns_24h),
                )
            )

        widths = [len(header) for header in headers]
        for row in rows:
            for idx, cell in enumerate(row):
                widths[idx] = max(widths[idx], len(cell))

        def _print_row(columns: tuple[str, ...]) -> None:
            formatted = "  " + "  ".join(
                value.ljust(widths[idx]) for idx, value in enumerate(columns)
            )
            print(formatted.rstrip())

        _print_row(headers)
        separator = "  " + "  ".join("-" * width for width in widths)
        print(separator)
        for row in rows:
            _print_row(row)

    async def _token_set_thresholds(self, ns) -> None:
        pair_key = ns.pair_key.strip()
        if not pair_key:
            raise CommandError("pair_key must not be empty")
        if ns.clear:
            self._fetcher.set_token_thresholds(pair_key, None)
            record = self._state.tokens.get(pair_key)
            if record is not None:
                record.thresholds = TokenThresholds()
                if record.metadata is None and not record.thresholds.to_dict():
                    self._state.tokens.pop(pair_key, None)
            self._save_state()
            print(f"[admin] Cleared thresholds for '{pair_key}'")
            return

        thresholds = TokenThresholds(
            min_liquidity_usd=ns.min_liquidity,
            min_volume_24h_usd=ns.min_volume,
            min_txns_24h=ns.min_txns,
        )
        if not thresholds.to_dict():
            raise CommandError("provide at least one threshold or use --clear")

        self._fetcher.set_token_thresholds(pair_key, thresholds)
        record = self._state.tokens.get(pair_key, TokenAdminRecord())
        record.thresholds = thresholds
        self._state.tokens[pair_key] = record
        self._save_state()
        print(f"[admin] Updated thresholds for '{pair_key}'")

    async def _token_remove(self, pair_key: str) -> None:
        pair_key = pair_key.strip()
        if not pair_key:
            raise CommandError("pair_key must not be empty")
        existed = await self._store.remove_pair(pair_key)
        self._fetcher.set_token_thresholds(pair_key, None)
        self._state.tokens.pop(pair_key, None)
        self._save_state()
        await self._scheduler.trigger_refresh()
        if existed:
            print(f"[admin] Removed token '{pair_key}'")
        else:
            print(f"[admin] Cleared overrides for '{pair_key}' (pair not tracked)")

    async def _list_tokens(self) -> None:
        metadata_items = await self._store.list_pair_metadata()
        if not metadata_items:
            print("No tracked tokens.")
            return
        print("Tracked tokens:")
        for meta in sorted(metadata_items, key=lambda item: item.pair_key):
            overrides = self._fetcher.get_token_thresholds(meta.pair_key).to_dict()
            effective = self._fetcher.get_effective_thresholds(meta.pair_key)
            desc = f"  - {meta.symbols} [{meta.pair_key}]"
            if overrides:
                desc += f" overrides={overrides}"
            desc += (
                f" (effective: liquidity>={effective.min_liquidity_usd:.0f},"
                f" volume>={effective.min_volume_24h_usd:.0f}, txns>={effective.min_txns_24h})"
            )
            print(desc)

    async def _settings_set_global(self, ns) -> None:
        if ns.clear:
            self._fetcher.set_global_thresholds()
            self._state.global_thresholds = TokenThresholds()
            self._save_state()
            print("[admin] Reset global thresholds to configuration defaults")
            return
        thresholds = TokenThresholds(
            min_liquidity_usd=ns.min_liquidity,
            min_volume_24h_usd=ns.min_volume,
            min_txns_24h=ns.min_txns,
        )
        if not thresholds.to_dict():
            raise CommandError("provide at least one override or use --clear")
        self._fetcher.set_global_thresholds(
            min_liquidity_usd=thresholds.min_liquidity_usd,
            min_volume_24h_usd=thresholds.min_volume_24h_usd,
            min_txns_24h=thresholds.min_txns_24h,
        )
        self._state.global_thresholds = thresholds
        self._save_state()
        print("[admin] Updated global thresholds")

    def _settings_set_mev(self, bps: float) -> None:
        if bps < 0:
            raise CommandError("MEV buffer must be >= 0")
        self._fetcher.set_mev_buffer_bps(bps)
        self._signal_service.set_default_mev_buffer_bps(bps)
        self._state.mev_buffer_bps = bps
        self._save_state()
        print(f"[admin] Set MEV buffer to {bps:.2f} bps")

    async def _profile_set(self, ns) -> None:
        updates = {}
        if ns.min_net_bps is not None:
            updates["min_net_bps"] = ns.min_net_bps
        if ns.min_net_eur is not None:
            updates["min_net_eur"] = ns.min_net_eur
        if ns.test_size_eur is not None:
            updates["test_size_eur"] = ns.test_size_eur
        if ns.slippage_cap_bps is not None:
            updates["slippage_cap_bps"] = ns.slippage_cap_bps
        if ns.cooldown_seconds is not None:
            updates["cooldown_seconds"] = ns.cooldown_seconds
        if not updates:
            raise CommandError("provide at least one field to update")
        profile = self._profile_service.update_default(**updates)
        self._state.default_profile = profile.to_dict()
        self._save_state()
        self._print_profile(profile)

    def _profile_reset(self) -> None:
        profile = self._profile_service.update_default(**self._baseline_profile.to_dict())
        self._state.default_profile = {}
        self._save_state()
        print("[admin] Reset default profile to baseline configuration")
        self._print_profile(profile)

    def _print_settings(self) -> None:
        base = self._fetcher.get_base_thresholds()
        global_overrides = self._fetcher.get_global_thresholds().to_dict()
        effective = self._fetcher.get_effective_thresholds()
        print("Market thresholds:")
        print(
            f"  base: liquidity>={base.min_liquidity_usd:.0f},"
            f" volume>={base.min_volume_24h_usd:.0f}, txns>={base.min_txns_24h}"
        )
        if global_overrides:
            print(f"  overrides: {global_overrides}")
        print(
            f"  effective: liquidity>={effective.min_liquidity_usd:.0f},"
            f" volume>={effective.min_volume_24h_usd:.0f}, txns>={effective.min_txns_24h}"
        )
        mev = self._fetcher.get_mev_buffer_bps()
        print(f"MEV buffer: {mev:.2f} bps")

    def _print_profile(self, profile: ArbProfile) -> None:
        print(
            "Default arbitrage profile:\n"
            f"  min_net_bps={profile.min_net_bps:.2f}\n"
            f"  min_net_eur={profile.min_net_eur:.2f}\n"
            f"  test_size_eur={profile.test_size_eur:.2f}\n"
            f"  slippage_cap_bps={profile.slippage_cap_bps:.2f}\n"
            f"  cooldown_seconds={profile.cooldown_seconds}"
        )

    def _print_help(self) -> None:
        print(
            "Available commands:\n"
            "  help                             Show this message\n"
            "  token list                       List tracked tokens\n"
            "  token view [--rows N] [--offset M] [--table]  View persisted tokens in SQLite\n"
            "  token add <pair_key> [options]   Add a new token to monitor\n"
            "  token set-thresholds <pair>      Override per-token filters\n"
            "  token remove <pair>              Remove token overrides\n"
            "  settings show                    Display global settings\n"
            "  settings set-global [options]    Update global market filters\n"
            "  settings set-mev --bps <value>   Update MEV buffer\n"
            "  arb-profile show                 Display default arbitrage profile\n"
            "  arb-profile set [options]        Update default profile\n"
            "  arb-profile reset                Reset profile to baseline\n"
            "  log [n]                          Show the last n log lines (default 20)\n"
            "  quit                             Exit the admin console"
        )

    async def _readline(self, prompt: str) -> Optional[str]:
        loop = asyncio.get_running_loop()
        self._prompt_state.set_prompt(prompt)
        try:
            return await loop.run_in_executor(None, _input_with_prompt, prompt)
        finally:
            self._prompt_state.clear()

    def _handle_log(self, argv: Sequence[str]) -> None:
        if self._log_buffer is None:
            print("Log history is not available in this session.")
            return
        max_lines = 20
        if argv:
            try:
                max_lines = max(1, int(argv[0]))
            except ValueError as exc:
                raise CommandError("log count must be an integer") from exc
        lines = self._log_buffer.tail(max_lines)
        if not lines:
            print("No log entries recorded yet.")
            return
        for line in lines:
            print(line)

    def _save_state(self) -> None:
        try:
            self._repository.save(self._state)
        except Exception as exc:  # pragma: no cover - defensive guard
            _LOGGER.warning("Failed to persist admin state: %s", exc)

    async def _shutdown(self) -> None:
        if self._closing:
            return
        self._closing = True
        self._save_state()
        self._stop_callback()


def _input_with_prompt(prompt: str) -> str:
    return input(prompt)
