"""Core event system for Agent X1.

Provides event types and event bus for loose coupling between modules.
"""

from enum import Enum, auto
from typing import Any, Dict, List, Callable, Optional
from dataclasses import dataclass
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class AgentEvent(Enum):
    """Core event types for the agent system."""
    
    # Session lifecycle
    SESSION_CREATED = auto()
    SESSION_RESUMED = auto()
    SESSION_PAUSED = auto()
    SESSION_COMPLETED = auto()
    SESSION_FAILED = auto()
    SESSION_ARCHIVED = auto()
    SESSION_FORKED = auto()
    
    # Token budget events
    TOKEN_BUDGET_WARNING = auto()
    SESSION_COMPACTING = auto()
    
    # Sub-agent events (reserved for future multi-agent support)
    SUBAGENT_SESSION_CREATED = auto()
    SUBAGENT_SESSION_COMPLETED = auto()
    
    # Turn events
    TURN_STARTED = auto()
    TURN_COMPLETED = auto()
    TURN_FAILED = auto()
    
    # LLM events
    LLM_CALL_STARTED = auto()
    LLM_CALL_COMPLETED = auto()
    LLM_CALL_FAILED = auto()
    
    # Tool events
    TOOL_CALLED = auto()
    TOOL_SUCCEEDED = auto()
    TOOL_FAILED = auto()
    
    # Context events
    CONTEXT_ASSEMBLED = auto()
    CONTEXT_COMPRESSED = auto()
    COMPRESSION_PIPELINE_COMPLETED = auto()
    
    # Memory events
    MEMORY_STORED = auto()
    MEMORY_RETRIEVED = auto()
    MEMORY_EXPIRED = auto()
    
    # Runtime events
    LOOP_STARTED = auto()
    LOOP_ITERATION = auto()
    LOOP_DETECTED = auto()
    LOOP_COMPLETED = auto()
    
    # State change
    STATE_CHANGED = auto()


@dataclass
class EventPayload:
    """Payload for events."""
    event_type: AgentEvent
    timestamp: datetime
    session_id: Optional[str] = None
    turn_number: Optional[int] = None
    data: Dict[str, Any] = None
    
    def __post_init__(self):
        if self.data is None:
            self.data = {}


# Event handler type
EventHandler = Callable[[EventPayload], None]


class EventBus:
    """
    Simple event bus for decoupled communication.
    
    Allows modules to subscribe to events and be notified
    when they occur.
    """
    
    def __init__(self):
        self._handlers: Dict[AgentEvent, List[EventHandler]] = {}
        self._global_handlers: List[EventHandler] = []
    
    def subscribe(
        self,
        event_type: AgentEvent,
        handler: EventHandler
    ) -> None:
        """Subscribe to a specific event type."""
        if event_type not in self._handlers:
            self._handlers[event_type] = []
        self._handlers[event_type].append(handler)
        logger.debug(
            "[EventBus] Subscribe | event=%s | handler=%s",
            event_type.name, getattr(handler, '__name__', str(handler))
        )
    
    def subscribe_all(self, handler: EventHandler) -> None:
        """Subscribe to all events."""
        self._global_handlers.append(handler)
        logger.debug("Handler subscribed to all events")
    
    def unsubscribe(
        self,
        event_type: AgentEvent,
        handler: EventHandler
    ) -> bool:
        """Unsubscribe a handler from an event type."""
        if event_type in self._handlers:
            if handler in self._handlers[event_type]:
                self._handlers[event_type].remove(handler)
                return True
        return False
    
    def unsubscribe_all(self, handler: EventHandler) -> bool:
        """Unsubscribe a handler from all events."""
        removed = False
        if handler in self._global_handlers:
            self._global_handlers.remove(handler)
            removed = True
        
        for handlers in self._handlers.values():
            if handler in handlers:
                handlers.remove(handler)
                removed = True
        
        return removed
    
    def emit(
        self,
        event_type: AgentEvent,
        session_id: Optional[str] = None,
        turn_number: Optional[int] = None,
        **kwargs
    ) -> None:
        """Emit an event to all subscribers."""
        payload = EventPayload(
            event_type=event_type,
            timestamp=datetime.now(),
            session_id=session_id,
            turn_number=turn_number,
            data=kwargs
        )
        
        # DEBUG: Emit
        subscriber_count = len(self._handlers.get(event_type, [])) + len(self._global_handlers)
        logger.debug(
            "[EventBus] Emit | event=%s | subscriber_count=%d | payload_keys=%s",
            event_type.name, subscriber_count, list(kwargs.keys())
        )
        
        # Notify specific handlers
        handlers = self._handlers.get(event_type, [])
        for handler in handlers:
            try:
                handler(payload)
            except Exception as e:
                logger.error(
                    "[EventBus] Handler error | event=%s | handler=%s | error=%s",
                    event_type.name, getattr(handler, '__name__', str(handler)), str(e)
                )
        
        # Notify global handlers
        for handler in self._global_handlers:
            try:
                handler(payload)
            except Exception as e:
                logger.error(f"Global event handler error for {event_type.name}: {e}")
    
    def emit_sync(self, payload: EventPayload) -> None:
        """Emit a pre-constructed payload."""
        handlers = self._handlers.get(payload.event_type, [])
        for handler in handlers:
            try:
                handler(payload)
            except Exception as e:
                logger.error(f"Event handler error: {e}")
        
        for handler in self._global_handlers:
            try:
                handler(payload)
            except Exception as e:
                logger.error(f"Global event handler error: {e}")


# Global event bus instance
_event_bus: Optional[EventBus] = None


def get_event_bus() -> EventBus:
    """Get the global event bus."""
    global _event_bus
    if _event_bus is None:
        _event_bus = EventBus()
    return _event_bus


def reset_event_bus():
    """Reset the global event bus (for testing)."""
    global _event_bus
    _event_bus = None
