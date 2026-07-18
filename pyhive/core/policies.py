# pyhive/core/policies.py
import ast
import inspect
import signal
import threading
import time
from typing import List, Dict, Set, Optional, Any, Callable
from enum import Enum
from pydantic import BaseModel, Field
from loguru import logger

from .base import PyHiveException

class PyHiveScope:
    """
    Represents a specific permission string.
    Format: 'domain:action:resource' (e.g., 'filesystem:read:/tmp')
    
    Supports Wildcards:
    - 'filesystem:*' matches 'filesystem:read' and 'filesystem:write'
    - '*' matches everything (Super Admin)
    """
    
    def __init__(self, scope_str: str):
        self.raw = scope_str.lower().strip()
        self.parts = self.raw.split(':')

    def matches(self, required_scope: 'PyHiveScope') -> bool:
        """
        Checks if this scope grants the 'required_scope'.
        Example: 'filesystem:*' grants 'filesystem:read'.
        """
        if self.raw == '*': 
            return True
            
        req_parts = required_scope.parts
        
        # If I have fewer specific parts than required (and not wildcard), I can't grant it
        # e.g. "filesystem" vs "filesystem:read" -> False
        # But "filesystem:*" vs "filesystem:read" -> True
        
        for i, part in enumerate(self.parts):
            if i >= len(req_parts):
                return True
            
            req_part = req_parts[i]
            
            if part == '*':
                return True
            if part != req_part:
                return False
                
        return len(self.parts) >= len(req_parts)

    def __str__(self):
        return self.raw

    def __repr__(self):
        return f"<Scope: {self.raw}>"


class PyHivePolicy(BaseModel):
    """
    A collection of scopes defining what a Role can do.
    """
    name: str
    description: str = ""
    allowed_scopes: List[str] = Field(default_factory=list)
    denied_scopes: List[str] = Field(default_factory=list) # Explicit Deny overrides Allow

    def check_access(self, required_scope_str: str) -> bool:
        req = PyHiveScope(required_scope_str)
        
        for deny in self.denied_scopes:
            if PyHiveScope(deny).matches(req):
                return False

        for allow in self.allowed_scopes:
            if PyHiveScope(allow).matches(req):
                return True
                
        return False

class PyHiveRBAC:
    """
    Central Authority for User Permissions.
    Maps User IDs to Roles, and Roles to Policies.
    """
    
    def __init__(self):
        self._roles: Dict[str, PyHivePolicy] = {}
        self._user_roles: Dict[str, List[str]] = {} # User -> [Role Names]
        self._lock = threading.RLock()
        
        self.create_role("admin", ["*"], description="Super User")
        self.create_role("guest", ["read:public", "tool:execute:safe"], description="Anonymous User")

    def create_role(self, name: str, scopes: List[str], description: str = ""):
        with self._lock:
            self._roles[name] = PyHivePolicy(
                name=name, 
                description=description, 
                allowed_scopes=scopes
            )

    def assign_role(self, user_id: str, role_name: str):
        with self._lock:
            if role_name not in self._roles:
                raise ValueError(f"Role '{role_name}' does not exist.")
            
            if user_id not in self._user_roles:
                self._user_roles[user_id] = []
            
            if role_name not in self._user_roles[user_id]:
                self._user_roles[user_id].append(role_name)

    def verify(self, user_id: str, required_scope: str) -> bool:
        """
        Does this user have permission?
        """
        with self._lock:
            user_roles = self._user_roles.get(user_id, ["guest"])
            
            for role_name in user_roles:
                policy = self._roles.get(role_name)
                if policy and policy.check_access(required_scope):
                    return True
            
            logger.warning(f"Access Denied: User {user_id} lacks {required_scope}")
            return False

class PyHiveSandbox:
    """
    Pre-flight safety check for Tool Code.
    
    Uses Python AST to inspect code for 'Dangerous Globals' before execution.
    This prevents plugins from importing 'os' or 'subprocess' if strictly forbidden.
    """
    
    BANNED_IMPORTS = {'os', 'sys', 'subprocess', 'shutil', 'socket', 'pickle'}
    BANNED_BUILTINS = {'exec', 'eval', 'compile', 'open'}

    def __init__(self, strict_mode: bool = True):
        self.strict_mode = strict_mode

    def inspect_tool(self, func: Callable) -> bool:
        """
        Statically analyzes the function's source code.
        Returns True if safe, raises SecurityError if unsafe.
        """
        if not self.strict_mode:
            return True

        try:
            source = inspect.getsource(func)
            tree = ast.parse(source)
        except OSError:
            logger.warning(f"Cannot inspect source for {func.__name__}. Assuming unsafe.")
            return False 

        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    root_module = alias.name.split('.')[0]
                    if root_module in self.BANNED_IMPORTS:
                        raise PermissionError(
                            f"Sandbox Violation: Tool '{func.__name__}' attempts to import banned module '{root_module}'"
                        )
            
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    if node.func.id in self.BANNED_BUILTINS:
                        raise PermissionError(
                            f"Sandbox Violation: Tool '{func.__name__}' uses banned function '{node.func.id}'"
                        )
        
        return True

class PyHiveTimeout:
    """
    Context Manager for execution time limits.
    
    Uses 'signal' on Unix (Standard) and Threading on Windows (Fallback).
    Strictly enforcing SLAs prevents zombie processes.
    """
    
    def __init__(self, seconds: int, error_message: str = "Execution timed out"):
        self.seconds = seconds
        self.error_message = error_message
        self._timer = None

    def __enter__(self):
        try:
            if hasattr(signal, 'SIGALRM'):
                signal.signal(signal.SIGALRM, self._handle_timeout)
                signal.alarm(self.seconds)
                return self
        except ValueError:
            pass

        self._timer = threading.Timer(self.seconds, self._handle_thread_timeout)
        self._timer.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if hasattr(signal, 'SIGALRM'):
            signal.alarm(0)
        
        if self._timer:
            self._timer.cancel()

    def _handle_timeout(self, signum, frame):
        raise TimeoutError(self.error_message)

    def _handle_thread_timeout(self):
        logger.error(f"Soft Timeout Reached ({self.seconds}s).")