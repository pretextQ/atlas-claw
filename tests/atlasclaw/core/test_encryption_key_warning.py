# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Tests for the default encryption key warning."""

from __future__ import annotations

import logging

import pytest

from app.atlasclaw.core.encryption import (
    EnvelopeEncryptionService,
    EncryptionService,
)


class TestDefaultKeyWarning:
    def test_default_key_emits_warning(self, monkeypatch, caplog):
        monkeypatch.delenv("ATLASCLAW_ENCRYPTION_KEY", raising=False)
        with caplog.at_level(logging.WARNING, logger="app.atlasclaw.core.encryption"):
            EncryptionService()
        assert any("built-in default key" in record.message for record in caplog.records)

    def test_custom_key_does_not_warn(self, monkeypatch, caplog):
        import base64

        custom = base64.b64encode(b"0123456789abcdef0123456789abcdef").decode()
        monkeypatch.setenv("ATLASCLAW_ENCRYPTION_KEY", custom)
        with caplog.at_level(logging.WARNING, logger="app.atlasclaw.core.encryption"):
            EncryptionService()
        assert not any("built-in default key" in record.message for record in caplog.records)

    def test_envelope_default_key_emits_warning(self, monkeypatch, caplog):
        monkeypatch.delenv("ATLASCLAW_MASTER_KEY", raising=False)
        monkeypatch.delenv("ATLASCLAW_ENCRYPTION_KEY", raising=False)
        with caplog.at_level(logging.WARNING, logger="app.atlasclaw.core.encryption"):
            EnvelopeEncryptionService()
        assert any(
            "default master key" in record.message for record in caplog.records
        )

    def test_encrypt_decrypt_roundtrip_still_works(self, monkeypatch):
        monkeypatch.delenv("ATLASCLAW_ENCRYPTION_KEY", raising=False)
        service = EncryptionService()
        ciphertext = service.encrypt("provider-token")
        assert service.decrypt(ciphertext) == "provider-token"
