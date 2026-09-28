# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Token health interceptor for response headers."""

from __future__ import annotations

import asyncio
from typing import Optional

from app.atlasclaw.core.token_health_store import TokenHealthStore
from app.atlasclaw.core.token_pool import TokenPool


class TokenHealthInterceptor:
    """Extract rate-limit headers and update token health state.

    Health snapshots are persisted off the event loop: each save schedules a
    background task running ``TokenHealthStore.save`` in a worker thread, and
    saves are chained so snapshots cannot be written out of order.
    """

    def __init__(self, token_pool: TokenPool, health_store: TokenHealthStore) -> None:
        self.token_pool = token_pool
        self.health_store = health_store
        self._save_task: Optional[asyncio.Task] = None

    def on_response(self, token_id: str, headers: dict[str, str]) -> None:
        lowered = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
        if not any(k.startswith("x-ratelimit-") for k in lowered.keys()):
            return
        self.token_pool.update_token_health(token_id, lowered)
        self._schedule_save()

    def on_hard_failure(self, token_id: str, error: str) -> None:
        """Persist a provider-level hard failure so selection can fail over quickly."""
        self.token_pool.mark_token_unhealthy(token_id, reason=error)
        self._schedule_save()

    def _schedule_save(self) -> None:
        """Persist the current health snapshot without blocking the loop."""
        snapshot = self.token_pool.export_health_status()
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No running loop (startup or sync context): write synchronously.
            self.health_store.save(snapshot)
            return

        previous = self._save_task

        async def _save_chained() -> None:
            if previous is not None:
                try:
                    await previous
                except asyncio.CancelledError:
                    raise
                except Exception:
                    pass
            await asyncio.to_thread(self.health_store.save, snapshot)

        self._save_task = loop.create_task(_save_chained())
