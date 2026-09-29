# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""AES-256-GCM encryption service for sensitive data.

This module provides encryption/decryption services using AES-256-GCM algorithm.
All sensitive data (API keys, passwords, credentials) should be encrypted at rest.

Format: v1:key_id:base64(nonce(12) + ciphertext + tag(16))

Key configuration contract:

- ``ATLASCLAW_ENCRYPTION_KEY``: base64 32-byte key used to encrypt new data.
  When set, data encrypted earlier with the legacy built-in key still decrypts
  (decrypt-only fallback), so no migration downtime is required.
- ``ATLASCLAW_KEY_STORE``: optional path to a JSON key store written by
  :meth:`EncryptionService.rotate_key`; loaded on startup so rotated keys
  survive process restarts.
- ``ATLASCLAW_ALLOW_INSECURE_DEFAULT_KEY=1``: explicit local-development opt-in
  to keep encrypting with the legacy public default key. Production must not
  set this.

Construction fails fast with :class:`MissingKeyError` when no key is
configured and the insecure opt-in is not set. The legacy default key is
NEVER used for new encryption unless the opt-in is present.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Optional, Union

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

logger = logging.getLogger(__name__)

# Version prefix for ciphertext format
FORMAT_VERSION = "v1"
FORMAT_PREFIX = f"{FORMAT_VERSION}:"

# Envelope encryption version
ENVELOPE_VERSION = "v2"
ENVELOPE_PREFIX = f"{ENVELOPE_VERSION}:"

# Legacy built-in default key (32 bytes, base64 encoded). It ships in the
# repository and is publicly known, so it must not protect any new data.
# It is kept loadable DECRYPT-ONLY so ciphertext written before a real key
# was configured stays readable, and for new encryption only behind the
# explicit ATLASCLAW_ALLOW_INSECURE_DEFAULT_KEY=1 opt-in.
# Generated: base64.b64encode(b"atlasclaw-default-32byte-key!!!!")
DEFAULT_ENCRYPTION_KEY = "YXRsYXNjbGF3LWRlZmF1bHQtMzJieXRlLWtleSEhISE="

# Environment variables
ENCRYPTION_KEY_ENV = "ATLASCLAW_ENCRYPTION_KEY"
KEY_STORE_ENV = "ATLASCLAW_KEY_STORE"
INSECURE_DEFAULT_KEY_ENV = "ATLASCLAW_ALLOW_INSECURE_DEFAULT_KEY"

# Key rotation support - environment variable pattern for additional keys
# Format: ATLASCLAW_ENCRYPTION_KEY_<KEY_ID>

# Guards singleton construction against concurrent check-then-act (the ORM
# thread pool can call get_encryption_service() from multiple threads).
_singleton_lock = threading.Lock()


def decode_key_b64(key_b64: str) -> bytes:
    """Decode a base64-encoded 32-byte key, rejecting malformed input.

    Args:
        key_b64: Base64-encoded key material.

    Returns:
        The raw 32-byte key.

    Raises:
        EncryptionError: If the value is not valid base64 or not 32 bytes.
    """
    try:
        key = base64.b64decode(key_b64, validate=True)
    except Exception as e:
        raise EncryptionError(f"Encryption key is not valid base64: {e}") from e
    if len(key) != 32:
        raise EncryptionError(
            f"Encryption key must be 32 bytes (256-bit), got {len(key)} bytes"
        )
    return key


class EncryptionError(Exception):
    """Raised when encryption or decryption fails."""
    pass


class MissingKeyError(EncryptionError):
    """Raised when no encryption key is configured (startup refuses to proceed)."""
    pass


class InvalidCiphertextError(EncryptionError):
    """Raised when ciphertext format is invalid or tampered."""
    pass


class EncryptionService:
    """AES-256-GCM encryption service with key rotation support.

    Keys come from (in priority order):

    1. An explicit ``key`` constructor argument (caller owns the key).
    2. ``ATLASCLAW_ENCRYPTION_KEY`` environment variable.
    3. The legacy built-in key, but ONLY when
       ``ATLASCLAW_ALLOW_INSECURE_DEFAULT_KEY=1`` is set (local development).
    4. Otherwise construction raises :class:`MissingKeyError`.

    Data written by deployments that used the legacy key keeps decrypting
    through a decrypt-only fallback, independent of the current key.

    Usage::

        service = EncryptionService()  # requires ATLASCLAW_ENCRYPTION_KEY
        encrypted = service.encrypt("sensitive data")
        decrypted = service.decrypt(encrypted)

    Key rotation::

        # 1. Rotate and persist to a key store file
        key_id = service.rotate_key(persist_path="keys.json")
        # 2. Point new processes at the store (or export the key to env)
        #    ATLASCLAW_KEY_STORE=keys.json
        # 3. Re-encrypt stored data with the new key when convenient
    """

    def __init__(self, key: bytes | None = None, key_id: str = "default") -> None:
        """Initialize encryption service.

        Args:
            key: Optional 32-byte encryption key. If not provided, keys are
                 loaded from the environment (see class docstring).
            key_id: Identifier for the explicit key (used in key rotation).

        Raises:
            MissingKeyError: If no key is provided and none is configured.
            EncryptionError: If provided key material is invalid.
        """
        self._key_id = key_id
        self._keys: dict[str, bytes] = {}
        # Keys that may decrypt but never encrypt (legacy data migration).
        self._decrypt_only_keys: dict[str, bytes] = {}
        self._current_key_id = key_id

        if key is not None:
            self._validate_key(key)
            self._keys[key_id] = key
            self._current_key_id = key_id
        else:
            self._load_keys_from_env()

        logger.debug(f"EncryptionService initialized with {len(self._keys)} key(s)")

    def _validate_key(self, key: bytes) -> None:
        """Validate key length."""
        if len(key) != 32:
            raise EncryptionError(f"Encryption key must be 32 bytes (256-bit), got {len(key)} bytes")

    def _load_keys_from_env(self) -> None:
        """Load encryption keys from the environment.

        Fails fast with MissingKeyError when no key is configured, unless the
        explicit insecure opt-in is set. The legacy built-in key is always
        registered decrypt-only so historical ciphertext stays readable.
        Additional keys: ATLASCLAW_ENCRYPTION_KEY_<key_id> and the JSON key
        store pointed at by ATLASCLAW_KEY_STORE.
        """
        legacy_key = decode_key_b64(DEFAULT_ENCRYPTION_KEY)
        env_key_b64 = os.environ.get(ENCRYPTION_KEY_ENV)

        if env_key_b64:
            current_key = decode_key_b64(env_key_b64)
            self._keys["default"] = current_key
            self._current_key_id = "default"
            logger.info("Using custom encryption key from ATLASCLAW_ENCRYPTION_KEY")
        elif os.environ.get(INSECURE_DEFAULT_KEY_ENV) == "1":
            current_key = legacy_key
            self._keys["default"] = legacy_key
            self._current_key_id = "default"
            logger.warning(
                "AtlasClaw is ENCRYPTING secrets with the built-in default key "
                "(ATLASCLAW_ALLOW_INSECURE_DEFAULT_KEY=1), which ships in the "
                "repository and is publicly known. This is only acceptable for "
                "throwaway local development. Set ATLASCLAW_ENCRYPTION_KEY to a "
                "32-byte base64 key (e.g. base64.b64encode(os.urandom(32))) for "
                "anything that persists."
            )
        else:
            raise MissingKeyError(
                "No encryption key configured. Set the ATLASCLAW_ENCRYPTION_KEY "
                "environment variable to a base64-encoded 32-byte key (generate "
                "one with: python -c \"import base64, os; "
                "print(base64.b64encode(os.urandom(32)).decode())\"). AtlasClaw "
                "refuses to protect secrets with the public built-in default "
                "key; for local development only, set "
                "ATLASCLAW_ALLOW_INSECURE_DEFAULT_KEY=1 to opt in."
            )

        # Legacy data written with the public default key must stay readable.
        # Register the legacy key decrypt-only whenever it is not the key we
        # encrypt with.
        if self._keys.get("default") != legacy_key:
            self._decrypt_only_keys["default"] = legacy_key

        # Load additional keys for rotation (ATLASCLAW_ENCRYPTION_KEY_<key_id>)
        for env_name, env_value in os.environ.items():
            if env_name.startswith(f"{ENCRYPTION_KEY_ENV}_") and env_name != ENCRYPTION_KEY_ENV:
                key_id = env_name[len(f"{ENCRYPTION_KEY_ENV}_"):]
                try:
                    self._keys[key_id] = decode_key_b64(env_value)
                    logger.debug(f"Loaded encryption key: {key_id}")
                except EncryptionError as e:
                    logger.warning(f"Failed to load key {key_id}: {e}")

        # Load the persistent key store (rotated keys survive restarts)
        store_path = os.environ.get(KEY_STORE_ENV)
        if store_path:
            self._load_key_store(Path(store_path))

    def _load_key_store(self, path: Path) -> None:
        """Load rotated keys from a JSON key store file.

        Args:
            path: Path to the key store written by rotate_key().
        """
        try:
            with open(path, "r", encoding="utf-8") as f:
                store = json.load(f)
        except FileNotFoundError:
            logger.warning(f"Key store file not found: {path}")
            return
        except (OSError, json.JSONDecodeError) as e:
            logger.warning(f"Failed to read key store {path}: {e}")
            return

        keys = store.get("keys", {})
        if not isinstance(keys, dict):
            logger.warning(f"Key store {path} has invalid 'keys' field")
            return
        for key_id, key_b64 in keys.items():
            try:
                self._keys[key_id] = decode_key_b64(key_b64)
            except EncryptionError as e:
                logger.warning(f"Failed to load key '{key_id}' from key store: {e}")
        current = store.get("current")
        if current and current in self._keys:
            self._current_key_id = current
            logger.info(f"Current encryption key restored from key store: {current}")

    def encrypt(self, plaintext: str, key_id: str | None = None) -> str:
        """Encrypt plaintext string.

        Args:
            plaintext: String to encrypt.
            key_id: Optional key ID to use. If not provided, uses the current key.

        Returns:
            Encrypted ciphertext in format: v1:key_id:base64(nonce+ciphertext+tag)

        Raises:
            MissingKeyError: If the requested key is not available.
            EncryptionError: If encryption fails.
        """
        try:
            use_key_id = key_id or self._current_key_id
            if use_key_id not in self._keys:
                raise MissingKeyError(f"Encryption key '{use_key_id}' not available")

            key = self._keys[use_key_id]
            aesgcm = AESGCM(key)

            # Generate random 12-byte nonce (96-bit for GCM)
            nonce = os.urandom(12)

            # Encrypt plaintext
            ciphertext = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)

            # Format: nonce(12) + ciphertext + tag(16)
            combined = nonce + ciphertext

            # Encode as base64 with version and key_id prefix
            return f"{FORMAT_PREFIX}{use_key_id}:{base64.b64encode(combined).decode('ascii')}"
        except EncryptionError:
            raise
        except Exception as e:
            logger.error(f"Encryption failed: {e}")
            raise EncryptionError(f"Failed to encrypt data: {e}") from e

    def decrypt(self, ciphertext: str) -> str:
        """Decrypt ciphertext string.

        Args:
            ciphertext: Encrypted string in format: v1:[key_id:]base64(nonce+ciphertext+tag)
                       (key_id is optional for backward compatibility)

        Returns:
            Decrypted plaintext string. Never returns the ciphertext itself.

        Raises:
            MissingKeyError: If the key that encrypted the data is not available.
            InvalidCiphertextError: If format is invalid or data is tampered.
        """
        try:
            # Check version prefix
            if not ciphertext.startswith(FORMAT_PREFIX):
                raise InvalidCiphertextError(
                    f"Invalid ciphertext format. Expected prefix '{FORMAT_PREFIX}'"
                )

            # Extract payload after version prefix
            payload = ciphertext[len(FORMAT_PREFIX):]

            # Check if key_id is present (new format) or not (old format)
            if ":" in payload:
                key_id, payload_b64 = payload.split(":", 1)
            else:
                # Backward compatibility: no key_id, use default
                key_id = "default"
                payload_b64 = payload

            key = self._keys.get(key_id)
            if key is None:
                key = self._decrypt_only_keys.get(key_id)
            if key is None:
                raise MissingKeyError(f"Cannot decrypt: key '{key_id}' not available")

            # Decode base64
            combined = base64.b64decode(payload_b64)

            # Extract nonce (first 12 bytes)
            if len(combined) < 28:  # 12 (nonce) + 16 (tag) minimum
                raise InvalidCiphertextError("Ciphertext too short")

            nonce = combined[:12]
            ciphertext_with_tag = combined[12:]

            # Decrypt (AESGCM.decrypt expects ciphertext with tag appended)
            try:
                plaintext = AESGCM(key).decrypt(nonce, ciphertext_with_tag, None)
            except InvalidTag:
                # The ciphertext may predate a key change (same key_id, older
                # key material). Retry once with the legacy decrypt-only key.
                fallback = self._decrypt_only_keys.get(key_id)
                if fallback is not None and fallback != key:
                    plaintext = AESGCM(fallback).decrypt(nonce, ciphertext_with_tag, None)
                else:
                    raise InvalidCiphertextError(
                        "Failed to decrypt data: authentication tag mismatch "
                        "(wrong key or tampered ciphertext)"
                    )

            return plaintext.decode("utf-8")
        except (InvalidCiphertextError, MissingKeyError):
            raise
        except InvalidTag:
            raise InvalidCiphertextError(
                "Failed to decrypt data: authentication tag mismatch "
                "(wrong key or tampered ciphertext)"
            )
        except Exception as e:
            logger.error(f"Decryption failed: {e}")
            raise InvalidCiphertextError(f"Failed to decrypt data: {e}") from e

    def encrypt_json(self, data: dict[str, Any], key_id: str | None = None) -> str:
        """Encrypt a JSON-serializable dictionary.

        Args:
            data: Dictionary to encrypt.
            key_id: Optional key ID to use.

        Returns:
            Encrypted ciphertext.
        """
        plaintext = json.dumps(data, ensure_ascii=False)
        return self.encrypt(plaintext, key_id)

    def decrypt_json(self, ciphertext: str) -> dict[str, Any]:
        """Decrypt ciphertext to a dictionary.

        Args:
            ciphertext: Encrypted JSON data.

        Returns:
            Decrypted dictionary.

        Raises:
            EncryptionError: If JSON parsing fails.
        """
        plaintext = self.decrypt(ciphertext)
        try:
            return json.loads(plaintext)
        except json.JSONDecodeError as e:
            raise EncryptionError(f"Failed to parse decrypted data as JSON: {e}") from e

    def rotate_key(
        self,
        new_key: bytes | None = None,
        persist_path: Optional[Union[str, Path]] = None,
    ) -> str:
        """Rotate to a new encryption key.

        The generated key_id embeds a timestamp plus a random suffix so two
        rotations within the same second never collide (a collision would
        overwrite the previous key and destroy data encrypted with it).

        Args:
            new_key: Optional new 32-byte key. If not provided, generates a
                     random key.
            persist_path: Optional path to a JSON key store file. When given,
                          the new key is written there (merged with existing
                          entries, atomic replace) so a restarted process with
                          ATLASCLAW_KEY_STORE pointing at the same file keeps
                          decrypting AND keeps using the rotated key.

        Returns:
            Key ID for the new key.

        Raises:
            EncryptionError: If the provided key material is invalid.

        Full rotation procedure:
            1. ``key_id = service.rotate_key(persist_path="atlasclaw-keys.json")``
            2. Set ``ATLASCLAW_KEY_STORE=atlasclaw-keys.json`` for the service
               (or export the key as ATLASCLAW_ENCRYPTION_KEY_<key_id>).
            3. Restart / re-encrypt stored data as convenient; old ciphertext
               remains readable through the legacy decrypt-only key or the
               previous keys kept in the store.

        Note: the key store file contains plaintext key material; protect it
        with filesystem permissions (0600) and never commit it.
        """
        if new_key is None:
            new_key = os.urandom(32)

        self._validate_key(new_key)

        # Timestamp + random suffix: same-second rotations must not collide.
        key_id = f"{time.strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:8]}"

        self._keys[key_id] = new_key
        self._current_key_id = key_id

        if persist_path is not None:
            self._persist_key_store(Path(persist_path), key_id, new_key)

        logger.info(f"Encryption key rotated to: {key_id}")
        return key_id

    def _persist_key_store(self, path: Path, key_id: str, key: bytes) -> None:
        """Merge the rotated key into a JSON key store file atomically."""
        store: dict[str, Any] = {"keys": {}, "current": key_id}
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    existing = json.load(f)
                if isinstance(existing, dict):
                    store["keys"] = existing.get("keys", {}) or {}
            except (OSError, json.JSONDecodeError) as e:
                logger.warning(f"Could not merge existing key store {path}: {e}")
        store["keys"][key_id] = base64.b64encode(key).decode("ascii")
        store["current"] = key_id

        tmp_path = path.with_suffix(path.suffix + ".tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(store, f, indent=2)
        os.replace(tmp_path, path)
        logger.info(f"Rotated key persisted to key store: {path}")

    def get_available_key_ids(self) -> list[str]:
        """Get list of available key IDs."""
        return list(self._keys.keys())


class EnvelopeEncryptionService:
    """Envelope encryption service using data keys encrypted by master key.

    Each encryption operation generates a unique data key.
    The data key is encrypted with the master key and stored alongside ciphertext.
    This allows for key rotation without re-encrypting all data.

    The master key comes from the ``ATLASCLAW_MASTER_KEY`` (or
    ``ATLASCLAW_ENCRYPTION_KEY``) environment variable. When neither is set,
    construction raises :class:`MissingKeyError` unless the explicit
    local-development opt-in ``ATLASCLAW_ALLOW_INSECURE_DEFAULT_KEY=1`` is
    set, in which case the legacy public default key is used with a warning.

    Format: v2:base64(encrypted_data_key + nonce + ciphertext + tag)
    """

    def __init__(self, master_key: bytes | None = None) -> None:
        """Initialize envelope encryption service.

        Args:
            master_key: Optional 32-byte master key. If not provided, uses the
                        ATLASCLAW_MASTER_KEY or ATLASCLAW_ENCRYPTION_KEY env
                        var; raises MissingKeyError when neither is configured
                        and the insecure opt-in is absent.

        Raises:
            MissingKeyError: If no master key is configured.
            EncryptionError: If the master key material is invalid.
        """
        if master_key is None:
            master_key_b64 = os.environ.get("ATLASCLAW_MASTER_KEY") or \
                           os.environ.get(ENCRYPTION_KEY_ENV)
            if master_key_b64:
                logger.info("Using master key from environment")
            elif os.environ.get(INSECURE_DEFAULT_KEY_ENV) == "1":
                master_key_b64 = DEFAULT_ENCRYPTION_KEY
                logger.warning(
                    "Envelope encryption is using the built-in default master key "
                    "(ATLASCLAW_ALLOW_INSECURE_DEFAULT_KEY=1), which ships in the "
                    "repository and is publicly known. Only acceptable for "
                    "throwaway local development."
                )
            else:
                raise MissingKeyError(
                    "No envelope master key configured. Set ATLASCLAW_MASTER_KEY "
                    "(or ATLASCLAW_ENCRYPTION_KEY) to a base64-encoded 32-byte "
                    "key. AtlasClaw refuses to protect secrets with the public "
                    "built-in default key; for local development only, set "
                    "ATLASCLAW_ALLOW_INSECURE_DEFAULT_KEY=1 to opt in."
                )
            try:
                master_key = decode_key_b64(master_key_b64)
            except EncryptionError as e:
                raise EncryptionError(f"Failed to decode master key: {e}") from e

        if len(master_key) != 32:
            raise EncryptionError(f"Master key must be 32 bytes (256-bit), got {len(master_key)} bytes")

        self._master_key = master_key
        self._master_aesgcm = AESGCM(master_key)

    def encrypt(self, plaintext: str) -> str:
        """Encrypt using envelope encryption.

        1. Generate random data key
        2. Encrypt plaintext with data key
        3. Encrypt data key with master key
        4. Combine: encrypted_data_key + nonce + ciphertext + tag

        Args:
            plaintext: String to encrypt.

        Returns:
            Encrypted ciphertext in envelope format.
        """
        try:
            # Generate random data key (32 bytes)
            data_key = os.urandom(32)

            # Encrypt data key with master key
            data_key_nonce = os.urandom(12)
            encrypted_data_key = self._master_aesgcm.encrypt(data_key_nonce, data_key, None)

            # Encrypt plaintext with data key
            data_aesgcm = AESGCM(data_key)
            plaintext_nonce = os.urandom(12)
            ciphertext = data_aesgcm.encrypt(plaintext_nonce, plaintext.encode("utf-8"), None)

            # Combine all parts
            # Format: data_key_nonce(12) + encrypted_data_key(48) + plaintext_nonce(12) + ciphertext
            combined = data_key_nonce + encrypted_data_key + plaintext_nonce + ciphertext

            return f"{ENVELOPE_PREFIX}{base64.b64encode(combined).decode('ascii')}"
        except Exception as e:
            logger.error(f"Envelope encryption failed: {e}")
            raise EncryptionError(f"Failed to encrypt data: {e}") from e

    def decrypt(self, ciphertext: str) -> str:
        """Decrypt envelope-encrypted ciphertext.

        1. Extract encrypted data key
        2. Decrypt data key with master key
        3. Decrypt ciphertext with data key

        Args:
            ciphertext: Envelope-encrypted string.

        Returns:
            Decrypted plaintext string. Never returns the ciphertext itself.

        Raises:
            InvalidCiphertextError: If format is invalid or data is tampered.
            EncryptionError: If decryption fails.
        """
        try:
            if not ciphertext.startswith(ENVELOPE_PREFIX):
                raise InvalidCiphertextError(
                    f"Invalid envelope format. Expected prefix '{ENVELOPE_PREFIX}'"
                )

            # Decode base64
            combined = base64.b64decode(ciphertext[len(ENVELOPE_PREFIX):])

            # Minimum size: 12 (dk_nonce) + 48 (encrypted_dk) + 12 (pt_nonce) + 16 (tag)
            if len(combined) < 88:
                raise InvalidCiphertextError("Envelope ciphertext too short")

            # Extract parts
            data_key_nonce = combined[:12]
            encrypted_data_key = combined[12:60]  # 48 bytes
            plaintext_nonce = combined[60:72]
            ciphertext_with_tag = combined[72:]

            # Decrypt data key
            data_key = self._master_aesgcm.decrypt(data_key_nonce, encrypted_data_key, None)

            # Decrypt plaintext with data key
            data_aesgcm = AESGCM(data_key)
            plaintext = data_aesgcm.decrypt(plaintext_nonce, ciphertext_with_tag, None)

            return plaintext.decode("utf-8")
        except (InvalidCiphertextError, EncryptionError):
            raise
        except InvalidTag:
            raise InvalidCiphertextError(
                "Failed to decrypt data: authentication tag mismatch "
                "(wrong master key or tampered ciphertext)"
            )
        except Exception as e:
            logger.error(f"Envelope decryption failed: {e}")
            raise InvalidCiphertextError(f"Failed to decrypt data: {e}") from e


# Global singleton instances
_encryption_service: EncryptionService | None = None
_envelope_service: EnvelopeEncryptionService | None = None


def get_encryption_service() -> EncryptionService:
    """Get or create the global encryption service instance.

    Returns:
        EncryptionService singleton instance.

    Raises:
        MissingKeyError: If no encryption key is configured and the insecure
                         opt-in is absent.
    """
    global _encryption_service
    if _encryption_service is None:
        with _singleton_lock:
            if _encryption_service is None:
                _encryption_service = EncryptionService()
    return _encryption_service


def get_envelope_service() -> EnvelopeEncryptionService:
    """Get or create the global envelope encryption service instance.

    Returns:
        EnvelopeEncryptionService singleton instance.

    Raises:
        MissingKeyError: If no master key is configured and the insecure
                         opt-in is absent.
    """
    global _envelope_service
    if _envelope_service is None:
        with _singleton_lock:
            if _envelope_service is None:
                _envelope_service = EnvelopeEncryptionService()
    return _envelope_service


def encrypt(plaintext: str, key_id: str | None = None) -> str:
    """Convenience function: Encrypt plaintext using global service.

    Args:
        plaintext: String to encrypt.
        key_id: Optional key ID to use.

    Returns:
        Encrypted ciphertext.
    """
    return get_encryption_service().encrypt(plaintext, key_id)


def decrypt(ciphertext: str) -> str:
    """Convenience function: Decrypt ciphertext using global service.

    Args:
        ciphertext: Encrypted ciphertext string.

    Returns:
        Decrypted plaintext string.

    Raises:
        InvalidCiphertextError: If the ciphertext is invalid or tampered.
        MissingKeyError: If the key is not available.
    """
    return get_encryption_service().decrypt(ciphertext)


def encrypt_json(data: dict[str, Any], key_id: str | None = None) -> str:
    """Convenience function: Encrypt JSON data using global service.

    Args:
        data: Dictionary to encrypt.
        key_id: Optional key ID to use.

    Returns:
        Encrypted ciphertext.
    """
    return get_encryption_service().encrypt_json(data, key_id)


def decrypt_json(ciphertext: str) -> dict[str, Any]:
    """Convenience function: Decrypt JSON data using global service.

    Args:
        ciphertext: Encrypted JSON data.

    Returns:
        Decrypted dictionary.
    """
    return get_encryption_service().decrypt_json(ciphertext)


def envelope_encrypt(plaintext: str) -> str:
    """Convenience function: Envelope encrypt plaintext.

    Args:
        plaintext: String to encrypt.

    Returns:
        Envelope-encrypted ciphertext.
    """
    return get_envelope_service().encrypt(plaintext)


def envelope_decrypt(ciphertext: str) -> str:
    """Convenience function: Envelope decrypt ciphertext.

    Args:
        ciphertext: Envelope-encrypted ciphertext string.

    Returns:
        Decrypted plaintext string.
    """
    return get_envelope_service().decrypt(ciphertext)
