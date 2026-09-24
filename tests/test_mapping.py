import pytest

from src.services.mapping import (
    DEFAULT_PROVIDER,
    MappingStorage,
    UnsupportedProviderError,
)


def test_default_provider_is_supported() -> None:
    assert MappingStorage._validate_provider(DEFAULT_PROVIDER) == DEFAULT_PROVIDER


def test_cache_invalidation_clears_internal_caches() -> None:
    storage = MappingStorage()
    storage._identity_cache[("mattermost", "user-1")] = (1, 9e18, "cached")
    storage._external_id_cache[("user-ext-1", "mattermost")] = (1, 9e18, "cached")

    storage.invalidate_cache()

    assert len(storage._identity_cache) == 0
    assert len(storage._external_id_cache) == 0


def test_provider_is_normalized() -> None:
    assert MappingStorage._validate_provider(" Mattermost ") == DEFAULT_PROVIDER


def test_unsupported_provider_fails_explicitly() -> None:
    with pytest.raises(UnsupportedProviderError):
        MappingStorage._validate_provider("unsupported_service_xyz")
