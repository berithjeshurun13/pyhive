# pyhive/core/tool.py

import inspect
import asyncio
import functools
import logging
from typing import Callable, Any, Dict, Optional, List, Type, get_type_hints
from pydantic import create_model, ValidationError, BaseModel, ConfigDict

class PyHiveToolError(Exception): pass
class ToolExecutionError(PyHiveToolError): pass
class ToolValidationError(PyHiveToolError): pass

class PyHiveTool:
    """
    Production-grade wrapper for a registrable Python function.
    
    Features:
    - Lazy Schema Generation: Doesn't parse JSON schemas until requested (startup speed).
    - Runtime Validation: Uses Pydantic V2 to enforce type safety on inputs.
    - Async Detection: Automatically handles 'async def' vs 'def'.
    - Memory Optimized: Uses __slots__ to reduce footprint.
    """
    
    __slots__ = (
        '_func', '_name', '_description', '_scopes', '_model', 
        '_is_async', '_metadata', '_schema_cache'
    )

    def __init__(
        self, 
        func: Callable, 
        name: Optional[str] = None, 
        description: Optional[str] = None,
        scopes: Optional[List[str]] = None
    ):
        if not callable(func):
            raise ValueError(f"PyHiveTool requires a callable, got {type(func)}")

        self._func = func
        self._name = name or func.__name__
        self._description = description or (func.__doc__ or "").strip()
        self._scopes = scopes or ["public"]
        self._is_async = inspect.iscoroutinefunction(func)
        self._metadata: Dict[str, Any] = {}
        self._schema_cache: Optional[Dict[str, Any]] = None
        
        self._model = self._create_validation_model()

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def scopes(self) -> List[str]:
        return self._scopes

    @property
    def is_async(self) -> bool:
        return self._is_async

    def execute(self, **kwargs) -> Any:
        """
        Synchronous execution entry point with validation.
        Blocking call - strictly for sync contexts.
        """
        if self._is_async:
            raise ToolExecutionError(f"Tool '{self._name}' is async. Use 'execute_async' instead.")

        validated_args = self._validate_inputs(kwargs)
        
        try:
            return self._func(**validated_args)
        except Exception as e:
            raise ToolExecutionError(f"Error executing tool '{self._name}': {str(e)}") from e

    async def execute_async(self, **kwargs) -> Any:
        """
        Asynchronous execution entry point with validation.
        """
        validated_args = self._validate_inputs(kwargs)

        try:
            if self._is_async:
                return await self._func(**validated_args)
            else:
                loop = asyncio.get_running_loop()
                return await loop.run_in_executor(None, lambda: self._func(**validated_args))
        except Exception as e:
            raise ToolExecutionError(f"Async error in tool '{self._name}': {str(e)}") from e

    def to_schema(self) -> Dict[str, Any]:
        """
        Generates the standard JSON Schema for LLMs (OpenAI/Gemini format).
        Cached after first generation for performance.
        """
        if self._schema_cache:
            return self._schema_cache

        try:
            schema = self._model.model_json_schema()
            
            parameters = {
                "type": "object",
                "properties": schema.get("properties", {}),
                "required": schema.get("required", [])
            }
            
            definition = {
                "name": self._name,
                "description": self._description,
                "parameters": parameters
            }
            
            self._schema_cache = definition
            return definition
        except Exception as e:
            logging.error(f"Failed to generate schema for {self._name}: {e}")
            return {}

    def _create_validation_model(self) -> Type[BaseModel]:
        """
        Dynamically creates a Pydantic model based on the function signature.
        This allows strict runtime validation of inputs.
        """
        type_hints = get_type_hints(self._func)
        signature = inspect.signature(self._func)
        fields = {}

        for param_name, param in signature.parameters.items():
            if param_name in ('self', 'cls', 'context'):
                continue

            annotation = type_hints.get(param_name, Any)
            default = param.default if param.default is not inspect.Parameter.empty else ...
            fields[param_name] = (annotation, default)

        return create_model(
            f"{self._name}_Arguments",
            __config__=ConfigDict(extra='forbid'), # Strict: Fail if extra args are passed
            **fields
        )

    def _validate_inputs(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        """Runs the Pydantic validation."""
        try:
            return self._model(**kwargs).model_dump()
        except ValidationError as e:
            raise ToolValidationError(f"Invalid arguments for '{self._name}': {e.json()}")