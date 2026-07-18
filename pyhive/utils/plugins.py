# pyhive/utils/plugins.py
import json
import re
from pathlib import Path
from typing import List, Optional, Dict, Any, Set
from pydantic import BaseModel, Field, ValidationError, field_validator

import time, sys
import threading
import importlib.util
import traceback
from .._logging import logger


# Pre-compiled regex for Semantic Versioning (Major.Minor.Patch)
SEMVER_REGEX = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-((?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?(?:\+([0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$")

class ManifestSchema(BaseModel):
    """
    Strict Pydantic definition for manifest.json files.
    """
    name: str = Field(..., min_length=3, max_length=50, pattern=r"^[a-z0-9_]+$")
    version: str = Field(..., description="Semantic Versioning (e.g. 1.0.0)")
    author: str = Field(default="Unknown")
    description: str = Field(default="")
    entry_point: str = Field(..., pattern=r"^.+\.py$")
    min_pyhive_version: str = Field(default="0.1.0")
    dependencies: List[str] = Field(default_factory=list)
    
    @field_validator("version", "min_pyhive_version")
    @classmethod
    def validate_semver(cls, v: str) -> str:
        if not SEMVER_REGEX.match(v):
            raise ValueError(f"Invalid version format '{v}'. Must be SemVer (e.g. 1.0.0)")
        return v

class PyHiveManifest:
    """
    Production-grade parser for plugin metadata.
    Enforces compatibility checks against the running framework version.
    """
    
    FRAMEWORK_VERSION = "1.0.0"

    def __init__(self):
        pass

    def load(self, plugin_dir: Path) -> ManifestSchema:
        """
        Parses, validates, and checks compatibility of a manifest.json file.
        
        Args:
            plugin_dir: Path to the plugin root directory.
            
        Returns:
            ManifestSchema: Validated metadata object.
            
        Raises:
            FileNotFoundError: If manifest.json is missing.
            ValidationError: If JSON is malformed or invalid.
            EnvironmentError: If plugin is incompatible with current PyHive version.
        """
        manifest_path = plugin_dir / "manifest.json"
        
        if not manifest_path.exists():
            raise FileNotFoundError(f"Missing 'manifest.json' in {plugin_dir}")

        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                raw_data = json.load(f)
            
            manifest = ManifestSchema(**raw_data)
            
            self._check_compatibility(manifest.name, manifest.min_pyhive_version)
            
            return manifest

        except json.JSONDecodeError as e:
            raise ValidationError(f"Invalid JSON in manifest: {e}") from e

    def _check_compatibility(self, plugin_name: str, required_version: str):
        """Compares Semantic Versions."""
        try:
            current = self._parse_version(self.FRAMEWORK_VERSION)
            required = self._parse_version(required_version)
            
            if current < required:
                raise EnvironmentError(
                    f"Plugin '{plugin_name}' requires PyHive >= {required_version} "
                    f"(Current: {self.FRAMEWORK_VERSION})"
                )
        except ValueError:
            pass

    @staticmethod
    def _parse_version(v_str: str) -> tuple:
        """Helper to convert '1.2.3' to (1, 2, 3)."""
        clean_ver = v_str.split('-')[0].split('+')[0]
        return tuple(map(int, clean_ver.split(".")))
    

from abc import ABC, abstractmethod
from typing import Any, Optional

class PyHivePlugin(ABC):
    """
    Abstract Base Class for third-party extensions.
    
    Lifecycle:
    1. __init__: Framework injects dependencies.
    2. setup(): Plugin registers tools and initializes resources.
    3. teardown(): Plugin cleans up (e.g. closes DB connections).
    """

    def __init__(self, registry: Any, context: Any, metadata: ManifestSchema):
        """
        Args:
            registry: The central PyHiveRegistry instance.
            context: The global PyHiveContext (config, paths, user).
            metadata: The validated manifest data for this plugin.
        """
        self.registry = registry
        self.context = context
        self.metadata = metadata
        self._registered_tool_names: list[str] = []

    @abstractmethod
    def setup(self):
        """
        Entry point. You MUST override this.
        Use self.registry.register() here.
        """
        pass

    def teardown(self):
        """
        Optional cleanup hook.
        Called when the plugin is unloaded or the application stops.
        """
        pass

    def register_tool(self, func: Any, **kwargs):
        """
        Wrapper around registry.register that tracks tool ownership.
        This allows the framework to auto-remove tools if the plugin crashes.
        """
        self.registry.register(func, **kwargs)
        
        name = kwargs.get("name") or getattr(func, "__name__", "unknown")
        self._registered_tool_names.append(name)



class PyHiveHotReloader:
    """
    Development-only file watcher.
    
    Monitors the 'tools/' directory. When a file changes:
    1. Attempts to reload the Python module.
    2. If successful, updates the Registry.
    3. If syntax error, catches it and logs the traceback without crashing.
    """

    def __init__(self, registry: Any, tools_dir: str, interval: float = 1.0):
        self.registry = registry
        self.tools_dir = Path(tools_dir).resolve()
        self.interval = interval
        
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        
        self._snapshot: Dict[str, float] = {}
        
        self._loaded_modules: Set[str] = set()

    def start(self):
        """Starts the background watcher thread."""
        if not self.tools_dir.exists():
            logger.warning(f"Tools directory '{self.tools_dir}' not found. Reloader disabled.")
            return

        if self._thread is not None:
            return

        self._scan_snapshot()
        
        self._thread = threading.Thread(target=self._watch_loop, name="PyHiveHotReloader", daemon=True)
        self._thread.start()
        logger.info(f"Hot Reloader active. Watching: {self.tools_dir}")

    def stop(self):
        """Stops the watcher thread cleanly."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None

    def _watch_loop(self):
        """Main polling loop."""
        while not self._stop_event.is_set():
            try:
                self._check_for_changes()
            except Exception as e:
                logger.error(f"Watcher loop error: {e}")
            
            time.sleep(self.interval)

    def _scan_snapshot(self):
        """Builds initial file map."""
        for path in self.tools_dir.rglob("*.py"):
            if path.name.startswith("__"): continue
            self._snapshot[str(path)] = path.stat().st_mtime

    def _check_for_changes(self):
        """Detects modifications and new files."""
        current_files = set()

        for path in self.tools_dir.rglob("*.py"):
            if path.name.startswith("__"): continue
            
            str_path = str(path)
            current_files.add(str_path)
            
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue

            last_mtime = self._snapshot.get(str_path)
            
            if last_mtime is None:
                logger.info(f"New tool file detected: {path.name}")
                self._reload_module(path)
                self._snapshot[str_path] = mtime
                
            elif mtime > last_mtime:
                logger.info(f"Modification detected in: {path.name}")
                self._reload_module(path)
                self._snapshot[str_path] = mtime

        deleted = set(self._snapshot.keys()) - current_files
        for d in deleted:
            logger.info(f"File removed: {d}")
            del self._snapshot[d]

    def _reload_module(self, path: Path):
        """
        Safe module reloader.
        Catches syntax errors so the app doesn't crash during dev edits.
        """
        module_name = path.stem
        
        try:
            spec = importlib.util.spec_from_file_location(module_name, path)
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                
                spec.loader.exec_module(module)
                
                sys.modules[module_name] = module
                self._loaded_modules.add(module_name)
                logger.info(f"Successfully reloaded '{module_name}'")
                
        except SyntaxError as e:
            logger.error(f"Syntax Error in '{path.name}': Line {e.lineno}\n{e.text}")
        except Exception as e:
            logger.error(f"Failed to reload '{path.name}': {e}")
            logger.debug(traceback.format_exc())