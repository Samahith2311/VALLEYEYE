from __future__ import annotations

import logging

import pytest
from pydantic import SecretStr, ValidationError

from valleyeye.core.logging import SecretRedactionFilter
from valleyeye.core.settings import Settings


def test_log_filter_redacts_secrets() -> None:
    record = logging.LogRecord(
        "test", logging.INFO, "test.py", 1, "password=%s token=%s", ("secret1", "secret2"), None
    )

    SecretRedactionFilter().filter(record)

    assert "secret1" not in record.getMessage()
    assert "secret2" not in record.getMessage()
    assert "[REDACTED]" in record.getMessage()


def test_public_settings_never_include_credentials() -> None:
    settings = Settings(CDSE_USERNAME="sam", CDSE_PASSWORD=SecretStr("top-secret"))

    public = str(settings.public_config())

    assert "'cdse_username': 'sam'" not in public
    assert "top-secret" not in public
    assert settings.public_config()["cdse_username_configured"] is True


def test_credentials_must_be_configured_together() -> None:
    with pytest.raises(ValidationError):
        Settings(CDSE_USERNAME="sam")


def test_invalid_pair_weights_are_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(pair_weight_overlap=0.5)
