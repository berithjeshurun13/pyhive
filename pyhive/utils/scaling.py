# pyhive/utils/scaling.py

import threading
import time
from typing import Optional, Dict, List, Callable
from loguru import logger
import heapq


class PyHiveGPUAllocator:
    """
    Production-grade VRAM manager.
    
    Prevents Out-Of-Memory (OOM) crashes by enforcing a reservation system.
    Tools must 'acquire' memory before loading models and 'release' it after.
    
    Features:
    - Real-time VRAM monitoring via pynvml.
    - Thread-safe reservations.
    - CPU Fallback signal.
    """

    def __init__(self, reserve_buffer_mb: int = 500):
        self._lock = threading.RLock()
        self._reserve_buffer = reserve_buffer_mb
        self._pynvml_available = False
        self._handle = None
        self._total_memory = 0
        
        self._reserved_memory = 0 

        self._init_nvml()

    def _init_nvml(self):
        """Loads NVIDIA Management Library if available."""
        try:
            import pynvml
            pynvml.nvmlInit()
            self._handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            info = pynvml.nvmlDeviceGetMemoryInfo(self._handle)
            self._total_memory = info.total // 1024 // 1024
            self._pynvml_available = True
            logger.info(f"GPU Detected: {self._total_memory}MB VRAM available.")
        except ImportError:
            logger.warning("pynvml not found. GPU management disabled (CPU only).")
        except Exception as e:
            logger.error(f"Failed to initialize NVML: {e}")

    def allocate(self, required_mb: int, timeout: float = 5.0) -> bool:
        """
        Attempts to reserve VRAM.
        
        Args:
            required_mb: Estimated VRAM usage.
            timeout: How long to wait if busy.
        
        Returns:
            True if allocated (Go ahead).
            False if denied (Fallback to CPU or Queue).
        """
        if not self._pynvml_available:
            return False

        start_time = time.time()
        
        while time.time() - start_time < timeout:
            with self._lock:
                try:
                    import pynvml
                    info = pynvml.nvmlDeviceGetMemoryInfo(self._handle)
                    free_real_mb = info.free // 1024 // 1024
                    
                    effective_free = free_real_mb - self._reserved_memory - self._reserve_buffer
                    
                    if effective_free >= required_mb:
                        self._reserved_memory += required_mb
                        logger.debug(f"Allocated {required_mb}MB. Remaining effective: {effective_free - required_mb}MB")
                        return True
                except Exception as e:
                    logger.error(f"NVML Error during allocation check: {e}")
                    return False
            
            time.sleep(0.5)
            
        logger.warning(f"Allocation Timeout: Could not secure {required_mb}MB VRAM after {timeout}s.")
        return False

    def release(self, amount_mb: int):
        """Releases the virtual reservation."""
        with self._lock:
            self._reserved_memory = max(0, self._reserved_memory - amount_mb)
            logger.debug(f"Released {amount_mb}MB reservation.")

    def get_stats(self) -> Dict[str, int]:
        """Returns current VRAM usage for telemetry."""
        if not self._pynvml_available:
            return {"gpu_available": False}
        
        try:
            import pynvml
            info = pynvml.nvmlDeviceGetMemoryInfo(self._handle)
            return {
                "gpu_available": True,
                "total_mb": self._total_memory,
                "free_real_mb": info.free // 1024 // 1024,
                "reserved_pending_mb": self._reserved_memory
            }
        except Exception:
            return {"gpu_available": False, "error": "NVML Failure"}
        

class PyHiveLoadBalancer:
    """
    Distributes jobs to the least busy worker.
    
    Strategy: Least Connections (Queue Depth).
    Uses a Min-Heap approach for retrieval of the best worker.
    """

    def __init__(self):
        self._lock = threading.RLock()
        self._worker_loads: Dict[str, int] = {} # {worker_id: active_job_count}
        self._worker_capabilities: Dict[str, List[str]] = {} # {worker_id: ['gpu', 'ocr']}

    def register_worker(self, worker_id: str, capabilities: List[str]):
        """Adds a new worker to the pool."""
        with self._lock:
            self._worker_loads[worker_id] = 0
            self._worker_capabilities[worker_id] = capabilities
            logger.info(f"Worker {worker_id} registered with capabilities: {capabilities}")

    def deregister_worker(self, worker_id: str):
        """Removes a dead worker."""
        with self._lock:
            if worker_id in self._worker_loads:
                self._worker_loads.pop(worker_id, None)
                self._worker_capabilities.pop(worker_id, None)
                logger.info(f"Worker {worker_id} deregistered.")

    def get_best_worker(self, required_capability: Optional[str] = None) -> Optional[str]:
        """
        Returns the ID of the worker with the lowest load.
        Optionally filters by capability (e.g., needs 'gpu').
        """
        with self._lock:
            candidates = []
            
            for wid, load in self._worker_loads.items():
                if required_capability:
                    caps = self._worker_capabilities.get(wid, [])
                    if required_capability not in caps:
                        continue
                
                candidates.append((load, wid))
            
            if not candidates:
                logger.warning(f"No available workers found for capability: {required_capability}")
                return None
            
            candidates.sort(key=lambda x: x[0])
            
            best_worker_id = candidates[0][1]
            
            self._worker_loads[best_worker_id] += 1
            logger.debug(f"Assigned job to {best_worker_id} (New Load: {self._worker_loads[best_worker_id]})")
            return best_worker_id

    def update_load(self, worker_id: str, delta: int):
        """Called by the Tracker when a job starts (+1) or ends (-1)."""
        with self._lock:
            if worker_id in self._worker_loads:
                new_load = max(0, self._worker_loads[worker_id] + delta)
                self._worker_loads[worker_id] = new_load
                logger.trace(f"Worker {worker_id} load updated to {new_load}")



class PyHiveWatchdog:
    """
    Process Health Monitor.
    
    Monitors 'heartbeats' from worker processes.
    If a worker hangs (deadlock) or crashes, this triggers a restart callback.
    """

    def __init__(self, check_interval: float = 5.0, timeout_threshold: float = 30.0):
        self.check_interval = check_interval
        self.timeout_threshold = timeout_threshold
        
        self._heartbeats: Dict[str, float] = {}
        self._callbacks: Dict[str, Callable] = {} # {worker_id: restart_func}
        self._stop_event = threading.Event()
        self._thread = None

    def start(self):
        """Starts the monitoring thread."""
        if self._thread: return
        
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._monitor_loop, name="PyHiveWatchdog", daemon=True)
        self._thread.start()
        logger.info("Watchdog active.")

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join()
            logger.info("Watchdog stopped.")

    def register_worker(self, worker_id: str, restart_callback: Callable):
        """Starts monitoring a specific worker ID."""
        self._heartbeats[worker_id] = time.time()
        self._callbacks[worker_id] = restart_callback
        logger.debug(f"Watchdog monitoring worker: {worker_id}")

    def beat(self, worker_id: str):
        """Worker calls this to say 'I am alive'."""
        self._heartbeats[worker_id] = time.time()
        # logger.trace(f"Heartbeat received from {worker_id}") # Uncomment for verbose debugging

    def _monitor_loop(self):
        while not self._stop_event.is_set():
            now = time.time()
            dead_workers = []

            # Checking for expired heartbeats
            for wid, last_beat in list(self._heartbeats.items()):
                if now - last_beat > self.timeout_threshold:
                    logger.critical(f"Worker {wid} is unresponsive (Last beat: {now - last_beat:.1f}s ago).")
                    dead_workers.append(wid)

            # Handle restarts outside the loop to avoid dict modification errors ;)
            for wid in dead_workers:
                self._restart_worker(wid)

            time.sleep(self.check_interval)

    def _restart_worker(self, worker_id: str):
        """Executes the restart logic."""
        restart_func = self._callbacks.get(worker_id)
        if restart_func:
            try:
                logger.warning(f"Initiating restart for Worker {worker_id}...")
                
                if worker_id in self._heartbeats:
                    del self._heartbeats[worker_id]
                
                restart_func()
                
                logger.success(f"Restart signal sent for Worker {worker_id}")
                
            except Exception as e:
                logger.error(f"Failed to restart worker {worker_id}: {e}")