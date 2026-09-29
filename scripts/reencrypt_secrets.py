#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Re-encrypt ``enc:`` secrets in a JSON config file under a new key.

Migration path away from the legacy built-in default encryption key
(WP-02). Run the three steps in order:

1. Generate and set the new key::

       python -c "import base64, os; print(base64.b64encode(os.urandom(32)).decode())"
       export ATLASCLAW_ENCRYPTION_KEY=<new-key>

2. Re-encrypt stored secrets (dry-run first, then write)::

       python scripts/reencrypt_secrets.py atlasclaw.json
       python scripts/reencrypt_secrets.py atlasclaw.json --write

   Old ciphertext encrypted with the legacy built-in key stays decryptable
   even before this step (decrypt-only fallback), but should be migrated.

3. Once step 2 reported every secret re-encrypted, remove the legacy
   fallback from your deployment notes: never set
   ATLASCLAW_ALLOW_INSECURE_DEFAULT_KEY in production.

The script walks every string value in the JSON document, decrypts values
with the ``enc:v1:`` prefix using the old key, re-encrypts them with the
new key, and (with ``--write``) replaces the file atomically.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.atlasclaw.core.encryption import (
    DEFAULT_ENCRYPTION_KEY,
    EncryptionService,
    EncryptionError,
    decode_key_b64,
    FORMAT_PREFIX,
)

ENC_PREFIX = "enc:"


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("config", help="Path to the JSON config file (e.g. atlasclaw.json)")
    parser.add_argument(
        "--old-key-b64",
        default=None,
        help="Base64 32-byte key the secrets were encrypted with. "
        "Defaults to the legacy built-in default key.",
    )
    parser.add_argument(
        "--new-key-b64",
        default=None,
        help="Base64 32-byte key to re-encrypt with. Defaults to "
        "$ATLASCLAW_ENCRYPTION_KEY.",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Write the re-encrypted file back (default: dry-run, print only)",
    )
    return parser.parse_args(argv)


def reencrypt_value(value: str, old_service: EncryptionService, new_service: EncryptionService,
                    stats: dict[str, int]) -> str:
    """Re-encrypt a single string value if it holds an enc: ciphertext."""
    if not (value.startswith(ENC_PREFIX) and FORMAT_PREFIX in value):
        return value
    payload = value[len(ENC_PREFIX):]
    plaintext = old_service.decrypt(payload)
    stats["reencrypted"] += 1
    return ENC_PREFIX + new_service.encrypt(plaintext)


def walk(obj: Any, old_service: EncryptionService, new_service: EncryptionService,
         stats: dict[str, int]) -> Any:
    """Recursively re-encrypt enc: values in dicts, lists, and strings."""
    if isinstance(obj, str):
        return reencrypt_value(obj, old_service, new_service, stats)
    if isinstance(obj, dict):
        return {k: walk(v, old_service, new_service, stats) for k, v in obj.items()}
    if isinstance(obj, list):
        return [walk(v, old_service, new_service, stats) for v in obj]
    return obj


def main(argv: Optional[list[str]] = None) -> int:
    """Re-encrypt all enc: values in the config file."""
    args = parse_args(argv)

    old_key_b64 = args.old_key_b64 or DEFAULT_ENCRYPTION_KEY
    new_key_b64 = args.new_key_b64 or os.environ.get("ATLASCLAW_ENCRYPTION_KEY")
    if not new_key_b64:
        print("ERROR: no new key. Pass --new-key-b64 or set ATLASCLAW_ENCRYPTION_KEY.",
              file=sys.stderr)
        return 2

    old_service = EncryptionService(key=decode_key_b64(old_key_b64))
    new_service = EncryptionService(key=decode_key_b64(new_key_b64))

    config_path = Path(args.config)
    with open(config_path, "r", encoding="utf-8") as f:
        document = json.load(f)

    stats = {"reencrypted": 0}
    new_document = walk(document, old_service, new_service, stats)

    print(f"Found and re-encrypted {stats['reencrypted']} enc: secret(s) in {config_path}")
    if not args.write:
        print("Dry-run: file NOT modified. Re-run with --write to apply.")
        return 0
    if stats["reencrypted"] == 0:
        print("Nothing to write: no enc: values found.")
        return 0

    tmp_path = config_path.with_suffix(config_path.suffix + ".reencrypt.tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(new_document, f, indent=2, ensure_ascii=False)
    os.replace(tmp_path, config_path)
    print(f"Wrote re-encrypted config to {config_path} (atomic replace).")
    print("Next: keep the old key available until you have verified every "
          "secret still decrypts, then remove any legacy-key fallback.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except EncryptionError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
