import pytest

from src.services.mapping import (
    DEFAULT_PROVIDER,
    MappingStorage,
    UnsupportedProviderError,
    _external_id_cache,
    _identity_cache,
)


def test_default_provider_is_supported() -> None:
    assert MappingStorage._validate_provider(DEFAULT_PROVIDER) == DEFAULT_PROVIDER


def test_cache_invalidation_clears_internal_caches() -> None:
    _identity_cache[('mattermost', 'user-1')] = 'cached'
    _external_id_cache[('user-ext-1', 'mattermost')] = 'cached'

    storage = MappingStorage()
    storage.invalidate_cache()

    assert len(_identity_cache) == 0
    assert len(_external_id_cache) == 0


def test_provider_is_normalized() -> None:
    assert MappingStorage._validate_provider(" Mattermost ") == DEFAULT_PROVIDER


def test_unsupported_provider_fails_explicitly() -> None:
    with pytest.raises(UnsupportedProviderError):
        MappingStorage._validate_provider("slack")
