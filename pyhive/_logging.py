# pyhive/_logging.py

from typing import Literal, Optional
from loguru import logger
from datetime import datetime
import time, threading
import requests, socketio
import sys, pathlib, os


USERNAME = None
DOC_LOG_LOCATION = None
clear_terminal = None
if sys.platform == 'win32' :
    DOC_LOG_LOCATION = pathlib.Path(pathlib.Path().home(), "Documents", "PyLMHiveSTEAM", "logs")
    clear_terminal = lambda : os.system("cls")
    try :
        os.makedirs(DOC_LOG_LOCATION, exist_ok=True)
    except Exception as e :
        raise Exception(f'Something Happend : {e}')
else :
    clear_terminal = lambda : os.system("clear")
    raise RuntimeError("Not Implemented on Other OS")

logger.remove()
clear_terminal()

class MyLogger(object) : 
    def __init__(self, logger) :
        super().__init__()
        self.__lgr = logger
        
    def info(self, message : str) :
        self.__lgr.info(message)
    
    def error(self, message : str) :
        self.__lgr.error(message)
    
    def debug(self, message : str) :
        self.__lgr.debug(message)
    
    def critical(self, message : str) :
        self.__lgr.critical(message)
    
    def warning(self, message : str) :
        self.__lgr.warning(message)
    
    def exception(self, message : str) :
        self.__lgr.exception(message)
    
    def success(self, message : str) :
        self.__lgr.success(message)

    def traceback(self, message : str) :
        self.__lgr.trace(message)
    
    def options(self) :
        return self.__lgr 
    
    @property
    def opt(self) : return self.__lgr

class Logger:
    def __init__(self, log_file: str = None, rotation="10 MB", retention="7 days", compression="zip", console=True):
        if log_file is None:
            log_file = os.path.join(DOC_LOG_LOCATION, f"{datetime.now().strftime('%H-%M-%S')}.log")

        _fmt : str = "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>PyHive</cyan>: {message}"
        logger.add(log_file, rotation=rotation, retention=retention, compression=compression, enqueue=True, format=_fmt)
        my_vars["clogfile"] = log_file
        if console:
            logger.add(sys.stderr, format=_fmt, colorize=True)

        logger.info(f'Started Logging...')
    def get_logger(self):
        return logger




class ProcessUpdater:
    def __init__(
        self,
        session_id: str,
        url: str,
        _type: Literal["sender", "receiver"] = "sender"
    ):
        self.__SSID = session_id
        self.__URL = url.rstrip("/")
        self._type = _type
        self.__owner_token: Optional[str] = None
        self.__running = False

        self.__sio = None

        if self._type == "sender":
            self.__create_room()
        else:
            self.__connect_receiver()


    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._type == "sender":
            self.destroy()
        elif self._type == "receiver":
            self.stop()


    def __create_room(self):
        r = requests.post(
            f"{self.__URL}/create_room",
            params={"ids": self.__SSID},
            timeout=3
        )

        data = r.json()

        if not data.get("status"):
            raise RuntimeError(f"Room creation failed: {data}")

        self.__owner_token = data["owner_token"]
        print(f"[SENDER] Room created → {self.__SSID}")

    def update(self, **payload):
        if self._type != "sender":
            raise RuntimeError("Only sender can send updates")

        requests.post(
            f"{self.__URL}/log",
            params={"ids": self.__SSID},
            json=payload,
            timeout=3
        )

    def destroy(self):
        if not self.__owner_token:
            return

        requests.post(
            f"{self.__URL}/destroy_room",
            params={"ids": self.__SSID},
            headers={"Owner-Token": self.__owner_token},
            timeout=3
        )

        print(f"[SENDER] Room destroyed => {self.__SSID}")

    
    def __connect_receiver(self):
        self.__sio = socketio.Client()
        self.__running = True

        @self.__sio.event
        def connect():
            print("[RECEIVER] Connected")
            self.__sio.emit("join", {"ids": self.__SSID})

        @self.__sio.on("log")
        def on_log(data):
            self.on_log(data)

        @self.__sio.on("room_closed")
        def on_close(data):
            print("[RECEIVER] Room closed")
            self.stop()

        @self.__sio.event
        def disconnect():
            print("[RECEIVER] Disconnected")

        self.__thread = threading.Thread(target=self.__run_socket)
        self.__thread.daemon = True
        self.__thread.start()

    def __run_socket(self):
        self.__sio.connect(self.__URL)
        self.__sio.wait()

    def stop(self):
        if self.__sio:
            self.__running = False
            self.__sio.disconnect()

    def on_log(self, data: dict):
        """
        Override this method if you want custom behavior
        """
        print("[RECEIVER] LOG:", data)


class GlobVars:
    _instance = None
    _lock = threading.RLock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._data = {}
                cls._instance._data_lock = threading.RLock()
            return cls._instance
    def __getitem__(self, key):
        with self._data_lock:
            return self._data[key]
    def __setitem__(self, key, value):
        with self._data_lock:
            self._data[key] = value
    def __delitem__(self, key):
        with self._data_lock:
            del self._data[key]
    def get(self, key, default=None):
        with self._data_lock:
            return self._data.get(key, default)
    def setdefault(self, key, default=None):
        with self._data_lock:
            return self._data.setdefault(key, default)
    def update(self, other):
        with self._data_lock:
            self._data.update(other)
    def items(self):
        with self._data_lock:
            return list(self._data.items())
    def __contains__(self, key):
        with self._data_lock:
            return key in self._data
my_vars = GlobVars()
logger = MyLogger(Logger().get_logger())
