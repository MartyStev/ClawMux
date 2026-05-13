import pytest

from src.services.mapping import (
    DEFAULT_PROVIDER,
    MappingStorage,
    UnsupportedProviderError,
)


def test_default_provider_is_supported() -> None:
    assert MappingStorage._validate_provider(DEFAULT_PROVIDER) == DEFAULT_PROVIDER


def test_provider_is_normalized() -> None:
    assert MappingStorage._validate_provider(" Mattermost ") == DEFAULT_PROVIDER


def test_unsupported_provider_fails_explicitly() -> None:
    with pytest.raises(UnsupportedProviderError):
        MappingStorage._validate_provider("slack")
