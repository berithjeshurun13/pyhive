# pyhive/utils/storage.py
from typing import Optional, Tuple, BinaryIO, Generator, Any, Dict, List, Callable
from .._logging import logger as logging
from pathlib import Path
import hashlib
import shutil
import mimetypes
import sqlite3
import json
import time, os

USECHROMA_DB = False

try :
    import chromadb
    from chromadb.utils import embedding_functions
    from chromadb.config import Settings
except ImportError:
    logging.warning("ChromaDB not found. Vector operations will fail.")
else:
    USECHROMA_DB = True

class PyHiveBlobStorage:
    """
    Production-grade local object storage.
    
    Features:
    - Content-Addressable: Filenames are SHA-256 hashes of content.
    - Deduplication: Identical files are stored only once.
    - Streaming: Handles multi-GB files without loading them into RAM.
    """

    def __init__(self, root_dir: Optional[str] = None):
        if root_dir:
            self.root = Path(root_dir)
        else:
            import os
            base = os.environ.get("PYHIVE_HOME", os.path.expanduser("~/.pyhive"))
            self.root = Path(base) / "data" / "blobs"
        
        self.root.mkdir(parents=True, exist_ok=True)
        self.logger = logging

    def store(self, file_path: str) -> str:
        """
        Ingests a local file into the blob store.
        Returns the 'blob_id' (SHA-256 hash).
        """
        src = Path(file_path)
        if not src.exists():
            raise FileNotFoundError(f"Source file not found: {file_path}")

        sha256 = hashlib.sha256()
        with open(src, "rb") as f:
            while chunk := f.read(8192):
                sha256.update(chunk)
        blob_id = sha256.hexdigest()

        dest = self.root / blob_id
        if dest.exists():
            self.logger.debug(f"Blob {blob_id} already exists. Skipping write.")
            return blob_id

        shutil.copy2(src, dest)
        self.logger.info(f"Stored blob: {blob_id}")
        return blob_id

    def store_bytes(self, data: bytes, extension: str = "") -> str:
        """Stores raw bytes directly."""
        blob_id = hashlib.sha256(data).hexdigest()
        dest = self.root / blob_id
        
        if not dest.exists():
            with open(dest, "wb") as f:
                f.write(data)
        
        return blob_id

    def get_path(self, blob_id: str) -> Optional[Path]:
        """Returns the absolute path to the blob."""
        path = self.root / blob_id
        if not path.exists():
            return None
        return path

    def get_stream(self, blob_id: str) -> Optional[BinaryIO]:
        """Returns a read-only file handle (for streaming to API)."""
        path = self.get_path(blob_id)
        if path:
            return open(path, "rb")
        return None

    def get_metadata(self, blob_id: str) -> dict:
        """Auto-detects file type and size."""
        path = self.get_path(blob_id)
        if not path:
            return {}
        
        mime, _ = mimetypes.guess_type(path)
        return {
            "id": blob_id,
            "size": path.stat().st_size,
            "mime_type": mime or "application/octet-stream",
            "path": str(path)
        }

class PyHiveCache:
    """
    Persistent function result cache using SQLite.
    
    Structure:
    - Key: SHA-256 hash of (Tool Name + Sorted Arguments).
    - Value: JSON serialized result.
    - TTL: Time-To-Live support.
    """

    def __init__(self, db_path: Optional[str] = None):
        if db_path:
            self.db_path = Path(db_path)
        else:
            import os
            base = os.environ.get("PYHIVE_HOME", os.path.expanduser("~/.pyhive"))
            self.db_path = Path(base) / "data" / "cache.db"
            
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        """Creates the table if missing."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS function_cache (
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    created_at REAL,
                    expires_at REAL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_expires ON function_cache(expires_at)")

    def get(self, tool_name: str, args: Dict[str, Any]) -> Optional[Any]:
        """Retrieves a cached result if valid."""
        key = self._generate_key(tool_name, args)
        
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT value, expires_at FROM function_cache WHERE key = ?", 
                (key,)
            )
            row = cursor.fetchone()
            
            if not row:
                return None
            
            value_json, expires_at = row
            
            if expires_at and time.time() > expires_at:
                conn.execute("DELETE FROM function_cache WHERE key = ?", (key,))
                return None
                
            try:
                return json.loads(value_json)
            except json.JSONDecodeError:
                return None

    def set(self, tool_name: str, args: Dict[str, Any], result: Any, ttl: int = 3600):
        """Saves a result to cache."""
        key = self._generate_key(tool_name, args)
        expires_at = time.time() + ttl
        
        try:
            value_json = json.dumps(result)
        except TypeError:
            return

        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO function_cache (key, value, created_at, expires_at)
                VALUES (?, ?, ?, ?)
                """,
                (key, value_json, time.time(), expires_at)
            )

    def _generate_key(self, tool_name: str, args: Dict[str, Any]) -> str:
        """Creates a stable hash key from arguments."""
        serialized = json.dumps(args, sort_keys=True)
        raw = f"{tool_name}:{serialized}"
        return hashlib.sha256(raw.encode()).hexdigest()

    def cleanup(self):
        """Removes expired entries."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM function_cache WHERE expires_at < ?", (time.time(),))

class PyHiveVectorStore:
    """
    Unified interface for Vector Database operations (RAG).
    
    Defaults to ChromaDB (persistent) if available.
    Designed to allow hot-swapping backends without changing tool logic.
    """

    def __init__(self, collection_name: str = "pyhive_knowledge", embedding_function: Optional[Callable] = None, _path_to_embedding_model: Optional[str] = None):
        self.collection_name = collection_name
        self.logger = logging
        self._client = None
        self._collection = None
        self._backend = "none"
        self.__embedding_function = embedding_function or (embedding_functions.DefaultEmbeddingFunction() if USECHROMA_DB else None)
        self.__path_to_embedding_model = _path_to_embedding_model
        self._init_backend()

    def _init_backend(self):
        """Attempts to load a supported vector engine."""
        try:
            
            base = os.environ.get("PYHIVE_HOME", os.path.expanduser("~/.pyhive"))
            persist_path = str(Path(base) / "data" / "vectors")
            if self.__path_to_embedding_model:
                embedding_function = embedding_functions.SentenceTransformerEmbeddingFunction(
                    model_name=self.__path_to_embedding_model
                )
                self._client = chromadb.PersistentClient(path=persist_path)
                self._collection = self._client.get_or_create_collection(name=self.collection_name, embedding_function=embedding_function)
            elif self.__embedding_function :
                self._client = chromadb.PersistentClient(path=persist_path)
                self._collection = self._client.get_or_create_collection(name=self.collection_name, embedding_function=self.__embedding_function)
            else :
                self._client = chromadb.PersistentClient(path=persist_path)
                self._collection = self._client.get_or_create_collection(name=self.collection_name)
            self._backend = "chromadb"
            self.logger.info(f"Initialized VectorStore with ChromaDB at {persist_path}")
            
        except ImportError:
            self.logger.warning("ChromaDB not found. Vector operations will fail.")
            self._backend = "none"

    def add_documents(self, documents: List[str], metadatas: List[Dict], ids: List[str]):
        """Adds text to the vector index."""
        if self._backend == "chromadb":
            self._collection.add(
                documents=documents,
                metadatas=metadatas,
                ids=ids
            )
        else:
            raise NotImplementedError("No Vector DB backend installed. Run 'pip install chromadb'.")

    def query(self, query_text: str, n_results: int = 5) -> List[Dict]:
        """Performs semantic search."""
        if self._backend == "chromadb":
            results = self._collection.query(
                query_texts=[query_text],
                n_results=n_results
            )
            
            parsed_results = []
            if results['ids']:
                for i in range(len(results['ids'][0])):
                    parsed_results.append({
                        "id": results['ids'][0][i],
                        "document": results['documents'][0][i] if results['documents'] else "",
                        "metadata": results['metadatas'][0][i] if results['metadatas'] else {},
                        "distance": results['distances'][0][i] if results['distances'] else 0.0
                    })
            return parsed_results
            
        else:
            raise NotImplementedError("No Vector DB backend installed.")

    def delete_collection(self):
        """Wipes the knowledge base."""
        if self._backend == "chromadb":
            self._client.delete_collection(self.collection_name)