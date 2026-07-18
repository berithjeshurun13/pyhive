# pyhive/core/context.py
import os
from .._logging import logger as logging
from pathlib import Path
from typing import Optional, Dict, Any, List
from pydantic import Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict


class PyHiveConfig(BaseSettings):
    """
    Production-grade Configuration Manager.
    
    Loads settings from Environment Variables (prefixed with PYHIVE_).
    Validates paths and types at startup.
    """
    
    home: Path = Field(default=Path.home() / ".pyhive", alias="PYHIVE_HOME")
    debug: bool = Field(default=False, alias="PYHIVE_DEBUG")
    secret_key: str = Field(default="changeme-in-production", alias="PYHIVE_SECRET_KEY")
    
    # Network / Broker
    broker_url: str = Field(default="ws://localhost:8080", alias="PYHIVE_BROKER_URL")
    api_host: str = Field(default="127.0.0.1", alias="PYHIVE_API_HOST")
    api_port: int = Field(default=8000, alias="PYHIVE_API_PORT")

    # Resource Limits
    max_workers: int = Field(default=4, alias="PYHIVE_MAX_WORKERS")
    task_timeout: int = Field(default=300, alias="PYHIVE_TASK_TIMEOUT")  # 5 minutes
    
    # LLM Defaults
    default_llm_model: str = Field(default="gemini-1.5-pro", alias="PYHIVE_DEFAULT_MODEL")
    
    # Derived Paths - Define these as Optional fields that will be set after init
    data_dir: Optional[Path] = Field(default=None, exclude=True)
    blob_dir: Optional[Path] = Field(default=None, exclude=True)
    cache_dir: Optional[Path] = Field(default=None, exclude=True)
    logs_dir: Optional[Path] = Field(default=None, exclude=True)

    model_config = SettingsConfigDict(
        env_prefix="PYHIVE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"  # Ignore unknown env vars
    )

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.home.mkdir(parents=True, exist_ok=True)
        
        # Derived Paths
        self.data_dir: Path = self.home / "data"
        self.blob_dir: Path = self.data_dir / "blobs"
        self.cache_dir: Path = self.data_dir / "cache"
        self.logs_dir: Path = self.home / "logs"

        for p in [self.data_dir, self.blob_dir, self.cache_dir, self.logs_dir]:
            p.mkdir(parents=True, exist_ok=True)

class PyHiveContext:
    """
    The 'God Object' injected into tools.
    
    Contains:
    1. Identity: Who is running this? (User ID, Job ID)
    2. Resources: Access to DB, Storage, Vectors.
    3. State: Shared memory for workflows.
    """
    
    __slots__ = (
        'job_id', 'user_id', 'session_id', 'permissions', 
        '_config', '_services', '_state'
    )

    def __init__(
        self, 
        job_id: str,
        user_id: str = "anonymous",
        permissions: Optional[List[str]] = None,
        config: Optional[PyHiveConfig] = None,
        services: Optional[Dict[str, Any]] = None
    ):
        self.job_id = job_id
        self.user_id = user_id
        self.permissions = permissions or ["read"]
        
        self._config = config or PyHiveConfig()
        self._services = services or {}
        
        self._state: Dict[str, Any] = {}

    @property
    def config(self) -> PyHiveConfig:
        return self._config

    @property
    def db(self) -> Any:
        """Access the main database."""
        return self._get_service("db")

    @property
    def storage(self) -> Any:
        """Access Blob Storage."""
        return self._get_service("storage")

    @property
    def vectors(self) -> Any:
        """Access Vector DB."""
        return self._get_service("vectors")
    
    @property
    def emitter(self) -> Any:
        """Access the Event Emitter for this job."""
        return self._get_service("emitter")

    def _get_service(self, name: str) -> Any:
        """Helper to safely retrieve injected services."""
        service = self._services.get(name)
        if not service:
            # In production, this might raise an error or return a NullObject
            # For now, we return None but log a warning
            logging.warning(f"Context: Service '{name}' requested but not available.")
            return None
        return service

    def get(self, key: str, default: Any = None) -> Any:
        """Retrieve value from workflow state."""
        return self._state.get(key, default)

    def set(self, key: str, value: Any):
        """Save value to workflow state."""
        self._state[key] = value

    @classmethod
    def from_dict(cls, data: Dict[str, Any], global_services: Dict[str, Any]) -> 'PyHiveContext':
        """
        Hydrates a context from a JSON payload (e.g., from a worker queue).
        Re-attaches global services (DB connections) that cannot be serialized.
        """
        return cls(
            job_id=data.get("job_id", "unknown"),
            user_id=data.get("user_id", "anonymous"),
            permissions=data.get("permissions", []),
            config=global_services.get("config"),
            services=global_services
        )

    def to_dict(self) -> Dict[str, Any]:
        """Serializes the identity/state for transport."""
        return {
            "job_id": self.job_id,
            "user_id": self.user_id,
            "permissions": self.permissions,
            "state": self._state
        }