# pyhive/utils/env.py
import os
import sys
import shutil
import platform
from pathlib import Path
from typing import Optional, Union, List
from .._logging import logger as logging
class PyHivePathResolver(object):
    """
    Cross-platform locator for external binaries and models.
    
    Resolves paths dynamically by checking:
    1. The 'PYHIVE_HOME' environment variable (Set by Bootstrapper).
    2. The local user directory (~/.pyhive or %LOCALAPPDATA%/PyHive).
    3. The system PATH (e.g., if user installed Tesseract via brew/apt).

    
    >>> resolver = PyHivePathResolver()
    
    >>> # 1. Get Tesseract
    >>> tesseract_cmd = resolver.find_binary("tesseract")
    >>> if not tesseract_cmd:
    >>>     raise FileNotFoundError("Tesseract not found. Please run 'pyhive-setup init'.")
    >>> # 2. Get Model
    >>> model_path = resolver.find_model("eng.traineddata")
    """

    def __init__(self):
        super().__init__()
        self._os = platform.system()
        
        # 1. Determine Root Directory
        self._home = self._detect_home()
        
        # 2. Define Subdirectories
        self._bin_dir = self._home / "bin"
        self._models_dir = self._home / "models"
        self._data_dir = self._home / "data"

    def _detect_home(self) -> Path:
        """Finds the installation root of the dependency pack."""
        # Priority 1: Explicit Env Var
        env_home = os.environ.get("PYHIVE_HOME")
        if env_home:
            return Path(env_home).resolve()

        # Priority 2: OS-Specific User Data
        if self._os == "Windows":
            return Path(os.environ.get("LOCALAPPDATA", "~")) / "PyHive"
        elif self._os == "Darwin": # MacOS
             return Path.home() / "Library" / "Application Support" / "PyHive"
        else: # Linux
             return Path.home() / ".pyhive"

    def find_binary(self, name: str) -> Optional[str]:
        """
        Locates an executable (e.g., 'tesseract', 'ffmpeg').
        Auto-appends .exe on Windows if missing.
        """
        # Normalize name for Windows
        if self._os == "Windows" and not name.lower().endswith(".exe"):
            name += ".exe"

        # 1. Check PyHive Local Binaries (Highest Priority)
        local_path = self._bin_dir / name
        if local_path.exists() and local_path.is_file():
            return str(local_path)
            
        # 2. Check System PATH (Fallback)
        # This allows users to use their own system-installed FFmpeg/Tesseract
        system_path = shutil.which(name)
        if system_path:
            return system_path
            
        return None

    def find_model(self, name: str) -> Optional[str]:
        """
        Locates a model file (e.g., 'yolov8.pt', 'eng.traineddata').
        Checks 'models/' directory recursively.
        """
        # 1. Direct check in models dir
        direct_path = self._models_dir / name
        if direct_path.exists():
            return str(direct_path)
            
        # 2. Recursive search (if model is inside a subdir like 'ocr/eng.traineddata')
        # Limiting depth to avoid slow startups
        try:
            for file in self._models_dir.rglob(name):
                return str(file)
        except Exception:
            pass

        return None

    def get_data_path(self, filename: str, create_if_missing: bool = False) -> str:
        """
        Returns a writeable path for persistent data (logs, dbs).
        """
        target = self._data_dir / filename
        
        if create_if_missing:
            target.parent.mkdir(parents=True, exist_ok=True)
            
        return str(target)

    @property
    def home(self) -> str:
        """Returns the resolved root path as string."""
        return str(self._home)



class PyHiveEnvManager(object):
    """
    Manages system and process-level environment variables.
    
    Crucial for pointing the framework to the '5GB Dependency Pack' (PYHIVE_HOME).
    Handles cross-platform persistence (Registry on Windows, RC files on Unix).


    >>> manager = PyHiveEnvManager()
    >>> # 1. Check if configured
    >>> if not manager.validate_path("PYHIVE_HOME"):
    >>>     print("Setup required!")
    >>>     install_path = "C:/Users/Dev/PyHive"
    >>>     if manager.persist("PYHIVE_HOME", install_path):
    >>>         print("Environment configured successfully.")
    """
    
    def __init__(self):
        super().__init__()
        self._os = platform.system()
        self._shell = os.environ.get("SHELL", "/bin/bash")

    def get(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """Safe retrieval of an environment variable."""
        return os.environ.get(key, default)

    def set_runtime(self, key: str, value: str) -> None:
        """
        Sets a variable for the current process only.
        Used immediately after installation before a restart.
        """
        os.environ[key] = str(value)

    def validate_path(self, key: str) -> bool:
        """
        Checks if the path pointed to by an env var actually exists.
        Useful for verifying if PYHIVE_HOME is broken.
        """
        val = self.get(key)
        if not val:
            return False
        return Path(val).exists()

    def persist(self, key: str, value: str) -> bool:
        """
        Attempts to permanently set an environment variable.
        
        Windows: Writes to HKCU Environment Registry.
        Linux/Mac: Appends export to ~/.bashrc or ~/.zshrc.
        """
        value = str(value)
        
        self.set_runtime(key, value)

        if self._os == "Windows":
            return self._persist_windows(key, value)
        else:
            return self._persist_unix(key, value)

    def _persist_windows(self, key: str, value: str) -> bool:
        """Writes to the Windows Registry (HKCU)."""
        try:
            import winreg
            key_path = r'Environment'
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_ALL_ACCESS) as reg_key:
                winreg.SetValueEx(reg_key, key, 0, winreg.REG_SZ, value)
            
            try:
                import ctypes
                HWND_BROADCAST = 0xFFFF
                WM_SETTINGCHANGE = 0x001A
                SMTO_ABORTIFHUNG = 0x0002
                result = ctypes.c_long()
                ctypes.windll.user32.SendMessageTimeoutW(
                    HWND_BROADCAST, WM_SETTINGCHANGE, 0, u'Environment',
                    SMTO_ABORTIFHUNG, 5000, ctypes.byref(result)
                )
            except Exception:
                pass

            return True
        except ImportError:
            logging.error("PyHiveEnvManager: winreg module missing on Windows.")
            return False
        except Exception as e:
            logging.error(f"PyHiveEnvManager: Failed to write Windows Registry: {e}")
            return False

    def _persist_unix(self, key: str, value: str) -> bool:
        """Appends export command to shell config files."""
        home = Path.home()
        config_files: List[Path] = []
        
        if "zsh" in self._shell:
            config_files.append(home / ".zshrc")
        elif "bash" in self._shell:
            config_files.append(home / ".bashrc")
            config_files.append(home / ".bash_profile")
        else:
            config_files.append(home / ".profile")

        export_cmd = f'\n# Added by PyHive Setup\nexport {key}="{value}"\n'
        
        success = False
        for rc_file in config_files:
            try:
                if rc_file.exists():
                    content = rc_file.read_text()
                    if f"export {key}=" in content:
                        logging.info(f"Variable {key} already exists in {rc_file}. Skipping append.")
                        success = True
                        continue

                with open(rc_file, "a") as f:
                    f.write(export_cmd)
                
                logging.info(f"Persisted {key} to {rc_file}")
                success = True
            except Exception as e:
                logging.error(f"PyHiveEnvManager: Failed to write to {rc_file}: {e}")
        
        return success