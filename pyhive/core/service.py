import subprocess
import os
from pathlib import Path
from typing import Literal
import msgpack
from .base import PyHiveException, ECode
import threading

class PyHiveRuntimeManager:
    def __init__(self):
        self.__DATA: dict = dict()
        self.__processes: dict[str, subprocess.Popen] = dict()

    def load(self, _file: Path | str):
        if isinstance(_file, str):
            _file = Path(_file)

        if not _file.exists():
            raise PyHiveException(f"The rnv file '{_file}' cannot be found", "FILE_NOT_FOUND")
        
        if _file.suffix != '.rnv':
            raise PyHiveException("File not supported. Supports only files with extension .rnv", "INVALID_FILE_TYPE")
        
        try:
            with open(_file, 'rb') as f:
                data = msgpack.unpack(f)
                if isinstance(data, dict):
                    self.__DATA.update(data)
                else:
                    raise PyHiveException("Invalid format: Root level must be a dictionary", "INVALID_FORMAT")
        except Exception as e:
            raise PyHiveException(f"Failed to parse msgpack file: {e}", "PARSE_ERROR")

    def run(self, name: str, **kwargs):
        """Runs the executable associated with the service name."""
        if name not in self.__DATA:
            raise PyHiveException(f"Service '{name}' not found in loaded configurations.", "SERVICE_NOT_FOUND")
            
        service_config = self.__DATA[name]
        executable = service_config.get('bin/exe')
        
        if not executable:
            raise PyHiveException(f"Missing 'bin/exe' for service '{name}'", "INVALID_CONFIG")

        cmd_kwargs = service_config.get('kwargs', {}).copy()
        cmd_kwargs.update(kwargs)

        cmd = [executable]
        for key, value in cmd_kwargs.items():
            prefix = "-" if len(key) == 1 else "--"
            cmd.append(f"{prefix}{key}")
            if value is not True:
                cmd.append(str(value))

        try:
            process = subprocess.Popen(cmd)
            self.__processes[name] = process
            return process
        except Exception as e:
            raise PyHiveException(f"Failed to run '{name}': {e}", "RUNTIME_ERROR")

    def stop(self, name: str):
        """Terminates the running service without blocking the main thread."""
        if name not in self.__processes:
            raise PyHiveException(f"Service '{name}' is not currently running.", "SERVICE_NOT_RUNNING")

        process = self.__processes.pop(name)
        
        def _terminate_sequence():
            try:
                process.terminate()
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
        threading.Thread(target=_terminate_sequence, daemon=True).start()

    def save(self, name: str, _path: str | Path, **kwargs):
        """Saves a service configuration to a .rnv file."""
        if isinstance(_path, str):
            _path = Path(_path)
            
        if _path.suffix != '.rnv':
            _path = _path.with_suffix('.rnv')

        executable = kwargs.get('bin/exe')
        if not executable:
            raise PyHiveException("Requires a 'bin/exe' value to save the configuration.", "INVALID_CONFIG")

        service_kwargs = kwargs.get('kwargs', {})

        payload = {
            name: {
                'bin/exe': executable,
                'kwargs': service_kwargs
            }
        }

        try:
            with open(_path, 'wb') as f:
                msgpack.pack(payload, f)
        except Exception as e:
            raise PyHiveException(f"Failed to write to '{_path}': {e}", "WRITE_ERROR")