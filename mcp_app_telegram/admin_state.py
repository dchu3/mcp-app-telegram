"""Admin console state persistence helpers."""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from .infra.store import PairMetadata


_LOGGER = logging.getLogger(__name__)


def _pair_metadata_to_dict(metadata: PairMetadata) -> Dict[str, Any]:
    return {
        "pair_key": metadata.pair_key,
        "symbols": metadata.symbols,
        "base_symbol": metadata.base_symbol,
        "quote_symbol": metadata.quote_symbol,
        "base_address": metadata.base_address,
        "quote_address": metadata.quote_address,
        "dex_id": metadata.dex_id,
        "fee_tiers": list(metadata.fee_tiers),
    }


def _pair_metadata_from_mapping(payload: Mapping[str, Any]) -> PairMetadata:
    return PairMetadata(
        pair_key=str(payload.get("pair_key") or "").strip(),
        symbols=str(payload.get("symbols") or "").strip(),
        base_symbol=str(payload.get("base_symbol") or "").strip(),
        quote_symbol=str(payload.get("quote_symbol") or "").strip(),
        base_address=payload.get("base_address") or None,
        quote_address=payload.get("quote_address") or None,
        dex_id=payload.get("dex_id") or None,
        fee_tiers=tuple(str(tier) for tier in payload.get("fee_tiers", ()) if str(tier)),
    )


@dataclass(slots=True)
class TokenThresholds:
    """Per-token overrides for market health thresholds."""

    min_liquidity_usd: Optional[float] = None
    min_volume_24h_usd: Optional[float] = None
    min_txns_24h: Optional[int] = None

    @classmethod
    def from_mapping(cls, payload: Optional[Mapping[str, Any]]) -> "TokenThresholds":
        if not payload:
            return cls()
        return cls(
            min_liquidity_usd=_maybe_float(payload.get("min_liquidity_usd")),
            min_volume_24h_usd=_maybe_float(payload.get("min_volume_24h_usd")),
            min_txns_24h=_maybe_int(payload.get("min_txns_24h")),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            key: value
            for key, value in {
                "min_liquidity_usd": self.min_liquidity_usd,
                "min_volume_24h_usd": self.min_volume_24h_usd,
                "min_txns_24h": self.min_txns_24h,
            }.items()
            if value is not None
        }


@dataclass(slots=True)
class TokenAdminRecord:
    """Persisted configuration for a single token."""

    metadata: Optional[PairMetadata] = None
    thresholds: TokenThresholds = field(default_factory=TokenThresholds)

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "TokenAdminRecord":
        metadata_payload = payload.get("metadata")
        metadata = None
        if isinstance(metadata_payload, Mapping):
            metadata = _pair_metadata_from_mapping(metadata_payload)
        thresholds = TokenThresholds.from_mapping(payload.get("thresholds"))
        return cls(metadata=metadata, thresholds=thresholds)

    def to_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {}
        if self.metadata is not None:
            data["metadata"] = _pair_metadata_to_dict(self.metadata)
        threshold_dict = self.thresholds.to_dict()
        if threshold_dict:
            data["thresholds"] = threshold_dict
        return data


@dataclass(slots=True)
class AdminState:
    """Top-level admin configuration."""

    tokens: Dict[str, TokenAdminRecord] = field(default_factory=dict)
    global_thresholds: TokenThresholds = field(default_factory=TokenThresholds)
    mev_buffer_bps: Optional[float] = None
    default_profile: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {}
        if self.tokens:
            payload["tokens"] = {
                key: record.to_dict() for key, record in self.tokens.items() if record.to_dict()
            }
        global_thresholds = self.global_thresholds.to_dict()
        if global_thresholds:
            payload["global_thresholds"] = global_thresholds
        if self.mev_buffer_bps is not None:
            payload["mev_buffer_bps"] = float(self.mev_buffer_bps)
        if self.default_profile:
            payload["default_profile"] = dict(self.default_profile)
        return payload


class AdminStateRepository:
    """Load and persist admin state using a SQLite backend."""

    def __init__(self, path: Path, legacy_json_path: Optional[Path] = None) -> None:
        if path.suffix.lower() == ".json":
            db_path = path.with_suffix(".db")
            inferred_legacy = path
        else:
            db_path = path
            inferred_legacy = None
            if path.suffix:
                candidate_json = path.with_suffix(".json")
            else:
                candidate_json = path.with_name(path.name + ".json")
            if candidate_json.exists():
                inferred_legacy = candidate_json

        self._path = db_path
        self._legacy_json_path = (
            legacy_json_path
            if legacy_json_path and legacy_json_path.exists()
            else inferred_legacy if inferred_legacy and inferred_legacy.exists()
            else None
        )
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()
        self._maybe_migrate_from_json()

    def load(self) -> AdminState:
        with self._connect() as conn:
            return self._read_state(conn)

    def save(self, state: AdminState) -> None:
        with self._connect() as conn:
            self._write_state(conn, state)

    def list_tokens(
        self,
        *,
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> tuple[list[tuple[str, TokenAdminRecord]], int]:
        """Return a slice of persisted token records and the total count."""

        state = self.load()
        items = sorted(state.tokens.items(), key=lambda item: item[0].lower())
        total = len(items)

        if offset <= 0:
            start = 0
        else:
            start = min(offset, total)

        page = items[start:]
        if limit is not None:
            if limit <= 0:
                page = []
            else:
                page = page[:limit]
        return page, total

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tokens (
                    pair_key TEXT PRIMARY KEY,
                    symbols TEXT,
                    base_symbol TEXT,
                    quote_symbol TEXT,
                    base_address TEXT,
                    quote_address TEXT,
                    dex_id TEXT,
                    fee_tiers TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS token_thresholds (
                    pair_key TEXT PRIMARY KEY,
                    min_liquidity_usd REAL,
                    min_volume_24h_usd REAL,
                    min_txns_24h INTEGER
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS global_thresholds (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    min_liquidity_usd REAL,
                    min_volume_24h_usd REAL,
                    min_txns_24h INTEGER
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS default_profile (
                    field TEXT PRIMARY KEY,
                    value REAL
                )
                """
            )
            conn.commit()

    def _database_has_state(self, conn: sqlite3.Connection) -> bool:
        for query in (
            "SELECT 1 FROM tokens LIMIT 1",
            "SELECT 1 FROM token_thresholds LIMIT 1",
            "SELECT 1 FROM global_thresholds LIMIT 1",
            "SELECT 1 FROM settings LIMIT 1",
            "SELECT 1 FROM default_profile LIMIT 1",
        ):
            if conn.execute(query).fetchone():
                return True
        return False

    def _maybe_migrate_from_json(self) -> None:
        if not self._legacy_json_path or not self._legacy_json_path.exists():
            return
        migrated = False
        try:
            with self._connect() as conn:
                if self._database_has_state(conn):
                    return
                legacy_state = self._load_state_from_json(self._legacy_json_path)
                if legacy_state is None:
                    return
                self._write_state(conn, legacy_state)
                migrated = True
        except Exception:
            _LOGGER.warning(
                "Failed to migrate legacy admin state from '%s'", self._legacy_json_path, exc_info=True
            )
            return
        if migrated:
            try:
                backup_path = self._legacy_json_path.with_suffix(
                    self._legacy_json_path.suffix + ".bak"
                )
                self._legacy_json_path.rename(backup_path)
                self._legacy_json_path = None
                _LOGGER.info(
                    "Migrated admin state from legacy JSON '%s' to SQLite '%s'",
                    backup_path,
                    self._path,
                )
            except Exception:
                _LOGGER.warning(
                    "Migrated admin state but failed to archive legacy JSON '%s'",
                    self._legacy_json_path,
                )

    def _read_state(self, conn: sqlite3.Connection) -> AdminState:
        state = AdminState()

        metadata_rows = conn.execute(
            "SELECT pair_key, symbols, base_symbol, quote_symbol, base_address, quote_address, dex_id, fee_tiers FROM tokens"
        ).fetchall()
        metadata_map: Dict[str, PairMetadata] = {}
        for row in metadata_rows:
            pair_key = row["pair_key"]
            if not pair_key:
                continue
            required_fields = (row["symbols"], row["base_symbol"], row["quote_symbol"], row["base_address"])
            if any(field is None for field in required_fields):
                continue
            fee_tiers = ()
            if row["fee_tiers"]:
                try:
                    fee_tiers = tuple(str(value) for value in json.loads(row["fee_tiers"]))
                except json.JSONDecodeError:
                    fee_tiers = ()
            metadata_map[pair_key] = PairMetadata(
                pair_key=pair_key,
                symbols=row["symbols"],
                base_symbol=row["base_symbol"],
                quote_symbol=row["quote_symbol"],
                base_address=row["base_address"],
                quote_address=row["quote_address"],
                dex_id=row["dex_id"],
                fee_tiers=fee_tiers,
            )

        threshold_rows = conn.execute(
            "SELECT pair_key, min_liquidity_usd, min_volume_24h_usd, min_txns_24h FROM token_thresholds"
        ).fetchall()
        threshold_map: Dict[str, TokenThresholds] = {}
        for row in threshold_rows:
            pair_key = row["pair_key"]
            if not pair_key:
                continue
            threshold_map[pair_key] = TokenThresholds(
                min_liquidity_usd=_maybe_float(row["min_liquidity_usd"]),
                min_volume_24h_usd=_maybe_float(row["min_volume_24h_usd"]),
                min_txns_24h=_maybe_int(row["min_txns_24h"]),
            )

        tokens: Dict[str, TokenAdminRecord] = {}
        for pair_key in set(metadata_map) | set(threshold_map):
            metadata = metadata_map.get(pair_key)
            thresholds = threshold_map.get(pair_key, TokenThresholds())
            if metadata is None and not thresholds.to_dict():
                continue
            tokens[pair_key] = TokenAdminRecord(metadata=metadata, thresholds=thresholds)
        state.tokens = tokens

        row = conn.execute(
            "SELECT min_liquidity_usd, min_volume_24h_usd, min_txns_24h FROM global_thresholds WHERE id = 1"
        ).fetchone()
        if row:
            state.global_thresholds = TokenThresholds(
                min_liquidity_usd=_maybe_float(row["min_liquidity_usd"]),
                min_volume_24h_usd=_maybe_float(row["min_volume_24h_usd"]),
                min_txns_24h=_maybe_int(row["min_txns_24h"]),
            )

        mev_value = self._get_setting(conn, "mev_buffer_bps")
        if mev_value is not None:
            state.mev_buffer_bps = _maybe_float(mev_value)

        profile_rows = conn.execute("SELECT field, value FROM default_profile").fetchall()
        if profile_rows:
            state.default_profile = {
                row["field"]: float(row["value"])
                for row in profile_rows
                if row["field"] and _is_number(row["value"])
            }
        return state

    def _write_state(self, conn: sqlite3.Connection, state: AdminState) -> None:
        conn.execute("BEGIN")
        conn.execute("DELETE FROM token_thresholds")
        conn.execute("DELETE FROM tokens")
        conn.execute("DELETE FROM global_thresholds")
        conn.execute("DELETE FROM settings")
        conn.execute("DELETE FROM default_profile")

        for pair_key, record in state.tokens.items():
            if record.metadata is not None:
                metadata = record.metadata
                fee_tiers = json.dumps(list(metadata.fee_tiers)) if metadata.fee_tiers else None
                conn.execute(
                    """
                    INSERT INTO tokens (
                        pair_key,
                        symbols,
                        base_symbol,
                        quote_symbol,
                        base_address,
                        quote_address,
                        dex_id,
                        fee_tiers
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        pair_key,
                        metadata.symbols,
                        metadata.base_symbol,
                        metadata.quote_symbol,
                        metadata.base_address,
                        metadata.quote_address,
                        metadata.dex_id,
                        fee_tiers,
                    ),
                )
            thresholds_dict = record.thresholds.to_dict()
            if thresholds_dict:
                conn.execute(
                    """
                    INSERT INTO token_thresholds (
                        pair_key,
                        min_liquidity_usd,
                        min_volume_24h_usd,
                        min_txns_24h
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        pair_key,
                        thresholds_dict.get("min_liquidity_usd"),
                        thresholds_dict.get("min_volume_24h_usd"),
                        thresholds_dict.get("min_txns_24h"),
                    ),
                )

        global_thresholds = state.global_thresholds.to_dict()
        if global_thresholds:
            conn.execute(
                """
                INSERT INTO global_thresholds (id, min_liquidity_usd, min_volume_24h_usd, min_txns_24h)
                VALUES (1, ?, ?, ?)
                """,
                (
                    global_thresholds.get("min_liquidity_usd"),
                    global_thresholds.get("min_volume_24h_usd"),
                    global_thresholds.get("min_txns_24h"),
                ),
            )

        if state.mev_buffer_bps is not None:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?)",
                ("mev_buffer_bps", str(state.mev_buffer_bps)),
            )

        for field, value in state.default_profile.items():
            if not _is_number(value):
                continue
            conn.execute(
                "INSERT INTO default_profile (field, value) VALUES (?, ?)",
                (str(field), float(value)),
            )

        conn.commit()

    def _get_setting(self, conn: sqlite3.Connection, key: str) -> Optional[str]:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def _load_state_from_json(self, path: Path) -> Optional[AdminState]:
        try:
            payload_text = path.read_text()
        except FileNotFoundError:
            return None
        if not payload_text.strip():
            return AdminState()
        try:
            payload = json.loads(payload_text)
        except json.JSONDecodeError:
            _LOGGER.warning("Legacy admin state at '%s' is not valid JSON", path)
            return AdminState()

        state = AdminState()
        tokens_payload = payload.get("tokens")
        if isinstance(tokens_payload, Mapping):
            tokens: Dict[str, TokenAdminRecord] = {}
            for key, value in tokens_payload.items():
                if not isinstance(value, Mapping):
                    continue
                record = TokenAdminRecord.from_mapping(value)
                if record.to_dict():
                    tokens[str(key)] = record
            state.tokens = tokens

        state.global_thresholds = TokenThresholds.from_mapping(payload.get("global_thresholds"))
        state.mev_buffer_bps = _maybe_float(payload.get("mev_buffer_bps"))
        default_profile_payload = payload.get("default_profile")
        if isinstance(default_profile_payload, Mapping):
            state.default_profile = {
                str(key): float(value)
                for key, value in default_profile_payload.items()
                if _is_number(value)
            }
        return state


def _maybe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _maybe_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _is_number(value: Any) -> bool:
    try:
        float(value)
        return True
    except (TypeError, ValueError):
        return False
