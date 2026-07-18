# pyhive/utils/bootstrapper.py
import hashlib
import os,time
from .._logging import logger as logging
import requests, platform
from pathlib import Path
from typing import Dict, List, Optional, Callable, Any
import json

from .bootstrapper import PyHiveDependencyCheck, PyHiveAssetDownloader
from .env import PyHiveEnvManager

class PyHiveBootstrapper:
    """
    The 'First Run' Orchestrator.
    
    1. Checks if PYHIVE_HOME is set.
    2. Downloads the 'Asset Manifest' (list of files needed).
    3. Verifies existing files.
    4. Downloads missing/corrupted files.
    5. Configures the environment.
    """
    
    MANIFEST_URL = "https://github.com/berithjeshurun/pyhive/latest_manifest.json" 

    def __init__(self, env_manager: Any):
        self.env_manager = env_manager
        self.downloader = PyHiveAssetDownloader()
        self.logger = logging.getLogger("PyHiveBootstrapper")
        
        self.install_root = self._determine_root()

    def _determine_root(self) -> Path:
        """Decides where to install the 5GB pack."""
        existing = self.env_manager.get("PYHIVE_HOME")
        if existing:
            return Path(existing)
            
        system = platform.system()
        if system == "Windows":
            return Path(os.environ["LOCALAPPDATA"]) / "PyHive"
        elif system == "Darwin":
            return Path.home() / "Library" / "Application Support" / "PyHive"
        else:
            return Path.home() / ".pyhive"

    def run(self, force: bool = False):
        """
        Main execution method.
        Returns True if environment is ready.
        """
        self.logger.info(f"Initializing PyHive Environment at: {self.install_root}")
        self.install_root.mkdir(parents=True, exist_ok=True)

        manifest = self._fetch_manifest()
        if not manifest:
            return False

        checker = PyHiveDependencyCheck(manifest["files"])
        missing_files = checker.verify_all(self.install_root)
        
        if not missing_files:
            self.logger.info("Environment is healthy. No downloads needed.")
            self._finalize_env()
            return True

        self.logger.info(f"Found {len(missing_files)} missing or corrupted files. Starting download...")

        base_url = manifest["base_url"]
        for rel_path in missing_files:
            url = f"{base_url}/{rel_path}"
            dest = self.install_root / rel_path
            
            self.logger.info(f"Downloading: {rel_path}")
            success = self.downloader.download(
                url, dest, 
                progress_callback=self._console_progress
            )
            
            if not success:
                self.logger.critical(f"Failed to download required asset: {rel_path}")
                return False

        if checker.verify_all(self.install_root):
            self.logger.info("Installation verification passed.")
            self._finalize_env()
            return True
        else:
            self.logger.error("Verification failed after download. Disk error?")
            return False

    def _fetch_manifest(self) -> Dict[str, Any]:
        """Downloads the JSON list of required files."""
        try:
            with requests.get(self.MANIFEST_URL, timeout=10) as r:
                r.raise_for_status()
                return r.json()
        except Exception as e:
            self.logger.error(f"Could not fetch asset manifest: {e}")
            return {}

    def _finalize_env(self):
        """Persists the PYHIVE_HOME variable."""
        self.env_manager.persist("PYHIVE_HOME", str(self.install_root))
        self.logger.info("PyHive environment variable set successfully.")

    @staticmethod
    def _console_progress(current: int, total: int):
        """Simple CLI progress bar."""
        percent = (current / total) * 100
        print(f"\rProgress: [{current//1024//1024}MB / {total//1024//1024}MB] {percent:.1f}%", end="")


class PyHiveAssetDownloader:
    """
    Robust download engine for massive files (models, binaries).
    
    Features:
    - Resumable Downloads: Uses HTTP Range headers to continue interrupted downloads.
    - Progress Callbacks: Feeds data to CLI progress bars or GUI widgets.
    - Timeout Handling: Retries automatically on flaky connections.
    """

    def __init__(self, chunk_size: int = 8192, retries: int = 3):
        self.chunk_size = chunk_size
        self.retries = retries
        self.logger = logging.getLogger("PyHiveDownloader")

    def download(
        self, 
        url: str, 
        dest_path: Path, 
        expected_size: Optional[int] = None,
        progress_callback: Optional[Callable[[int, int], None]] = None
    ) -> bool:
        """
        Downloads a file with resume capability.
        
        Args:
            url: Source URL.
            dest_path: Local destination.
            expected_size: Total bytes (optional, for validation).
            progress_callback: Func(current_bytes, total_bytes).
        """
        dest_path = Path(dest_path)
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        
        existing_size = 0
        if dest_path.exists():
            existing_size = dest_path.stat().st_size
            if expected_size and existing_size == expected_size:
                self.logger.info(f"File {dest_path.name} already exists and matches size.")
                return True

        headers = {}
        if existing_size > 0:
            headers["Range"] = f"bytes={existing_size}-"
            self.logger.info(f"Resuming {dest_path.name} from {existing_size} bytes...")

        for attempt in range(self.retries):
            try:
                with requests.get(url, headers=headers, stream=True, timeout=30) as r:
                    r.raise_for_status()
                    
                    if existing_size > 0 and r.status_code == 200:
                        self.logger.warning("Server does not support resume. Restarting download.")
                        existing_size = 0
                        mode = "wb"
                    else:
                        mode = "ab" # Append mode for resume

                    total_size = int(r.headers.get('content-length', 0)) + existing_size
                    
                    with open(dest_path, mode) as f:
                        downloaded = existing_size
                        for chunk in r.iter_content(chunk_size=self.chunk_size):
                            if chunk:
                                f.write(chunk)
                                downloaded += len(chunk)
                                if progress_callback:
                                    progress_callback(downloaded, total_size)
                
                return True
                
            except (requests.exceptions.RequestException, OSError) as e:
                self.logger.warning(f"Download attempt {attempt+1}/{self.retries} failed: {e}")
                time.sleep(2)

        self.logger.error(f"Failed to download {url} after {self.retries} attempts.")
        return False


class PyHiveDependencyCheck:
    """
    Production-grade integrity verifier.
    
    Ensures that external assets (Tesseract, LLM weights, Vector DBs) 
    match their expected SHA-256 signatures before the app starts.
    """

    def __init__(self, asset_map: Dict[str, str]):
        """
        Args:
            asset_map: Dictionary of { 'relative/path/to/file': 'sha256_hash' }
        """
        self.asset_map = asset_map
        self.logger = logging.getLogger("PyHiveDependencyCheck")

    def verify_all(self, base_path: Path) -> List[str]:
        """
        Checks all assets in the map.
        Returns a list of missing or corrupted filenames.
        """
        missing_or_corrupted = []
        
        for rel_path, expected_hash in self.asset_map.items():
            full_path = base_path / rel_path
            
            if not full_path.exists():
                self.logger.warning(f"Missing asset: {rel_path}")
                missing_or_corrupted.append(rel_path)
                continue
            
            if not self._verify_file(full_path, expected_hash):
                self.logger.error(f"Hash mismatch for: {rel_path}")
                missing_or_corrupted.append(rel_path)
            else:
                self.logger.debug(f"Verified: {rel_path}")

        return missing_or_corrupted

    def _verify_file(self, path: Path, expected_hash: str) -> bool:
        """
        Calculates SHA-256 of a file in 64KB chunks to keep RAM usage low.
        """
        sha256 = hashlib.sha256()
        try:
            with open(path, "rb") as f:
                while True:
                    data = f.read(65536) # 64KB chunk
                    if not data:
                        break
                    sha256.update(data)
            
            calculated_hash = sha256.hexdigest()
            return calculated_hash == expected_hash
        except OSError as e:
            self.logger.error(f"Could not read {path}: {e}")
            return False