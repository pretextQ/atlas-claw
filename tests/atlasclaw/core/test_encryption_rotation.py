# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""WP-02 regression tests: encryption key fail-fast, legacy decrypt fallback,
rotation persistence, and the config re-encryption migration script."""

from __future__ import annotations

import base64
import json
import logging

import pytest

from app.atlasclaw.core.encryption import (
    DEFAULT_ENCRYPTION_KEY,
    EncryptionService,
    EnvelopeEncryptionService,
    InvalidCiphertextError,
    MissingKeyError,
    INSECURE_DEFAULT_KEY_ENV,
)

_LEGACY_KEY = base64.b64decode(DEFAULT_ENCRYPTION_KEY)


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


class TestFailFast:
    def test_envelope_service_requires_master_key(self, monkeypatch):
        """WP-02/F-0003: envelope encryption must not fall back to the public key."""
        monkeypatch.delenv("ATLASCLAW_MASTER_KEY", raising=False)
        monkeypatch.delenv("ATLASCLAW_ENCRYPTION_KEY", raising=False)
        monkeypatch.delenv(INSECURE_DEFAULT_KEY_ENV, raising=False)
        with pytest.raises(MissingKeyError) as exc_info:
            EnvelopeEncryptionService()
        assert "ATLASCLAW_MASTER_KEY" in str(exc_info.value)

    def test_envelope_service_insecure_opt_in_roundtrip(self, monkeypatch):
        monkeypatch.delenv("ATLASCLAW_MASTER_KEY", raising=False)
        monkeypatch.delenv("ATLASCLAW_ENCRYPTION_KEY", raising=False)
        monkeypatch.setenv(INSECURE_DEFAULT_KEY_ENV, "1")
        service = EnvelopeEncryptionService()
        assert service.decrypt(service.encrypt("secret")) == "secret"


class TestLegacyDataDecryptable:
    def test_legacy_ciphertext_decrypts_when_env_key_set(self, monkeypatch):
        """Data written with the public legacy key must stay decryptable after
        a real key is configured (migration path, no downtime)."""
        legacy_service = EncryptionService(key=_LEGACY_KEY, key_id="default")
        legacy_ct = legacy_service.encrypt("legacy-provider-token")

        monkeypatch.setenv("ATLASCLAW_ENCRYPTION_KEY", _b64(b"a" * 32))
        monkeypatch.delenv(INSECURE_DEFAULT_KEY_ENV, raising=False)
        service = EncryptionService()

        assert service.decrypt(legacy_ct) == "legacy-provider-token"
        # New data must NOT use the legacy key.
        new_ct = service.encrypt("fresh-data")
        key_id = new_ct.split(":")[1]
        assert service._keys[key_id] != _LEGACY_KEY

    def test_legacy_ciphertext_decrypts_with_insecure_opt_in(self, monkeypatch):
        monkeypatch.delenv("ATLASCLAW_ENCRYPTION_KEY", raising=False)
        monkeypatch.setenv(INSECURE_DEFAULT_KEY_ENV, "1")
        legacy_service = EncryptionService(key=_LEGACY_KEY, key_id="default")
        legacy_ct = legacy_service.encrypt("legacy-provider-token")
        assert EncryptionService().decrypt(legacy_ct) == "legacy-provider-token"


class TestWrongKey:
    def test_wrong_key_raises_and_never_returns_ciphertext(self, monkeypatch):
        monkeypatch.setenv("ATLASCLAW_ENCRYPTION_KEY", _b64(b"b" * 32))
        service = EncryptionService()
        ciphertext = service.encrypt("top secret")

        other = EncryptionService(key=_b64_and_decode(b"c" * 32))
        with pytest.raises(InvalidCiphertextError):
            result = other.decrypt(ciphertext)
            assert result != ciphertext

    def test_unknown_key_id_raises_missing_key(self, monkeypatch):
        monkeypatch.setenv("ATLASCLAW_ENCRYPTION_KEY", _b64(b"b" * 32))
        service = EncryptionService()
        with pytest.raises(MissingKeyError):
            service.decrypt("v1:no-such-key:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")


def _b64_and_decode(raw: bytes) -> bytes:
    """Round-trip through base64 so the explicit key path is exercised."""
    return base64.b64decode(_b64(raw))


class TestKeyRotation:
    def test_same_second_rotations_get_distinct_key_ids(self, monkeypatch):
        """WP-02/F-0039: two rotations within one second must not collide."""
        monkeypatch.setenv("ATLASCLAW_ENCRYPTION_KEY", _b64(b"b" * 32))
        service = EncryptionService()

        first_id = service.rotate_key()
        ct_between = service.encrypt("data-after-rotation-1")
        second_id = service.rotate_key()

        assert first_id != second_id
        assert service.decrypt(ct_between) == "data-after-rotation-1"
        assert service.encrypt("data-after-rotation-2").startswith(f"v1:{second_id}:")

    def test_rotate_key_persists_across_restart(self, monkeypatch, tmp_path):
        """WP-02/F-0040: a rotated key survives a process restart via the
        key store file, and stays the current encryption key."""
        store = tmp_path / "atlasclaw-keys.json"
        monkeypatch.setenv("ATLASCLAW_ENCRYPTION_KEY", _b64(b"b" * 32))
        monkeypatch.delenv("ATLASCLAW_KEY_STORE", raising=False)

        service = EncryptionService()
        key_id = service.rotate_key(persist_path=store)
        ciphertext = service.encrypt("survives-restart")

        # Simulate a restarted process that only has env + key store.
        monkeypatch.setenv("ATLASCLAW_KEY_STORE", str(store))
        fresh = EncryptionService()

        assert fresh.decrypt(ciphertext) == "survives-restart"
        assert fresh._current_key_id == key_id
        assert fresh.encrypt("after-restart").startswith(f"v1:{key_id}:")

    def test_invalid_base64_key_material_rejected(self, monkeypatch):
        monkeypatch.setenv("ATLASCLAW_ENCRYPTION_KEY", "not-valid-base64!!!")
        with pytest.raises(Exception) as exc_info:
            EncryptionService()
        assert "base64" in str(exc_info.value).lower()


class TestReencryptScript:
    @pytest.fixture
    def script_module(self):
        import importlib.util
        import sys
        from pathlib import Path

        script_path = Path(__file__).parents[3] / "scripts" / "reencrypt_secrets.py"
        spec = importlib.util.spec_from_file_location("reencrypt_secrets", script_path)
        module = importlib.util.module_from_spec(spec)
        sys.modules["reencrypt_secrets"] = module
        spec.loader.exec_module(module)
        return module

    def _write_config(self, path, legacy_service):
        document = {
            "model": {
                "providers": {
                    "deepseek": {"api_key": "enc:" + legacy_service.encrypt("sk-legacy-123")},
                }
            },
            "server": {"port": 8000, "plain": "not-a-secret"},
            "list_of_secrets": ["enc:" + legacy_service.encrypt("sk-list-456")],
        }
        path.write_text(json.dumps(document, indent=2), encoding="utf-8")
        return document

    def test_reencrypt_dry_run_and_write(self, script_module, monkeypatch, tmp_path):
        legacy_service = EncryptionService(key=_LEGACY_KEY, key_id="default")
        config_path = tmp_path / "atlasclaw.json"
        original = self._write_config(config_path, legacy_service)
        original_text = config_path.read_text(encoding="utf-8")

        new_key_b64 = _b64(b"d" * 32)

        # Dry-run: report without touching the file.
        rc = script_module.main([
            str(config_path), "--new-key-b64", new_key_b64,
        ])
        assert rc == 0
        assert config_path.read_text(encoding="utf-8") == original_text

        # Write: secrets re-encrypted with the new key, plaintext untouched.
        rc = script_module.main([
            str(config_path), "--new-key-b64", new_key_b64, "--write",
        ])
        assert rc == 0

        new_document = json.loads(config_path.read_text(encoding="utf-8"))
        assert new_document["server"] == original["server"]
        new_service = EncryptionService(key=base64.b64decode(new_key_b64))
        api_key = new_document["model"]["providers"]["deepseek"]["api_key"]
        assert api_key.startswith("enc:v1:")
        assert new_service.decrypt(api_key[len("enc:"):]) == "sk-legacy-123"
        list_secret = new_document["list_of_secrets"][0]
        assert new_service.decrypt(list_secret[len("enc:"):]) == "sk-list-456"

    def test_reencrypt_requires_new_key(self, script_module, monkeypatch, tmp_path):
        monkeypatch.delenv("ATLASCLAW_ENCRYPTION_KEY", raising=False)
        legacy_service = EncryptionService(key=_LEGACY_KEY, key_id="default")
        config_path = tmp_path / "atlasclaw.json"
        self._write_config(config_path, legacy_service)
        assert script_module.main([str(config_path)]) == 2
