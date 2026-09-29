# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Channel manager for managing channel connections."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from .handler import ChannelHandler
from .models import ChannelConnection, ConnectionStatus, InboundMessage, OutboundMessage
from .registry import ChannelRegistry
from app.atlasclaw.auth.models import UserInfo
from app.atlasclaw.db.orm.channel_config import ChannelConfigService
from app.atlasclaw.session.context import ChatType, SessionKey, SessionScope

if TYPE_CHECKING:
    from app.atlasclaw.agent.runner import AgentRunner
    from app.atlasclaw.core.deps import SkillDeps
    from app.atlasclaw.session.router import SessionManagerRouter

logger = logging.getLogger(__name__)


class ChannelManager:
    """Manager for channel connections lifecycle."""

    # Stable text sent to external group chats when a turn fails; the raw
    # error stays in the server log.
    USER_FACING_ERROR_TEXT = "抱歉，处理这条消息时出错了，请稍后重试。"

    def __init__(self, workspace_path: Path):
        """Initialize channel manager.

        Args:
            workspace_path: Path to workspace directory (kept for compatibility)
        """
        self._workspace_path = workspace_path
        self._active_connections: Dict[str, ChannelHandler] = {}
        self._runtime_status_by_connection_id: Dict[str, ConnectionStatus] = {}
        self._agent_runner: Optional["AgentRunner"] = None
        self._session_manager_router: Optional["SessionManagerRouter"] = None
        self._event_loop: Optional[asyncio.AbstractEventLoop] = None
        self._background_tasks: set = set()
        # In-flight connection initializations keyed by instance key; shared so
        # concurrent enable/create requests cannot start two handlers.
        self._initializing: Dict[str, asyncio.Task] = {}
        # Connections with a pending stop request: an initialization that
        # completes after the stop must not leave a live handler behind.
        self._stop_intents: set[str] = set()
        # instance key -> (user_id, channel_type, connection_id), so shutdown
        # can act on connections without parsing the opaque key.
        self._active_connection_coordinates: Dict[str, tuple[str, str, str]] = {}

    def _set_connection_runtime_status(
        self,
        connection_id: str,
        status: ConnectionStatus,
    ) -> None:
        """Persist the latest known runtime status for a connection."""
        self._runtime_status_by_connection_id[connection_id] = status

    @staticmethod
    def _map_runtime_status(status: ConnectionStatus) -> str:
        """Convert enum runtime state to API response text."""
        status_map = {
            ConnectionStatus.CONNECTED: "connected",
            ConnectionStatus.CONNECTING: "connecting",
            ConnectionStatus.DISCONNECTED: "disconnected",
            ConnectionStatus.ERROR: "error",
        }
        return status_map.get(status, "disconnected")
    
    def set_agent_runner(self, agent_runner: "AgentRunner") -> None:
        """Set the agent runner for processing messages.
        
        Args:
            agent_runner: AgentRunner instance for processing messages
        """
        self._agent_runner = agent_runner
        # Capture the event loop for async operations from sync callbacks
        try:
            self._event_loop = asyncio.get_running_loop()
        except RuntimeError:
            self._event_loop = None

    def set_session_manager_router(self, session_manager_router: "SessionManagerRouter") -> None:
        """Set the per-user session manager router used by channel traffic."""
        self._session_manager_router = session_manager_router

    async def initialize_connection(
        self,
        user_id: str,
        channel_type: str,
        connection_id: str
    ) -> bool:
        """Initialize and start a channel connection.

        For long-connection channels, this will establish the persistent connection.
        For webhook channels, this will register the webhook handler.

        Initialization is idempotent for a given connection: concurrent calls
        share one attempt, and an already-running handler for the same
        connection is stopped first so a re-init cannot leave two SDK
        subprocesses serving the same account.

        Args:
            user_id: User identifier
            channel_type: Channel type
            connection_id: Connection identifier

        Returns:
            True if initialized successfully
        """
        instance_key = self._instance_key(user_id, channel_type, connection_id)

        in_flight = self._initializing.get(instance_key)
        if in_flight is not None and not in_flight.done():
            logger.info(f"Connection initialization already in progress: {instance_key}")
            return await self._await_initialization(in_flight)

        task = asyncio.ensure_future(
            self._initialize_connection_impl(user_id, channel_type, connection_id)
        )
        self._initializing[instance_key] = task
        task.add_done_callback(lambda _finished, key=instance_key: self._initializing.pop(key, None))
        return await self._await_initialization(task)

    @staticmethod
    async def _await_initialization(task: "asyncio.Task") -> bool:
        """Await a shared initialization task without cancelling it on abort."""
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Connection initialization task failed")
            return False

    def _register_active_handler(
        self,
        user_id: str,
        channel_type: str,
        connection_id: str,
        handler: ChannelHandler,
    ) -> str:
        """Record a live handler for a connection and return its instance key.

        Registers the handler together with its coordinates so every lookup
        (status, probe, shutdown) works without parsing the opaque key.
        """
        instance_key = self._instance_key(user_id, channel_type, connection_id)
        self._active_connections[instance_key] = handler
        self._active_connection_coordinates[instance_key] = (
            user_id,
            channel_type,
            connection_id,
        )
        return instance_key

    def _find_active_handler(self, connection_id: str) -> Optional[ChannelHandler]:
        """Return the active handler for a connection id, if any."""
        for key, coordinates in self._active_connection_coordinates.items():
            if coordinates[2] != connection_id:
                continue
            handler = self._active_connections.get(key)
            if handler is not None:
                return handler
        return None

    @staticmethod
    def _instance_key(user_id: str, channel_type: str, connection_id: str) -> str:
        """Return an unambiguous registry/instance key for a connection.

        Plain ":"-joining let ids that contain ":" collide (e.g. a connection
        id "b:c" with channel "a" vs channel "b" with id "c"), which would make
        one connection's handler serve another. A JSON array encoding keeps the
        fields distinct.
        """
        return json.dumps(
            [str(user_id or ""), str(channel_type or ""), str(connection_id or "")],
            ensure_ascii=False,
            separators=(",", ":"),
        )

    async def _stop_existing_handler(self, instance_key: str) -> None:
        """Stop and unregister any handler already serving a connection."""
        handler = self._active_connections.pop(instance_key, None)
        if handler is not None:
            try:
                if getattr(handler, "supports_long_connection", False):
                    await handler.disconnect()
            except Exception:
                logger.warning(f"Failed to disconnect previous handler: {instance_key}", exc_info=True)
            try:
                await handler.stop()
            except Exception:
                logger.warning(f"Failed to stop previous handler: {instance_key}", exc_info=True)
            logger.info(f"Stopped previous handler before re-initialization: {instance_key}")

        self._active_connection_coordinates.pop(instance_key, None)
        ChannelRegistry.remove_instance(instance_key)
        self._stop_intents.discard(instance_key)

    def _release_handler_instance(self, instance_key: str, *, reason: str) -> None:
        """Drop a partially started handler from the registry and active map.

        Every failure path after create_instance must release the registry
        entry: otherwise the channel type stays marked as instantiated and the
        half-configured handler keeps its SDK subprocess alive.
        """
        handler = self._active_connections.pop(instance_key, None)
        self._active_connection_coordinates.pop(instance_key, None)
        ChannelRegistry.remove_instance(instance_key)
        logger.info(f"Released handler instance after {reason}: {instance_key}")
        return handler

    async def _initialize_connection_impl(
        self,
        user_id: str,
        channel_type: str,
        connection_id: str
    ) -> bool:
        """Start a channel connection (single attempt; see initialize_connection)."""
        self._set_connection_runtime_status(connection_id, ConnectionStatus.CONNECTING)
        instance_key = self._instance_key(user_id, channel_type, connection_id)
        handler_created = False

        try:
            from app.atlasclaw.db import get_db_manager

            # Get connection config from database
            async with get_db_manager().get_session() as session:
                channel = await ChannelConfigService.get_by_id(session, connection_id)
                if not channel or channel.user_id != user_id or channel.type != channel_type:
                    logger.error(f"Connection not found: {user_id}/{channel_type}/{connection_id}")
                    self._set_connection_runtime_status(connection_id, ConnectionStatus.ERROR)
                    return False

                connection_config = ChannelConfigService.to_channel_config(channel)

            # Get handler class
            handler_class = ChannelRegistry.get(channel_type)
            if not handler_class:
                logger.error(f"Channel type not found: {channel_type}")
                self._set_connection_runtime_status(connection_id, ConnectionStatus.ERROR)
                return False

            # Create instance
            instance_key = self._instance_key(user_id, channel_type, connection_id)

            # A previous handler for this connection must be torn down before
            # its replacement starts, otherwise the old SDK subprocess keeps
            # running and messages are handled twice.
            await self._stop_existing_handler(instance_key)

            handler = ChannelRegistry.create_instance(
                instance_key,
                channel_type,
                connection_config["config"]
            )
            handler_created = handler is not None

            if not handler:
                logger.error(f"Failed to create handler instance: {instance_key}")
                self._set_connection_runtime_status(connection_id, ConnectionStatus.ERROR)
                return False

            # Setup handler
            if not await handler.setup(connection_config["config"]):
                logger.error(f"Handler setup failed: {instance_key}")
                self._release_handler_instance(instance_key, reason="setup failure")
                self._set_connection_runtime_status(connection_id, ConnectionStatus.ERROR)
                return False

            # Set message callback for long-connection mode
            if handler.supports_long_connection:
                handler.set_message_callback(
                    lambda msg: self._on_message_received(user_id, channel_type, connection_id, msg)
                )

            # Start handler (for long-connection, this establishes the connection)
            if not await handler.start(None):  # TODO: pass proper context
                logger.error(f"Handler start failed: {instance_key}")
                self._release_handler_instance(instance_key, reason="start failure")
                failure_status = handler.get_status()
                if failure_status == ConnectionStatus.DISCONNECTED:
                    failure_status = ConnectionStatus.ERROR
                self._set_connection_runtime_status(connection_id, failure_status)
                return False

            # For long-connection handlers, also call connect()
            if handler.supports_long_connection:
                if not await handler.connect():
                    logger.error(f"Long connection failed: {instance_key}")
                    await handler.stop()
                    self._release_handler_instance(instance_key, reason="connect failure")
                    failure_status = handler.get_status()
                    if failure_status == ConnectionStatus.DISCONNECTED:
                        failure_status = ConnectionStatus.ERROR
                    self._set_connection_runtime_status(connection_id, failure_status)
                    return False
                logger.info(f"Long connection established: {instance_key}")

            # Register as active connection
            if instance_key in self._stop_intents:
                # A stop arrived while this initialization was in flight: never
                # publish the fresh handler, tear it down instead.
                logger.info(f"Stop requested during initialization; discarding handler: {instance_key}")
                try:
                    if getattr(handler, "supports_long_connection", False):
                        await handler.disconnect()
                except Exception:
                    logger.warning(f"Failed to disconnect handler after late stop: {instance_key}", exc_info=True)
                try:
                    await handler.stop()
                except Exception:
                    logger.warning(f"Failed to stop handler after late stop: {instance_key}", exc_info=True)
                self._release_handler_instance(instance_key, reason="stop requested during initialization")
                self._set_connection_runtime_status(connection_id, ConnectionStatus.DISCONNECTED)
                return False

            self._register_active_handler(user_id, channel_type, connection_id, handler)
            ChannelRegistry.register_connection(ChannelConnection(
                id=channel.id,
                name=channel.name,
                channel_type=channel.type,
                config=channel.config or {},
                enabled=channel.is_active,
                is_default=channel.is_default,
            ))
            self._set_connection_runtime_status(connection_id, handler.get_status())

            logger.info(f"Channel connection initialized: {instance_key}")
            return True

        except Exception as e:
            logger.error(f"Failed to initialize connection: {e}")
            if handler_created:
                self._release_handler_instance(instance_key, reason="initialization exception")
            self._set_connection_runtime_status(connection_id, ConnectionStatus.ERROR)
            return False

    def _on_message_received(
        self,
        user_id: str,
        channel_type: str,
        connection_id: str,
        message: InboundMessage
    ) -> None:
        """Handle incoming message from long connection.

        Args:
            user_id: User identifier
            channel_type: Channel type
            connection_id: Connection identifier
            message: Received message
        """
        logger.info(f"[ChannelManager] Message received from {channel_type}/{connection_id}: {message.content[:50]}...")
        
        # Schedule async processing on the event loop
        if self._event_loop and self._agent_runner:
            asyncio.run_coroutine_threadsafe(
                self._process_message_async(user_id, channel_type, connection_id, message),
                self._event_loop
            )
        else:
            logger.warning("[ChannelManager] No event loop or agent runner available for message processing")
    
    async def _process_message_async(
        self,
        user_id: str,
        channel_type: str,
        connection_id: str,
        message: InboundMessage
    ) -> None:
        """Async message processing - routes to agent and sends reply.
        
        Args:
            user_id: User identifier
            channel_type: Channel type
            connection_id: Connection identifier
            message: Received message
        """
        try:
            logger.info(f"[ChannelManager] Processing message: {message.content[:50]}...")
            
            # Get handler for sending reply
            instance_key = self._instance_key(user_id, channel_type, connection_id)
            handler = self._active_connections.get(instance_key)
            
            if not handler:
                logger.error(f"[ChannelManager] No handler found for {instance_key}")
                return

            await self._acknowledge_message_received(
                handler,
                channel_type=channel_type,
                connection_id=connection_id,
                message=message,
            )
            
            from app.atlasclaw.api.deps_context import build_scoped_deps, get_api_context

            api_context = get_api_context()
            user_info = UserInfo(user_id=user_id, display_name=user_id.capitalize())
            session_key = self._build_channel_session_key(
                owner_user_id=user_id,
                channel_type=channel_type,
                connection_id=connection_id,
                message=message,
            )
            deps = build_scoped_deps(
                api_context,
                user_info,
                session_key,
                extra={
                    "channel_connection_id": connection_id,
                    "external_sender_id": message.sender_id,
                    "external_chat_id": message.chat_id,
                    "external_chat_type": self._resolve_chat_type(message).value,
                },
            )
            deps.peer_id = self._resolve_peer_id(message)
            deps.channel = channel_type
            # Collect response from agent
            response_text = ""
            event_count = 0
            logger.debug(f"[ChannelManager] Starting to collect events for message: {message.content[:30]}...")
            # Serialize turns per session so channel traffic cannot interleave
            # transcript writes with an HTTP run for the same session.
            await api_context.session_queue.acquire(session_key)
            try:
                async for event in self._agent_runner.run(
                    session_key=session_key,
                    user_message=message.content,
                    deps=deps,
                    max_tool_calls=10,
                    timeout_seconds=120,
                ):
                    event_count += 1
                    logger.debug(f"[ChannelManager] Event {event_count}: type={event.type}")
                    # Collect text deltas
                    if event.type == "assistant":
                        response_text += event.content or ""
                    elif event.type == "error":
                        # The raw error may carry provider/internal detail;
                        # log it server-side and send the group a stable text.
                        logger.error(f"[ChannelManager] Agent error: {event.error}")
                        response_text = self.USER_FACING_ERROR_TEXT
                        break
            finally:
                api_context.session_queue.release(session_key)
            
            logger.info(f"[ChannelManager] Processed {event_count} events, response length: {len(response_text)}")
            
            # Send reply back to channel
            if response_text:
                outbound = OutboundMessage(
                    chat_id=message.chat_id,
                    content=response_text,
                    content_type="text",
                    reply_to=message.message_id,
                    metadata=message.metadata,  # Pass metadata for session_webhook etc.
                )
                logger.debug(f"[ChannelManager] Sending reply to chat_id={message.chat_id}...")
                result = await handler.send_message(outbound)
                if result.success:
                    logger.info(f"[ChannelManager] Reply sent successfully to {channel_type}/{connection_id}")
                else:
                    logger.error(f"[ChannelManager] Failed to send reply: {result.error}")
            else:
                logger.warning("[ChannelManager] No response generated from agent")
                
        except Exception as e:
            logger.error(f"[ChannelManager] Error processing message: {e}", exc_info=True)

    async def _acknowledge_message_received(
        self,
        handler: ChannelHandler,
        *,
        channel_type: str,
        connection_id: str,
        message: InboundMessage,
    ) -> None:
        """Best-effort native acknowledgement before Agent processing starts."""
        try:
            result = await asyncio.wait_for(
                handler.acknowledge_message(message),
                timeout=1.5,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "[ChannelManager] Message acknowledgement timed out for %s/%s: %s",
                channel_type,
                connection_id,
                message.message_id,
            )
            return
        except Exception as e:
            logger.warning(
                "[ChannelManager] Message acknowledgement failed for %s/%s: %s",
                channel_type,
                connection_id,
                e,
                exc_info=True,
            )
            return

        if not result.supported:
            logger.debug(
                "[ChannelManager] Native acknowledgement unsupported for %s/%s: %s",
                channel_type,
                connection_id,
                message.message_id,
            )
            return
        if result.success:
            logger.debug(
                "[ChannelManager] Native acknowledgement sent for %s/%s: %s",
                channel_type,
                connection_id,
                message.message_id,
            )
            return
        logger.warning(
            "[ChannelManager] Native acknowledgement returned failure for %s/%s: %s",
            channel_type,
            connection_id,
            result.error or "unknown error",
        )

    def _resolve_chat_type(self, message: InboundMessage) -> ChatType:
        """Map provider-specific metadata to canonical chat types."""
        metadata = message.metadata or {}
        raw_type = (
            metadata.get("chat_type")
            or metadata.get("conversation_type")
            or metadata.get("conversationType")
            or ""
        )
        raw_type = str(raw_type).strip().lower()
        if raw_type in {"group", "groupchat", "chat", "2"}:
            return ChatType.GROUP
        if raw_type in {"channel"}:
            return ChatType.CHANNEL
        if raw_type in {"thread"}:
            return ChatType.THREAD
        if raw_type in {"p2p", "single", "im", "1", "private"}:
            return ChatType.DM
        if message.chat_id and message.chat_id != message.sender_id:
            return ChatType.GROUP
        return ChatType.DM

    def _resolve_peer_id(self, message: InboundMessage) -> str:
        """Return the peer identity that should own the conversation state."""
        chat_type = self._resolve_chat_type(message)
        if chat_type in {ChatType.GROUP, ChatType.CHANNEL, ChatType.THREAD}:
            return message.chat_id or message.sender_id or "default"
        return message.sender_id or message.chat_id or "default"

    def _build_channel_session_key(
        self,
        *,
        owner_user_id: str,
        channel_type: str,
        connection_id: str,
        message: InboundMessage,
    ) -> str:
        """Build canonical session keys for inbound channel traffic."""
        key = SessionKey(
            agent_id="main",
            user_id=owner_user_id,
            channel=channel_type,
            account_id=connection_id,
            chat_type=self._resolve_chat_type(message),
            peer_id=self._resolve_peer_id(message),
        )
        return key.to_string(scope=SessionScope.PER_ACCOUNT_CHANNEL_PEER)

    async def stop_all(self) -> None:
        """Stop every active channel connection.

        Used at application shutdown so SDK subprocesses and WebSockets do not
        outlive the process. Connection coordinates are read from the recorded
        map instead of parsing the instance key, which is opaque by design.
        """
        for instance_key, coordinates in list(self._active_connection_coordinates.items()):
            if instance_key not in self._active_connections:
                continue
            user_id, channel_type, connection_id = coordinates
            try:
                await self.stop_connection(user_id, channel_type, connection_id)
            except Exception:
                logger.warning("Failed to stop channel connection: %s", instance_key, exc_info=True)
        # Anything registered without coordinates is torn down by key so
        # shutdown never leaves a live handler behind.
        for instance_key in list(self._active_connections.keys()):
            handler = self._active_connections.pop(instance_key, None)
            if handler is None:
                continue
            self._stop_intents.add(instance_key)
            try:
                if getattr(handler, "supports_long_connection", False):
                    await handler.disconnect()
            except Exception:
                logger.warning("Failed to disconnect channel handler: %s", instance_key, exc_info=True)
            try:
                await handler.stop()
            except Exception:
                logger.warning("Failed to stop channel handler: %s", instance_key, exc_info=True)
            ChannelRegistry.remove_instance(instance_key)
            self._active_connection_coordinates.pop(instance_key, None)

    async def stop_connection(
        self,
        user_id: str,
        channel_type: str,
        connection_id: str
    ) -> bool:
        """Stop a channel connection.

        For long-connection channels, this will gracefully close the connection.

        Args:
            user_id: User identifier
            channel_type: Channel type
            connection_id: Connection identifier

        Returns:
            True if stopped successfully
        """
        instance_key = self._instance_key(user_id, channel_type, connection_id)
        # Record the intent first: an initialization still in flight must see
        # it and tear its own handler down when it finishes, instead of
        # reporting "stopped" while a fresh handler keeps running.
        self._stop_intents.add(instance_key)
        try:
            handler = self._active_connections.get(instance_key)

            if not handler:
                logger.warning(f"Connection not active: {instance_key}")
                self._set_connection_runtime_status(connection_id, ConnectionStatus.DISCONNECTED)
                return False

            # For long-connection handlers, disconnect first
            if handler.supports_long_connection:
                await handler.disconnect()
                logger.info(f"Long connection disconnected: {instance_key}")

            await handler.stop()
            self._active_connections.pop(instance_key, None)
            self._active_connection_coordinates.pop(instance_key, None)
            ChannelRegistry.remove_instance(instance_key)
            self._set_connection_runtime_status(connection_id, ConnectionStatus.DISCONNECTED)
            self._stop_intents.discard(instance_key)

            logger.info(f"Channel connection stopped: {instance_key}")
            return True

        except Exception as e:
            logger.error(f"Failed to stop connection: {e}")
            # Keep the intent: the in-flight initialization will clean up.
            self._set_connection_runtime_status(connection_id, ConnectionStatus.ERROR)
            return False

    def find_active_connection(
        self,
        channel_type: str,
        connection_id: str,
    ) -> Optional[tuple]:
        """Locate the active handler for a connection.

        Args:
            channel_type: Channel type
            connection_id: Connection identifier

        Returns:
            Tuple of (owner_user_id, handler) or None when the connection
            has no active handler.
        """
        # Coordinates are matched exactly: suffix matching on the opaque key
        # could match a different connection whose ids happen to line up.
        for key, coordinates in self._active_connection_coordinates.items():
            if coordinates[1] != channel_type or coordinates[2] != connection_id:
                continue
            handler = self._active_connections.get(key)
            if handler is not None:
                return coordinates[0], handler
        return None

    def schedule_inbound_processing(
        self,
        user_id: str,
        channel_type: str,
        connection_id: str,
        message: InboundMessage
    ) -> "asyncio.Task":
        """Schedule Agent processing for a parsed inbound channel message.

        The task is kept referenced so it cannot be garbage collected while
        running.

        Args:
            user_id: Owner of the channel connection
            channel_type: Channel type
            connection_id: Connection identifier
            message: Parsed inbound message

        Returns:
            The scheduled processing task.
        """
        task = asyncio.create_task(
            self._process_message_async(user_id, channel_type, connection_id, message)
        )
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return task

    async def route_inbound_message(
        self,
        channel_type: str,
        connection_id: str,
        request: Any
    ) -> Optional[InboundMessage]:
        """Parse an inbound webhook request and schedule Agent processing.

        Args:
            channel_type: Channel type
            connection_id: Connection identifier
            request: Raw platform payload (dict or JSON string)

        Returns:
            Standardized InboundMessage, or None when no active handler
            exists or the payload cannot be parsed.
        """
        found = self.find_active_connection(channel_type, connection_id)
        if not found:
            logger.error(f"No active handler for connection: {channel_type}/{connection_id}")
            return None
        user_id, handler = found

        try:
            inbound = await handler.handle_inbound(request)
        except Exception as e:
            logger.error(f"Failed to parse inbound message: {channel_type}/{connection_id}: {e}")
            return None
        if not inbound:
            return None

        self.schedule_inbound_processing(user_id, channel_type, connection_id, inbound)
        return inbound

    def get_user_connections(
        self,
        user_id: str,
        channel_type: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Get all connections for a user.

        Note: This is a sync wrapper for backwards compatibility.
        For async usage, use get_user_connections_async instead.

        Args:
            user_id: User identifier
            channel_type: Optional channel type filter

        Returns:
            List of connection info
        """
        # Return cached active connections for sync access
        # For full data, use get_user_connections_async
        result = []
        for key in self._active_connections:
            coordinates = self._active_connection_coordinates.get(key)
            if coordinates is None:
                continue
            conn_user_id, conn_type, conn_id = coordinates
            if conn_user_id == user_id:
                if channel_type is None or conn_type == channel_type:
                    result.append({
                        "id": conn_id,
                        "channel_type": conn_type,
                        "enabled": True,  # Active connections are enabled
                    })
        return result

    async def get_user_connections_async(
        self,
        user_id: str,
        channel_type: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Get all connections for a user from database.

        Args:
            user_id: User identifier
            channel_type: Optional channel type filter

        Returns:
            List of connection info
        """
        from app.atlasclaw.db import get_db_manager

        result = []
        async with get_db_manager().get_session() as session:
            if channel_type:
                channels = await ChannelConfigService.list_by_user_and_type(
                    session, user_id, channel_type
                )
            else:
                channels = await ChannelConfigService.list_by_user(session, user_id)

            for channel in channels:
                result.append(ChannelConfigService.to_channel_config(channel))

        return result

    async def enable_connection(
        self,
        user_id: str,
        channel_type: str,
        connection_id: str
    ) -> bool:
        """Enable a connection.

        Args:
            user_id: User identifier
            channel_type: Channel type
            connection_id: Connection identifier

        Returns:
            True if enabled successfully (DB status updated, initialization started in background)
        """
        from app.atlasclaw.db import get_db_manager

        # Step 1: Update DB status (synchronous, fast)
        async with get_db_manager().get_session() as session:
            channel = await ChannelConfigService.update_status(session, connection_id, True)
            if not channel:
                return False

        # Step 2: Initialize connection in background (async, don't block API response)
        self._set_connection_runtime_status(connection_id, ConnectionStatus.CONNECTING)
        self.schedule_background_initialize(user_id, channel_type, connection_id)
        return True

    def schedule_background_initialize(
        self,
        user_id: str,
        channel_type: str,
        connection_id: str,
    ) -> "asyncio.Task":
        """Schedule background connection initialization.

        Call this only after the connection row is committed, so the
        initialization cannot read a stale row. The task is kept referenced so
        it cannot be garbage collected mid-flight.

        Args:
            user_id: User identifier
            channel_type: Channel type
            connection_id: Connection identifier

        Returns:
            The scheduled initialization task.
        """
        task = asyncio.create_task(
            self._background_initialize(user_id, channel_type, connection_id)
        )
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return task

    async def _background_initialize(
        self,
        user_id: str,
        channel_type: str,
        connection_id: str
    ) -> None:
        """Initialize connection in background. Status is tracked via handler._status.

        Args:
            user_id: User identifier
            channel_type: Channel type
            connection_id: Connection identifier
        """
        retry_delays_seconds = (0.0, 2.0, 5.0)
        total_attempts = len(retry_delays_seconds)
        for attempt, delay_seconds in enumerate(
            retry_delays_seconds,
            start=1,
        ):
            if delay_seconds > 0:
                await asyncio.sleep(delay_seconds)

            try:
                result = await self.initialize_connection(user_id, channel_type, connection_id)
            except Exception as e:
                logger.error(
                    f"Background connection initialization error: "
                    f"{channel_type}/{connection_id}: {e}"
                )
                result = False

            if result:
                return

            if attempt < total_attempts:
                next_delay = retry_delays_seconds[attempt]
                self._set_connection_runtime_status(connection_id, ConnectionStatus.CONNECTING)
                logger.warning(
                    "Background connection initialization failed for %s/%s "
                    "(attempt %s/%s), retrying in %.1fs",
                    channel_type,
                    connection_id,
                    attempt,
                    total_attempts,
                    next_delay,
                )
                continue

            logger.warning(
                "Background connection initialization failed for %s/%s after %s attempts",
                channel_type,
                connection_id,
                total_attempts,
            )

    async def disable_connection(
        self,
        user_id: str,
        channel_type: str,
        connection_id: str
    ) -> bool:
        """Disable a connection.

        Args:
            user_id: User identifier
            channel_type: Channel type
            connection_id: Connection identifier

        Returns:
            True if disabled successfully
        """
        from app.atlasclaw.db import get_db_manager

        await self.stop_connection(user_id, channel_type, connection_id)

        async with get_db_manager().get_session() as session:
            channel = await ChannelConfigService.update_status(session, connection_id, False)
            if channel is not None:
                self._set_connection_runtime_status(connection_id, ConnectionStatus.DISCONNECTED)
            return channel is not None

    def get_connection_runtime_status(
        self,
        connection_id: str
    ) -> str:
        """Get runtime connection status for a specific connection.
        
        The instance key is opaque, so the handler is located through the
        recorded connection coordinates instead of string suffix matching
        (which could match a different connection's key).
        
        Args:
            connection_id: Connection identifier (UUID)
        
        Returns:
            Runtime status string: "connected", "disconnected", "connecting", or "error"
        """
        handler = self._find_active_handler(connection_id)

        if not handler:
            cached_status = self._runtime_status_by_connection_id.get(
                connection_id,
                ConnectionStatus.DISCONNECTED,
            )
            return self._map_runtime_status(cached_status)

        try:
            status = handler.get_status()
            self._set_connection_runtime_status(connection_id, status)
            return self._map_runtime_status(status)
        except Exception:
            cached_status = self._runtime_status_by_connection_id.get(
                connection_id,
                ConnectionStatus.DISCONNECTED,
            )
            return self._map_runtime_status(cached_status)

    def list_active_connection_descriptors(self) -> list[dict[str, Any]]:
        """Return lightweight descriptors for all active connections."""
        items: list[dict[str, Any]] = []
        for key, handler in sorted(self._active_connections.items()):
            coordinates = self._active_connection_coordinates.get(key)
            if coordinates is None:
                continue
            user_id, channel_type, connection_id = coordinates
            items.append(
                {
                    "user_id": user_id,
                    "channel_type": channel_type,
                    "connection_id": connection_id,
                    "status": self.get_connection_runtime_status(connection_id),
                    "supports_long_connection": bool(getattr(handler, "supports_long_connection", False)),
                }
            )
        return items

    async def probe_connection(
        self,
        user_id: str,
        channel_type: str,
        connection_id: str,
    ) -> dict[str, Any]:
        """Run a narrow health probe for an active connection."""
        instance_key = self._instance_key(user_id, channel_type, connection_id)
        handler = self._active_connections.get(instance_key)
        if handler is None:
            return {"healthy": False, "status": "disconnected", "reconnected": False}

        healthy = await handler.health_check()
        return {
            "healthy": healthy,
            "status": self.get_connection_runtime_status(connection_id),
            "reconnected": False,
        }

    async def reconnect_connection(
        self,
        user_id: str,
        channel_type: str,
        connection_id: str,
    ) -> bool:
        """Attempt a best-effort reconnect for an active long connection."""
        instance_key = self._instance_key(user_id, channel_type, connection_id)
        handler = self._active_connections.get(instance_key)
        if handler is None:
            return False
        if not getattr(handler, "supports_long_connection", False):
            return False
        try:
            return bool(await handler.reconnect())
        except Exception:
            logger.exception("Failed to reconnect channel connection: %s", instance_key)
            return False
