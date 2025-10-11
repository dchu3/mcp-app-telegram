"""Helpers for seeding admin state from legacy configuration."""

from __future__ import annotations

from typing import Iterable, Sequence

from .admin_state import AdminState, AdminStateRepository, TokenAdminRecord, TokenThresholds
from .config import ScanPairDefinition
from .infra.store import PairMetadata


def scan_pair_to_metadata(pair: ScanPairDefinition) -> PairMetadata:
    """Convert a legacy scan pair definition into stored pair metadata."""

    return PairMetadata(
        pair_key=pair.pair_key,
        symbols=pair.symbols,
        base_symbol=pair.base_symbol,
        quote_symbol=pair.quote_symbol,
        base_address=pair.base_address,
        quote_address=pair.quote_address,
        dex_id=pair.dex_id,
        fee_tiers=pair.fee_tiers,
    )


def ensure_tokens_seeded(
    *,
    repository: AdminStateRepository,
    state: AdminState,
    scan_pairs: Sequence[ScanPairDefinition],
    default_thresholds: TokenThresholds | None = None,
) -> bool:
    """Persist legacy scan pairs into SQLite if they are missing.

    Returns True when the repository was updated.
    """

    changed = False
    has_default_thresholds = (
        default_thresholds is not None and bool(default_thresholds.to_dict())
    )

    for definition in scan_pairs:
        metadata = scan_pair_to_metadata(definition)
        record = state.tokens.get(metadata.pair_key, TokenAdminRecord())

        if record.metadata != metadata:
            record.metadata = metadata
            changed = True

        if has_default_thresholds and not record.thresholds.to_dict():
            record.thresholds = TokenThresholds(
                min_liquidity_usd=default_thresholds.min_liquidity_usd,
                min_volume_24h_usd=default_thresholds.min_volume_24h_usd,
                min_txns_24h=default_thresholds.min_txns_24h,
            )
            changed = True

        state.tokens[metadata.pair_key] = record

    if changed:
        repository.save(state)

    return changed


def metadata_from_state(state: AdminState) -> list[PairMetadata]:
    """Extract pair metadata entries from the admin state."""

    return [
        record.metadata
        for record in state.tokens.values()
        if record.metadata is not None
    ]


def metadata_from_scan_pairs(pairs: Iterable[ScanPairDefinition]) -> list[PairMetadata]:
    """Build metadata objects from a legacy scan-set sequence."""

    return [scan_pair_to_metadata(pair) for pair in pairs]
