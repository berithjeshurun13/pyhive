# pyhive/core/registry.py

import threading
import re
import inspect
from typing import Dict, List, Optional, Callable, Union, Any, Set
from pydantic import BaseModel, Field, field_validator
from loguru import logger

from .base import PyHiveTool, PyHiveException

class PyHiveMetadata(BaseModel):
    """
    Standardized Metadata for Tools and Modules.
    Ensures that every tool has a version and author for auditing.
    """
    name: str = Field(..., pattern=r"^[a-zA-Z0-9_]+$")
    description: str = Field(default="No description provided.")
    version: str = Field(default="0.1.0")
    author: str = Field(default="Unknown")
    tags: Set[str] = Field(default_factory=set)
    
    _module: str = "root"

    @field_validator("name")
    def validate_name(cls, v):
        if len(v) > 64:
            raise ValueError("Name too long (max 64 chars).")
        return v

class PyHiveModule:
    """
    A logical container for grouping related tools.
    Example: 'filesystem' module contains 'read_file', 'write_file'.
    """
    def __init__(self, name: str, description: str = ""):
        self.metadata = PyHiveMetadata(name=name, description=description)
        self._tools: Dict[str, PyHiveTool] = {}
        
    def add_tool(self, tool: PyHiveTool):
        """Adds a tool to this module's namespace."""
        self._tools[tool.name] = tool
        tool._metadata = getattr(tool, '_metadata', {})
        tool._metadata['module'] = self.metadata.name

    @property
    def tools(self) -> List[PyHiveTool]:
        return list(self._tools.values())

class PyHiveRegistry:
    """
    Thread-Safe Central Repository.
    
    Features:
    - Conflict Resolution: Raise error or overwrite duplicates.
    - Scoped Lookup: Find all tools tagged "admin".
    - Lazy Discovery: Can scan Python modules for tools.
    """

    def __init__(self):
        self._tools: Dict[str, PyHiveTool] = {}
        self._modules: Dict[str, PyHiveModule] = {}
        self._lock = threading.RLock()
        
        self._schema_cache: Optional[List[Dict]] = None

    def register(
        self, 
        func_or_tool: Union[Callable, PyHiveTool] = None, 
        *,
        name: Optional[str] = None, 
        description: Optional[str] = None,
        module: str = "root",
        overwrite: bool = False,
        scopes: Optional[List[str]] = None
    ):
        """
        Universal Registration Decorator/Method.
        
        Usage:
            @registry.register(name="my_tool")
            def my_func(): ...
        """
        def decorator(obj):
            if isinstance(obj, PyHiveTool):
                tool = obj
            elif callable(obj):
                tool_name = name or obj.__name__
                tool_desc = description or obj.__doc__
                tool = PyHiveTool(obj, name=tool_name, description=tool_desc, scopes=scopes)
            else:
                raise TypeError(f"Cannot register object of type {type(obj)}")

            self._register_impl(tool, module_name=module, overwrite=overwrite)
            return tool

        if func_or_tool is None:
            return decorator
        else:
            return decorator(func_or_tool)

    def _register_impl(self, tool: PyHiveTool, module_name: str, overwrite: bool):
        """Thread-safe implementation logic."""
        with self._lock:
            if tool.name in self._tools and not overwrite:
                logger.error(f"Registry Conflict: Tool '{tool.name}' already exists.")
                raise PyHiveException(
                    f"Tool '{tool.name}' is already registered. Use overwrite=True to replace.",
                    code="REGISTRY_CONFLICT"
                )

            if module_name not in self._modules:
                self._modules[module_name] = PyHiveModule(name=module_name)
            
            self._modules[module_name].add_tool(tool)
            self._tools[tool.name] = tool
            
            self._schema_cache = None
            logger.debug(f"Registered tool '{tool.name}' in module '{module_name}'")

    def get_tool(self, name: str) -> PyHiveTool:
        """Retrieves a tool by name (O(1) lookup)."""
        with self._lock:
            if name not in self._tools:
                raise PyHiveException(f"Tool '{name}' not found.", code="TOOL_NOT_FOUND")
            return self._tools[name]

    def list_tools(self, module: Optional[str] = None) -> List[PyHiveTool]:
        """Lists tools, optionally filtered by module."""
        with self._lock:
            if module:
                if module not in self._modules:
                    return []
                return self._modules[module].tools
            return list(self._tools.values())

    def get_llm_definitions(self) -> List[Dict[str, Any]]:
        """
        Returns the list of JSON Schemas for all registered tools.
        Optimized for passing directly to `openai.chat.completions.create(tools=...)`.
        """
        with self._lock:
            if self._schema_cache is not None:
                return self._schema_cache

            definitions = []
            for tool in self._tools.values():
                try:
                    schema = tool.to_schema()
                    if "type" not in schema: 
                        schema = {"type": "function", "function": schema}
                    definitions.append(schema)
                except Exception as e:
                    logger.error(f"Failed to generate schema for {tool.name}: {e}")
            
            self._schema_cache = definitions
            return definitions

    def unregister(self, name: str):
        """Safely removes a tool."""
        with self._lock:
            if name in self._tools:
                del self._tools[name]
                self._schema_cache = None
                logger.info(f"Unregistered tool: {name}")