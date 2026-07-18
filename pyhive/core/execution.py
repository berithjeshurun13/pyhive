# pyhive/core/execution.py

import time
import uuid
import threading
import queue
import traceback
from enum import Enum
from typing import Any, Dict, Optional, Callable
from pydantic import BaseModel, Field
from loguru import logger

from .base import PyHiveResponse
from .context import PyHiveContext
from .registry import PyHiveRegistry


class PyHiveState(str, Enum):
    """
    Standard lifecycle states for a job.
    """
    PENDING = "PENDING"     # In queue, waiting for worker
    RUNNING = "RUNNING"     # Worker has picked it up
    COMPLETED = "COMPLETED" # Finished successfully
    FAILED = "FAILED"       # Crashed or raised exception
    CANCELLED = "CANCELLED" # User stopped it


class PyHiveJob(BaseModel):
    """
    Represents a single unit of work.
    Serializable for transport over Redis/Sockets.
    """
    job_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    tool_name: str
    arguments: Dict[str, Any]
    user_id: str = "anonymous"
    priority: int = 10  # Lower is higher priority (1 = Urgent, 100 = Background)
    
    status: PyHiveState = PyHiveState.PENDING
    result: Optional[Any] = None
    error: Optional[str] = None
    
    created_at: float = Field(default_factory=time.time)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    
    context_data: Optional[Dict[str, Any]] = None

    def mark_running(self):
        self.status = PyHiveState.RUNNING
        self.started_at = time.time()

    def mark_complete(self, result: Any):
        self.status = PyHiveState.COMPLETED
        self.result = result
        self.completed_at = time.time()

    def mark_failed(self, error_msg: str):
        self.status = PyHiveState.FAILED
        self.error = error_msg
        self.completed_at = time.time()


class PyHiveQueue:
    """
    Thread-safe Priority Queue.
    
    In a distributed deployment, this class would wrap Redis or RabbitMQ.
    For the standalone engine, it uses Python's native PriorityQueue.
    """
    def __init__(self):
        self._queue = queue.PriorityQueue()
        self._lock = threading.RLock()

    def push(self, job: PyHiveJob):
        """Add a job to the queue."""
        self._queue.put((job.priority, job.created_at, job))
        logger.debug(f"Job {job.job_id} pushed to queue (Priority: {job.priority})")

    def pop(self, timeout: float = 1.0) -> Optional[PyHiveJob]:
        """
        Retrieves the next highest priority job.
        Blocks for 'timeout' seconds if empty.
        """
        try:
            _, _, job = self._queue.get(timeout=timeout)
            return job
        except queue.Empty:
            return None

    def size(self) -> int:
        return self._queue.qsize()

class PyHiveTracker:
    """
    The 'Database' of active jobs.
    Allows the API/CLI to query 'Is job X done yet?'
    """
    def __init__(self):
        self._jobs: Dict[str, PyHiveJob] = {}
        self._lock = threading.RLock()

    def register_job(self, job: PyHiveJob):
        """Starts tracking a new job."""
        with self._lock:
            self._jobs[job.job_id] = job

    def get_job(self, job_id: str) -> Optional[PyHiveJob]:
        """Retrieves job status safely."""
        with self._lock:
            return self._jobs.get(job_id)

    def update_job(self, job_id: str, status: PyHiveState, result: Any = None, error: str = None):
        """Updates the status of a job."""
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            
            if status == PyHiveState.RUNNING:
                job.mark_running()
            elif status == PyHiveState.COMPLETED:
                job.mark_complete(result)
            elif status == PyHiveState.FAILED:
                job.mark_failed(error)
            elif status == PyHiveState.CANCELLED:
                job.status = PyHiveState.CANCELLED


class PyHiveWorker(threading.Thread):
    """
    Background Worker Thread.
    
    Lifecycle:
    1. Pops Job from Queue.
    2. Hydrates Context (User ID, DB connections).
    3. Finds Tool in Registry.
    4. Executes Tool (Sandboxed).
    5. Updates Tracker with result.
    """

    def __init__(self, 
                 worker_id: str, 
                 queue: PyHiveQueue, 
                 tracker: PyHiveTracker, 
                 registry: Any, # PyHiveRegistry
                 context_factory: Callable[[PyHiveJob], Any]): # Returns PyHiveContext
        
        super().__init__(name=f"PyHiveWorker-{worker_id}", daemon=True)
        self.worker_id = worker_id
        self.queue = queue
        self.tracker = tracker
        self.registry = registry
        self.context_factory = context_factory
        self._stop_event = threading.Event()

    def run(self):
        logger.info(f"Worker {self.worker_id} started.")
        
        while not self._stop_event.is_set():
            job = self.queue.pop(timeout=2.0)
            if not job:
                continue

            self.tracker.update_job(job.job_id, PyHiveState.RUNNING)
            logger.info(f"Worker {self.worker_id} executing {job.tool_name} ({job.job_id})")

            try:
                tool = self.registry.get_tool(job.tool_name)
                context = self.context_factory(job)
                response = tool.execute(**job.arguments)

                if response.success:
                    self.tracker.update_job(job.job_id, PyHiveState.COMPLETED, result=response.data)
                    logger.success(f"Job {job.job_id} completed successfully.")
                else:
                    self.tracker.update_job(job.job_id, PyHiveState.FAILED, error=response.error)
                    logger.error(f"Job {job.job_id} failed logic: {response.error}")

            except Exception as e:
                error_msg = f"Worker Crash: {str(e)}"
                logger.exception(f"Critical error in job {job.job_id}")
                self.tracker.update_job(job.job_id, PyHiveState.FAILED, error=error_msg)

    def stop(self):
        """Graceful shutdown signal."""
        self._stop_event.set()