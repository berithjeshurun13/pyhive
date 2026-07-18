# pyhive/core/tool.py
import inspect
import asyncio
import functools
import time
import traceback
from typing import Any, Callable, Dict, List, Optional, Type, Union, get_type_hints, Literal
from pydantic import BaseModel, Field, create_model, ValidationError, ConfigDict
from loguru import logger


ECode = Literal[
    "INTERNAL_ERROR",
    "INVALID_ARGUMENTS",
    "EXECUTION_FAILED",
    "TOOL_NOT_FOUND",
    "FILE_NOT_FOUND"
]

class PyHiveLLMResponse(BaseModel):
    """The strict contract every LLM Adapter MUST return."""
    content: str = Field(default="", description="The text the LLM wants to say to the user.")
    tool_calls: List[Dict[str, Any]] = Field(default_factory=list, description="List of validated tool calls.")
    is_error: bool = Field(default=False)
    error_message: Optional[str] = None
    

class PyHiveException(Exception):
    """Base exception for all framework errors."""
    def __init__(self, message: str, code: ECode = "INTERNAL_ERROR"):
        self.message = message
        self.code = code
        super().__init__(self.message)

class ToolValidationError(PyHiveException):
    """Raised when arguments fail Pydantic validation."""
    def __init__(self, message: str):
        super().__init__(message, code="INVALID_ARGUMENTS")

class ToolExecutionError(PyHiveException):
    """Raised when the internal function crashes."""
    def __init__(self, message: str):
        super().__init__(message, code="EXECUTION_FAILED")

class ToolNotFoundError(PyHiveException):
    """Raised when requesting a missing tool."""
    def __init__(self, tool_name: str):
        super().__init__(f"Tool '{tool_name}' not registered.", code="TOOL_NOT_FOUND")


class PyHiveResponse(BaseModel):
    """
    Universal Response Wrapper.
    Ensures that CLI, API, and LLM adapters always receive the exact same structure.
    """
    success: bool = Field(..., description="Did the tool execute without crashing?")
    data: Optional[Any] = Field(None, description="The actual return value of the function")
    error: Optional[str] = Field(None, description="Error message if failed")
    error_code: Optional[str] = Field(None, description="Machine-readable error code")
    execution_time: float = Field(0.0, description="Runtime in seconds")
    timestamp: float = Field(default_factory=time.time)

    model_config = ConfigDict(arbitrary_types_allowed=True)


class PyHiveTool:
    """
    Production-grade wrapper for Python functions.
    
    Features:
    - Runtime Type Enforcement (Pydantic V2).
    - Auto-Async Bridging (Runs sync code in threadpool if needed).
    - JSON Schema Generation for LLMs.
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
        
        # Dynamically generate Pydantic model for argument validation
        try:
            self._model = self._create_validation_model()
        except Exception as e:
            logger.error(f"Failed to inspect signature for {self._name}: {e}")
            raise

    @property
    def name(self) -> str: return self._name

    @property
    def description(self) -> str: return self._description

    @property
    def scopes(self) -> List[str]: return self._scopes

    def execute(self, **kwargs) -> PyHiveResponse:
        """
        Synchronous execution entry point.
        """
        start_time = time.time()
        
        try:
            validated_args = self._validate_inputs(kwargs)

            if self._is_async:
                # DANGER: Running async code in sync context.
                # so lets assume there is an event loop running or we create a new one :(.
                try:
                    loop = asyncio.get_event_loop()
                except RuntimeError:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                
                result = loop.run_until_complete(self._func(**validated_args))
            else:
                result = self._func(**validated_args)

            return PyHiveResponse(
                success=True,
                data=result,
                execution_time=time.time() - start_time
            )

        except PyHiveException as e:
            return PyHiveResponse(success=False, error=e.message, error_code=e.code)
        except Exception as e:
            # Unexpected crashes
            logger.error(f"Tool Crash '{self._name}': {e}\n{traceback.format_exc()}")
            return PyHiveResponse(
                success=False, 
                error=str(e), 
                error_code="CRASH_EXECUTION_FAILED"
            )

    async def execute_async(self, **kwargs) -> PyHiveResponse:
        """
        Asynchronous execution entry point.
        Safe for high-concurrency environments (FastAPI/WebSockets).
        """
        start_time = time.time()

        try:
            validated_args = self._validate_inputs(kwargs)

            if self._is_async:
                result = await self._func(**validated_args)
            else:
                # AUTO-THREADING:
                # If the tool is synchronous (blocking), we offload it 
                # to a thread so we don't block the async event loop.
                loop = asyncio.get_running_loop()
                result = await loop.run_in_executor(
                    None, 
                    functools.partial(self._func, **validated_args)
                )

            return PyHiveResponse(
                success=True,
                data=result,
                execution_time=time.time() - start_time
            )

        except PyHiveException as e:
            return PyHiveResponse(success=False, error=e.message, error_code=e.code)
        except Exception as e:
            logger.error(f"Async Tool Crash '{self._name}': {e}")
            return PyHiveResponse(
                success=False, 
                error=str(e), 
                error_code="CRASH_EXECUTION_FAILED"
            )

    def to_schema(self) -> Dict[str, Any]:
        """
        Generates the standard JSON Schema for LLMs.
        Compatible with OpenAI function calling and Gemini.
        """
        if self._schema_cache:
            return self._schema_cache

        try:
            # 1. Generate schema with inline references where possible
            pydantic_schema = self._model.model_json_schema(mode="validation")
            
            # 2. Extract core components
            properties = pydantic_schema.get("properties", {})
            required = pydantic_schema.get("required", [])
            
            # 3. Clean up Pydantic noise (Optional but keeps tokens low)
            for field in properties.values():
                if isinstance(field, dict):
                    field.pop("title", None) # Remove 'title' keys to save tokens

            # 4. If Pydantic still generated top-level definitions, we must include them
            parameters = {
                "type": "object",
                "properties": properties,
                "required": required
            }
            
            if "$defs" in pydantic_schema:
                parameters["$defs"] = pydantic_schema["$defs"]
            
            definition = {
                "name": self._name,
                "description": self._description,
                "parameters": parameters
            }
            
            self._schema_cache = definition
            return definition
            
        except Exception as e:
            logger.error(f"Schema generation failed for {self._name}: {e}")
            return {
                "name": self._name,
                "description": "Error generating schema.",
                "parameters": {"type": "object", "properties": {}, "required": []}
            }

    def _create_validation_model(self) -> Type[BaseModel]:
        """
        Introspects the function signature to create a Pydantic model.
        """
        type_hints = get_type_hints(self._func)
        signature = inspect.signature(self._func)
        fields = {}

        for param_name, param in signature.parameters.items():
            if param_name in ('self', 'cls', 'context'):
                continue

            annotation = type_hints.get(param_name, Any)
            
            if param.default is not inspect.Parameter.empty:
                default = param.default
            else:
                default = ... # Pydantic Ellipsis means "Required"

            fields[param_name] = (annotation, default)

        return create_model(
            f"{self._name}_Args",
            __config__=ConfigDict(extra='forbid'), # Strict input validation
            **fields
        )

    def _validate_inputs(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        """Runs validation against the dynamic Pydantic model."""
        try:
            return self._model(**kwargs).model_dump()
        except ValidationError as e:
            error_msgs = [f"{err['loc'][0]}: {err['msg']}" for err in e.errors()]
            raise ToolValidationError(f"Invalid arguments: {'; '.join(error_msgs)}")
    
    