# pyhive/core/broker_client.py
import time
import json
import threading
from typing import Any, Dict, List, Optional, Callable
from loguru import logger


class PyHiveEmitter:
    """
    The 'Voice' of a running tool.
    
    Developers use this to stream updates back to the UI/LLM.
    It is injected automatically into tools if requested.
    
    Usage:
        def my_tool(emitter: PyHiveEmitter):
            emitter.send_progress(0.1, "Starting...")
    """

    def __init__(self, transport_callback: Callable[[Dict], None]):
        """
        Args:
            job_id: The unique ID of the task being executed.
            transport_callback: The function that actually sends data (e.g., to Redis/Socket).
        """
        self._transport = transport_callback

    def send(self, data : Dict, ids : str) :
        """Sends JSON (raw python Dict)"""
        self._safe_emit(ids, payload=data)


    def _safe_emit(self, ids : str, payload: Dict):
        """Internal fail-safe wrapper."""
        try:
            self._transport(ids=ids, payload=payload)
        except Exception as e:
            logger.warning(f"Failed to emit message for job {self.job_id}: {e}")

class PyHiveRoom:
    """
    Manages a secure communication channel for a specific Job ID.
    
    In a standalone app, this holds local references to WebSocket clients.
    In a distributed app, this interfaces with Redis/ZeroMQ channels.
    """

    def __init__(self, job_id: str):
        self.job_id = job_id
        self.created_at = time.time()
        self._subscribers: List[Callable[[Dict], None]] = []
        self._lock = threading.RLock()
        self._history: List[Dict] = [] # Optional: Store last N messages for reconnection

    def join(self, callback: Callable[[Dict], None]):
        """A client (Frontend/CLI) subscribes to this room."""
        with self._lock:
            self._subscribers.append(callback)
            logger.debug(f"Client joined room {self.job_id}. Total: {len(self._subscribers)}")

    def leave(self, callback: Callable[[Dict], None]):
        """A client disconnects."""
        with self._lock:
            if callback in self._subscribers:
                self._subscribers.remove(callback)
                logger.debug(f"Client left room {self.job_id}.")

    def broadcast(self, message: Dict):
        """
        Sends a message to all active subscribers.
        This is the method passed to PyHiveEmitter as 'transport_callback'.
        """
        with self._lock:
            if len(self._history) > 50: 
                self._history.pop(0)
            self._history.append(message)
            
            dead_listeners = []
            for callback in self._subscribers:
                try:
                    callback(message)
                except Exception as e:
                    logger.error(f"Error broadcasting to listener in {self.job_id}: {e}")
                    dead_listeners.append(callback)
            
            for d in dead_listeners:
                self._subscribers.remove(d)

    def close(self):
        """Destroys the room and disconnects everyone."""
        with self._lock:
            self._subscribers.clear()
            self._history.clear()
            logger.debug(f"Room {self.job_id} closed.")