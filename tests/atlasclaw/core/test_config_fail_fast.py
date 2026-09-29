# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""WP-11 regression tests: configuration loading must not hide failures.

Covers: invalid JSON fails fast (F-0035), validation failures fail fast
instead of silently running on defaults (F-0036), and an ``enc:`` value
that cannot be decrypted raises instead of being returned as plaintext
(F-0037/F-0038). An explicit lenient switch keeps the old behavior
available for legacy deployments.
"""

from __future__ import annotations

import base64
import json
import logging

import pytest

from app.atlasclaw.core.config import (
    LENIENT_CONFIG_ENV,
    ConfigError,
    ConfigManager,
)
from app.atlasclaw.core.encryption import EncryptionService
from app.atlasclaw.core.config_schema import AtlasClawConfig


@pytest.fixture(autouse=True)
def _no_lenient_opt_in(monkeypatch):
    monkeypatch.delenv(LENIENT_CONFIG_ENV, raising=False)


class TestInvalidJson:
    def test_invalid_json_fails_fast(self, tmp_path):
        config_path = tmp_path / "atlasclaw.json"
        config_path.write_text('{"server": {"port": 8000},,,}', encoding="utf-8")

        with pytest.raises(ConfigError) as exc_info:
            ConfigManager(config_path=str(config_path)).load()
        assert "Failed to read config file" in str(exc_info.value)

    def test_invalid_json_with_lenient_opt_in_uses_defaults(self, tmp_path, monkeypatch, caplog):
        monkeypatch.setenv(LENIENT_CONFIG_ENV, "1")
        config_path = tmp_path / "atlasclaw.json"
        config_path.write_text('{"broken": ', encoding="utf-8")

        with caplog.at_level(logging.ERROR, logger="app.atlasclaw.core.config"):
            config = ConfigManager(config_path=str(config_path)).load()

        assert isinstance(config, AtlasClawConfig)
        assert any("continuing with defaults" in record.getMessage() for record in caplog.records)

    def test_missing_file_still_uses_defaults(self, tmp_path):
        """A path that does not exist is a normal 'no config' case."""
        config = ConfigManager(config_path=str(tmp_path / "absent.json")).load()
        assert isinstance(config, AtlasClawConfig)


class TestValidationFailure:
    def test_invalid_values_fail_fast(self, tmp_path):
        config_path = tmp_path / "atlasclaw.json"
        config_path.write_text(
            json.dumps({"agent_defaults": {"timeout_seconds": "not-a-number"}}),
            encoding="utf-8",
        )

        with pytest.raises(ConfigError) as exc_info:
            ConfigManager(config_path=str(config_path)).load()
        assert "validation failed" in str(exc_info.value).lower()

    def test_invalid_values_with_lenient_opt_in_use_defaults(self, tmp_path, monkeypatch, caplog):
        monkeypatch.setenv(LENIENT_CONFIG_ENV, "1")
        config_path = tmp_path / "atlasclaw.json"
        config_path.write_text(
            json.dumps({"agent_defaults": {"timeout_seconds": "not-a-number"}}),
            encoding="utf-8",
        )

        with caplog.at_level(logging.ERROR, logger="app.atlasclaw.core.config"):
            config = ConfigManager(config_path=str(config_path)).load()

        assert isinstance(config, AtlasClawConfig)
        assert config.agent_defaults.timeout_seconds == 600


class TestEncryptedValues:
    def _config_with_secret(self, tmp_path, ciphertext_value: str):
        config_path = tmp_path / "atlasclaw.json"
        config_path.write_text(
            json.dumps(
                {
                    "model": {
                        "providers": {
                            "deepseek": {
                                "base_url": "https://api.deepseek.com",
                                "api_key": ciphertext_value,
                                "api_type": "openai",
                            }
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        return config_path

    def test_decrypt_failure_raises_instead_of_returning_ciphertext(self, tmp_path):
        """A different key must not turn the ciphertext into the api_key value."""
        other_service = EncryptionService(key=base64.b64decode(base64.b64encode(b"z" * 32)))
        encrypted = other_service.encrypt("sk-real-secret")
        config_path = self._config_with_secret(tmp_path, f"enc:{encrypted}")

        with pytest.raises(ConfigError) as exc_info:
            ConfigManager(config_path=str(config_path)).load()
        assert "corrupted" in str(exc_info.value) or "different key" in str(exc_info.value)
        assert "sk-real-secret" not in str(exc_info.value)

    def test_unavailable_key_is_reported_as_key_problem(self, tmp_path):
        """A missing key is reported distinctly from a corrupted ciphertext."""
        config_path = self._config_with_secret(
            tmp_path, "enc:v1:key-that-is-not-loaded:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        )

        with pytest.raises(ConfigError) as exc_info:
            ConfigManager(config_path=str(config_path)).load()
        assert "not available" in str(exc_info.value)

    def test_valid_ciphertext_still_decrypts(self, tmp_path, monkeypatch):
        from app.atlasclaw.core import encryption as encryption_module

        key_b64 = base64.b64encode(b"q" * 32).decode()
        monkeypatch.setenv("ATLASCLAW_ENCRYPTION_KEY", key_b64)
        # Rebuild the process-wide singleton so this test and the config
        # loader share the same key (earlier tests already created it).
        monkeypatch.setattr(encryption_module, "_encryption_service", None)
        encrypted = encryption_module.get_encryption_service().encrypt("sk-real-secret")
        config_path = self._config_with_secret(tmp_path, f"enc:{encrypted}")

        config = ConfigManager(config_path=str(config_path)).load()
        assert config.model.providers["deepseek"]["api_key"] == "sk-real-secret"


class TestUnsetEnvPlaceholder:
    def test_unset_placeholder_logs_a_warning(self, tmp_path, monkeypatch, caplog):
        monkeypatch.delenv("ATLASCLAW_UNSET_PLACEHOLDER_XYZ", raising=False)
        config_path = tmp_path / "atlasclaw.json"
        config_path.write_text(
            json.dumps(
                {
                    "model": {
                        "providers": {
                            "deepseek": {
                                "base_url": "${ATLASCLAW_UNSET_PLACEHOLDER_XYZ}",
                                "api_key": "dummy",
                                "api_type": "openai",
                            }
                        }
                    }
                }
            ),
            encoding="utf-8",
        )

        with caplog.at_level(logging.WARNING, logger="app.atlasclaw.core.config"):
            config = ConfigManager(config_path=str(config_path)).load()

        assert config.model.providers["deepseek"]["base_url"] == ""
        assert any(
            "ATLASCLAW_UNSET_PLACEHOLDER_XYZ" in record.getMessage()
            for record in caplog.records
        )


class TestUnsetPlaceholderValues:
    """A value that is only unset because its ${VAR} is missing must not make
    an otherwise valid configuration unusable (the shipped atlasclaw.json
    fills required token fields from env vars)."""

    def test_typed_field_from_unset_placeholder_uses_its_default(self, tmp_path, monkeypatch, caplog):
        monkeypatch.delenv("ATLASCLAW_TEST_TEMPERATURE", raising=False)
        config_path = tmp_path / "atlasclaw.json"
        config_path.write_text(
            json.dumps({"model": {"temperature": "${ATLASCLAW_TEST_TEMPERATURE}"}}),
            encoding="utf-8",
        )

        with caplog.at_level(logging.WARNING, logger="app.atlasclaw.core.config"):
            config = ConfigManager(config_path=str(config_path)).load()

        assert config.model.temperature == 0.7
        assert any(
            "placeholder is not set" in record.getMessage()
            for record in caplog.records
        )

    def test_real_invalid_value_still_fails_fast(self, tmp_path):
        """A written-out bad value is not excused by the pruning path."""
        config_path = tmp_path / "atlasclaw.json"
        config_path.write_text(
            json.dumps({"model": {"temperature": "not-a-number"}}),
            encoding="utf-8",
        )

        with pytest.raises(ConfigError):
            ConfigManager(config_path=str(config_path)).load()

    def test_unset_placeholder_in_string_field_keeps_the_entry_readable(self, tmp_path, monkeypatch, caplog):
        monkeypatch.delenv("ATLASCLAW_TEST_TOKEN_PROVIDER", raising=False)
        config_path = tmp_path / "atlasclaw.json"
        config_path.write_text(
            json.dumps(
                {
                    "model": {
                        "tokens": [
                            {
                                "id": "t1",
                                "provider": "${ATLASCLAW_TEST_TOKEN_PROVIDER}",
                                "model": "m",
                                "base_url": "https://example.invalid",
                                "api_key": "k",
                            }
                        ]
                    }
                }
            ),
            encoding="utf-8",
        )

        with caplog.at_level(logging.WARNING, logger="app.atlasclaw.core.config"):
            config = ConfigManager(config_path=str(config_path)).load()

        # An empty string is a valid string, so the entry still loads (the
        # legacy behaviour) and the unset variable is reported loudly instead
        # of silently disabling the whole configuration.
        assert len(config.model.tokens) == 1
        assert config.model.tokens[0].provider == ""
        assert any(
            "ATLASCLAW_TEST_TOKEN_PROVIDER" in record.getMessage()
            for record in caplog.records
        )
