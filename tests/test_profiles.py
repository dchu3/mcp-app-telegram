import pytest

from mcp_app_telegram.arb.profiles import ArbProfile, ProfileService
from mcp_app_telegram.infra.store import InMemoryStore


@pytest.mark.asyncio
async def test_profile_defaults_and_update():
    store = InMemoryStore()
    defaults = ArbProfile(min_net_bps=25.0, min_net_eur=1.0, test_size_eur=400.0)
    service = ProfileService(store, default_profile=defaults)

    profile = await service.get(111)
    assert profile.min_net_bps == 25.0
    assert profile.test_size_eur == 400.0

    updated = await service.update(111, min_net_bps=30.0, cooldown_seconds=60)
    assert updated.min_net_bps == 30.0
    assert updated.cooldown_seconds == 60

    refreshed = await service.get(111)
    assert refreshed.min_net_bps == 30.0
    assert refreshed.cooldown_seconds == 60


@pytest.mark.asyncio
async def test_profile_update_does_not_mutate_default():
    store = InMemoryStore()
    defaults = ArbProfile(min_net_bps=22.0, cooldown_seconds=180)
    service = ProfileService(store, default_profile=defaults)

    await service.update(200, min_net_bps=40.0, cooldown_seconds=90)

    # Original defaults remain unchanged
    assert defaults.min_net_bps == 22.0
    assert defaults.cooldown_seconds == 180

    # A different chat receives fresh defaults
    other_profile = await service.get(201)
    assert other_profile.min_net_bps == 22.0
    assert other_profile.cooldown_seconds == 180
    assert other_profile is not defaults
