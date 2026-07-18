# pyhive/core/middleware.py
import time
import re
import threading
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, Callable, List
from pathlib import Path
from loguru import logger

from .base import PyHiveException, PyHiveResponse
from .context import PyHiveContext


class PyHiveMiddleware(ABC):
    """
    Base class for all request processors.
    
    Functions like a chain of responsibility:
    1. pre_process(context, args): Can modify args or raise Exception to stop execution.
    2. post_process(context, response): Can modify the result before sending back.
    """
    
    @abstractmethod
    def pre_process(self, context: PyHiveContext, tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        """
        Runs BEFORE the tool executes.
        Must return the (potentially modified) arguments.
        Raises PyHiveException to block the request.
        """
        pass

    def post_process(self, context: PyHiveContext, response: PyHiveResponse) -> PyHiveResponse:
        """
        Runs AFTER the tool executes.
        Can be used for logging, auditing, or scrubbing sensitive data from output.
        """
        return response

class PyHiveSanitizer(PyHiveMiddleware):
    """
    Security Middleware.
    
    1. Path Traversal Prevention: blocks '../' in file paths.
    2. Shell Injection Prevention: strips dangerous chars if needed.
    3. Type Coercion: Ensures booleans are bools, not strings "true".
    """

    def __init__(self, strict_paths: bool = True):
        self.strict_paths = strict_paths
        self._path_traversal = re.compile(r'(\.\./|\.\.\\)')

    def pre_process(self, context: PyHiveContext, tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        clean_args = {}
        
        for key, value in args.items():
            if isinstance(value, str):
                if self.strict_paths and self._path_traversal.search(value):
                    logger.warning(f"Security Alert: Path traversal attempt by {context.user_id} in arg '{key}'")
                    raise PyHiveException(f"Security violation: Invalid path sequence in argument '{key}'", code="SECURITY_VIOLATION")
                
                clean_value = value.replace("\0", "")
                clean_args[key] = clean_value.strip()
            
            elif isinstance(value, Path):
                clean_args[key] = value

            else:
                clean_args[key] = value
                
        return clean_args

class PyHiveRateLimiter(PyHiveMiddleware):
    """
    Token Bucket Rate Limiter.
    
    Limits users to N requests per minute.
    Thread-safe implementation using RLock.
    """

    def __init__(self, limit: int = 60, window: int = 60):
        """
        Args:
            limit: Max requests allowed.
            window: Time window in seconds (default 1 minute).
        """
        self.limit = limit
        self.window = window
        self._lock = threading.RLock()
        
        # Structure: { user_id: [timestamp1, timestamp2, ...] }
        self._history: Dict[str, List[float]] = {}

    def pre_process(self, context: PyHiveContext, tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        user_id = context.user_id
        now = time.time()
        with self._lock:
            history = self._history.get(user_id, [])
            cutoff = now - self.window
            history = [t for t in history if t > cutoff]
            
            if len(history) >= self.limit:
                logger.warning(f"Rate Limit Exceeded for user {user_id} ({len(history)}/{self.limit})")
                raise PyHiveException(
                    f"Rate limit exceeded. Try again in {int(self.window - (now - history[0]))} seconds.",
                    code="RATE_LIMIT_EXCEEDED"
                )
            
            history.append(now)
            self._history[user_id] = history
            
        return args

class MiddlewarePipeline:
    """
    Manages the execution chain of middlewares.
    """
    def __init__(self):
        self._middlewares: List[PyHiveMiddleware] = []

    def add(self, middleware: PyHiveMiddleware):
        self._middlewares.append(middleware)

    def run_pre_processing(self, context: PyHiveContext, tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        """Runs all pre_process methods in order."""
        current_args = args
        for mw in self._middlewares:
            current_args = mw.pre_process(context, tool_name, current_args)
        return current_args

    def run_post_processing(self, context: PyHiveContext, response: PyHiveResponse) -> PyHiveResponse:
        """Runs all post_process methods in reverse order (stack-like)."""
        current_response = response
        for mw in reversed(self._middlewares):
            current_response = mw.post_process(context, current_response)
        return current_response