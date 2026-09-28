# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Tests for request-scoped database transaction ownership."""

from __future__ import annotations

from typing import AsyncGenerator

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.atlasclaw.bootstrap.app_factory_helpers import DatabaseSessionMiddleware
from app.atlasclaw.db.database import get_db_session


class _FailingCommitSession:
    """Session double whose commit always fails."""

    def __init__(self) -> None:
        self.rolled_back = False
        self.closed = False

    async def commit(self) -> None:
        raise RuntimeError("commit failed")

    async def rollback(self) -> None:
        self.rolled_back = True

    async def close(self) -> None:
        self.closed = True


class _RecordingSession:
    """Session double recording the commit/close sequence."""

    def __init__(self, events: list[str]) -> None:
        self._events = events

    async def commit(self) -> None:
        self._events.append("commit")

    async def rollback(self) -> None:
        self._events.append("rollback")

    async def close(self) -> None:
        self._events.append("close")


class _StubManager:
    def __init__(self, session_factory) -> None:
        self.is_initialized = True
        self._session_factory = session_factory

    def new_session(self):
        return self._session_factory()


def _build_app(manager, *, fail_route: bool = False) -> FastAPI:
    app = FastAPI()
    app.add_middleware(DatabaseSessionMiddleware)

    @app.post("/write")
    async def write(session: AsyncSession = Depends(get_db_session)) -> dict:
        if fail_route:
            raise RuntimeError("handler failed")
        return {"session_committed_before_response": True}

    return app


def test_commit_failure_surfaces_as_request_error(monkeypatch):
    """A failing commit must fail the request, not report success."""
    session = _FailingCommitSession()
    manager = _StubManager(lambda: session)
    monkeypatch.setattr("app.atlasclaw.db.get_db_manager", lambda: manager)

    client = TestClient(_build_app(manager), raise_server_exceptions=False)

    response = client.post("/write")

    # The client is told the write failed instead of receiving a success
    # status for a transaction that was rolled back.
    assert response.status_code == 500
    assert session.rolled_back is True
    assert session.closed is True


def test_teardown_commit_would_report_false_success(monkeypatch):
    """Documents why the request-scoped commit is needed.

    Committing when the dependency's teardown runs happens after the response
    has been sent, so a failed transaction is invisible to the caller.
    """
    app = FastAPI()
    session = _FailingCommitSession()

    async def teardown_committing_session():
        yield session
        await session.commit()

    @app.post("/write")
    async def write(session: AsyncSession = Depends(teardown_committing_session)) -> dict:
        del session
        return {"ok": True}

    client = TestClient(app, raise_server_exceptions=False)
    response = client.post("/write")

    assert response.status_code == 200  # the very bug this change removes
    assert session.rolled_back is False


def test_handler_failure_rolls_back_without_committing(monkeypatch):
    events: list[str] = []
    manager = _StubManager(lambda: _RecordingSession(events))
    monkeypatch.setattr("app.atlasclaw.db.get_db_manager", lambda: manager)

    client = TestClient(_build_app(manager, fail_route=True), raise_server_exceptions=True)

    with pytest.raises(RuntimeError, match="handler failed"):
        client.post("/write")

    assert events == ["rollback"]


def test_successful_request_commits_before_returning(monkeypatch):
    events: list[str] = []
    manager = _StubManager(lambda: _RecordingSession(events))
    monkeypatch.setattr("app.atlasclaw.db.get_db_manager", lambda: manager)

    client = TestClient(_build_app(manager))

    response = client.post("/write")

    assert response.status_code == 200
    assert events[0] == "commit"
    assert events[-1] == "close"


def test_dependency_prefers_request_scoped_session(monkeypatch):
    """Routes receive the middleware-owned session, not a fresh one."""
    from types import SimpleNamespace

    scoped = _RecordingSession([])

    async def _collect():
        request = SimpleNamespace(state=SimpleNamespace(db_session=scoped))
        generator: AsyncGenerator[AsyncSession, None] = get_db_session(request)
        session = await generator.__anext__()
        assert session is scoped
        await generator.aclose()

    import asyncio

    asyncio.run(_collect())


def test_dependency_falls_back_without_request_session(monkeypatch):
    """Direct calls without a request-scoped session still get a session."""
    from types import SimpleNamespace

    events: list[str] = []

    class _Manager:
        is_initialized = True

        def new_session(self):  # pragma: no cover - not used by the fallback
            return _RecordingSession(events)

        def get_session(self):
            session = _RecordingSession(events)

            class _Ctx:
                async def __aenter__(self_inner):
                    events.append("enter")
                    return session

                async def __aexit__(self_inner, *exc_info):
                    events.append("exit")
                    return False

            return _Ctx()

    monkeypatch.setattr("app.atlasclaw.db.database.get_db_manager", lambda: _Manager())

    async def _collect():
        request = SimpleNamespace(state=SimpleNamespace())
        generator: AsyncGenerator[AsyncSession, None] = get_db_session(request)
        session = await generator.__anext__()
        assert isinstance(session, _RecordingSession)
        await generator.aclose()

    import asyncio

    asyncio.run(_collect())

    assert events == ["enter", "exit"]
