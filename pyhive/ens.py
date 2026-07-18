import shutil
import os
import threading
from pathlib import Path
from typing import Dict, Optional, Union

class PyHiveEnvironment:
    """Manages internal and external tools for PyHive in a thread-safe manner."""
    
    def __init__(self):
        self._tools: Dict[str, Path] = {}
        self._custom_paths: list[Path] = []
        self._lock = threading.Lock()

    def add_search_path(self, path: Union[str, Path]):
        """Register a custom directory to search for executables."""
        path_obj = Path(path).resolve()
        if path_obj.is_dir():
            with self._lock:
                if path_obj not in self._custom_paths:
                    self._custom_paths.append(path_obj)

    def register_tool(self, name: str, exact_path: Union[str, Path]):
        """Manually hardcode a tool's path safely."""
        path_obj = Path(exact_path).resolve()
        if path_obj.exists() and os.access(path_obj, os.X_OK):
            with self._lock:
                self._tools[name] = path_obj
        else:
            raise FileNotFoundError(f"Executable not found or lacks permissions at: {exact_path}")

    def get_tool(self, name: str, executable_name: Optional[str] = None) -> Optional[Path]:
        """Locate a tool safely across multiple threads."""
        
        with self._lock:
            if name in self._tools:
                return self._tools[name]

            exe_name = executable_name or name
            
            for search_path in self._custom_paths:
                potential_path = search_path / exe_name
                if potential_path.exists() and os.access(potential_path, os.X_OK):
                    self._tools[name] = potential_path
                    return potential_path
                
                if os.name == 'nt' and not exe_name.lower().endswith('.exe'):
                    potential_path_exe = search_path / f"{exe_name}.exe"
                    if potential_path_exe.exists() and os.access(potential_path_exe, os.X_OK):
                        self._tools[name] = potential_path_exe
                        return potential_path_exe

            system_path = shutil.which(exe_name)
            if system_path:
                path_obj = Path(system_path)
                self._tools[name] = path_obj
                return path_obj

            return None

    def require_tool(self, name: str, executable_name: Optional[str] = None) -> Path:
        """Like get_tool, but raises an error if the tool isn't found."""
        tool_path = self.get_tool(name, executable_name)
        if not tool_path:
            raise EnvironmentError(f"Required tool '{name}' could not be found in the environment.")
        return tool_path