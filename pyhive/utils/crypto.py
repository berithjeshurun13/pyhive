# pyhive/utils/crypto.py
import hmac
import hashlib
import secrets
import base64
import json
import time
import platform
import os
from typing import Dict, Optional, Any, Union, List
from dataclasses import dataclass
from .._logging import logger as logging

try:
    from cryptography.fernet import Fernet, InvalidToken
except ImportError:
    raise ImportError("PyHivePayloadEncryptor requires 'cryptography'. Run: pip install cryptography")


class PyHiveTokenError(Exception):
    """Base exception for token validation failures."""
    pass

class TokenExpiredError(PyHiveTokenError):
    """Raised when a token is mathematically valid but past its expiration."""
    pass

class TokenTamperedError(PyHiveTokenError):
    """Raised when the signature does not match the payload."""
    pass

@dataclass
class TokenPayload:
    """Standardized payload structure for PyHive tokens."""
    job_id: str
    user_id: str
    scope: str  # e.g., 'read', 'write', 'admin'
    exp: float

class PyHiveToken:
    """
    The cryptography engine for PyHive.
    
    Generates and validates URL-safe, signed tokens.
    Uses HMAC-SHA256 for signing to ensure tokens cannot be forged.


    >>> from pyhive.utils.crypto import PyHiveToken
    >>> 
    >>> token_engine = PyHiveToken() 
    >>>  
    >>> # 1. Backend starts a job
    >>> job_id = token_engine.generate_job_id()
    >>>
    >>> # 2. Backend grants frontend access to listen to this job
    >>> access_token = token_engine.sign({
    >>>      "job_id": job_id,
    >>>      "user_id": "user_123",
    >>>      "scope": "read_only"
    >>> })
    # 
    >>> # 3. Frontend connects to WebSocket with `access_token`
    >>> # 4. Broker validates it:
    >>> try:
    >>>     data = token_engine.validate(access_token)
    >>>     print(f"Allowed to listen to {data['job_id']}")
    >>> except PyHiveTokenError as e:
    #     print(f"Access Denied: {e}")
    """

    def __init__(self, secret_key: Optional[str] = None):
        self._secret_key = secret_key or os.environ.get("PYHIVE_SECRET_KEY")
        
        if not self._secret_key:
            self._secret_key = secrets.token_hex(32)
            
        self._key_bytes = self._secret_key.encode('utf-8')

    def generate_job_id(self, prefix: str = "job") -> str:
        """
        Generates a high-entropy, collision-resistant ID for a new job.
        Format: {prefix}-{timestamp}-{random_hex}
        Example: job-1709324000-a1b2c3d4
        """
        timestamp = int(time.time())
        random_part = secrets.token_hex(6)
        return f"{prefix}-{timestamp}-{random_part}"

    def sign(self, payload: Dict[str, Any], expires_in: int = 3600) -> str:
        """
        Creates a signed, URL-safe token containing the payload.
        
        Args:
            payload: Dictionary of data to embed (job_id, user_id, etc.)
            expires_in: Seconds until expiration (default 1 hour)
            
        Returns:
            String: "header.payload.signature" (Base64URL encoded)
        """
        if "exp" not in payload:
            payload["exp"] = time.time() + expires_in

        json_payload = json.dumps(payload, separators=(',', ':'))
        
        encoded_payload = self._base64url_encode(json_payload.encode('utf-8'))
        
        signature = self._create_signature(encoded_payload)
        
        return f"{encoded_payload.decode('utf-8')}.{signature}"

    def validate(self, token: str) -> Dict[str, Any]:
        """
        Validates a token's signature and expiration.
        
        Returns:
            Dict: The original payload if valid.
            
        Raises:
            TokenTamperedError: If signature is invalid.
            TokenExpiredError: If token has expired.
            PyHiveTokenError: Malformed token.
        """
        try:
            encoded_payload, received_signature = token.rsplit('.', 1)
        except ValueError:
            raise PyHiveTokenError("Malformed token format.")

        expected_signature = self._create_signature(encoded_payload.encode('utf-8'))
        
        if not hmac.compare_digest(expected_signature, received_signature):
            raise TokenTamperedError("Invalid token signature. Data may have been tampered with.")

        try:
            json_payload = self._base64url_decode(encoded_payload).decode('utf-8')
            payload = json.loads(json_payload)
        except Exception:
            raise PyHiveTokenError("Failed to decode token payload.")

        if "exp" in payload and payload["exp"] < time.time():
            raise TokenExpiredError(f"Token expired at {payload['exp']}")

        return payload

    def _create_signature(self, data: bytes) -> str:
        """Internal method to generate HMAC-SHA256 signature."""
        return self._base64url_encode(
            hmac.new(self._key_bytes, data, hashlib.sha256).digest()
        ).decode('utf-8')

    @staticmethod
    def _base64url_encode(data: bytes) -> bytes:
        """Standard Base64URL encode without padding."""
        return base64.urlsafe_b64encode(data).rstrip(b'=')

    @staticmethod
    def _base64url_decode(data: str) -> bytes:
        """Standard Base64URL decode with padding restoration."""
        padding = 4 - (len(data) % 4)
        if padding != 4:
            data += '=' * padding
        return base64.urlsafe_b64decode(data)


class PyHiveKeyManager(object):
    """
    Securely manages external API keys using the OS Credential Store.
    
    Priority Order:
    1. In-Memory Cache (Runtime only)
    2. Environment Variables (PYHIVE_{SERVICE}_KEY)
    3. OS Keyring (Windows Credential Locker / macOS Keychain)
    4. Local Encrypted File (Fallback)
    """

    def __init__(self, use_os_store: bool = True):
        super().__init__()
        self._cache: Dict[str, str] = {}
        self._use_os_store = use_os_store
        self._keyring_available = False
        self._keyring_lib = None
        
        self._encryptor = None

        if self._use_os_store:
            try:
                import keyring
                self._keyring_lib = keyring
                self._keyring_available = True
            except ImportError:
                logging.warning("PyHiveKeyManager: 'keyring' library not found. Falling back to local file storage.")

        # Windows: %LOCALAPPDATA%\PyHive\secrets.store
        # Linux/Mac: ~/.pyhive/secrets.store
        if platform.system() == "Windows":
            base_dir = os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))
        else:
            base_dir = os.path.expanduser("~")
            
        self._storage_dir = os.path.join(base_dir, ".pyhive")
        self._storage_file = os.path.join(self._storage_dir, "secrets.store")

        if not os.path.exists(self._storage_dir):
            try:
                os.makedirs(self._storage_dir, exist_ok=True)
            except OSError as e:
                logging.error(f"PyHiveKeyManager: Could not create storage directory: {e}")

    def _get_encryptor(self):
        """Lazy loader for the payload encryptor to avoid circular imports."""
        if self._encryptor:
            return self._encryptor
            
        try:
            from .crypto import PyHivePayloadEncryptor
            self._encryptor = PyHivePayloadEncryptor()
        except ImportError:
            logging.warning("PyHiveKeyManager: Encryption library missing. File storage will use basic obfuscation.")
            self._encryptor = None
        except Exception as e:
            logging.error(f"PyHiveKeyManager: Failed to init encryptor: {e}")
            self._encryptor = None
            
        return self._encryptor

    def set_key(self, service: str, api_key: str) -> bool:
        """
        Securely stores an API key for a specific service.
        Example: manager.set_key("openai", "sk-...")
        """
        service = service.lower().strip()
        
        self._cache[service] = api_key
        
        if self._keyring_available:
            try:
                self._keyring_lib.set_password("pyhive", service, api_key)
                return True
            except Exception as e:
                logging.error(f"PyHiveKeyManager: OS Keyring set failed: {e}")

        return self._save_to_file(service, api_key)

    def get_key(self, service: str) -> Optional[str]:
        """
        Retrieves a key using the tiered priority system.
        """
        service = service.lower().strip()

        if service in self._cache:
            return self._cache[service]

        env_var_name = f"PYHIVE_{service.upper()}_KEY"
        if env_var_name in os.environ:
            key = os.environ[env_var_name]
            self._cache[service] = key
            return key

        if self._keyring_available:
            try:
                key = self._keyring_lib.get_password("pyhive", service)
                if key:
                    self._cache[service] = key
                    return key
            except Exception as e:
                logging.debug(f"PyHiveKeyManager: OS Keyring lookup failed: {e}")

        file_key = self._load_from_file(service)
        if file_key:
            self._cache[service] = file_key
            return file_key

        return None

    def delete_key(self, service: str) -> bool:
        """Removes a key from all storage layers."""
        service = service.lower().strip()
        
        if service in self._cache:
            del self._cache[service]

        if self._keyring_available:
            try:
                self._keyring_lib.delete_password("pyhive", service)
            except Exception:
                pass

        return self._remove_from_file(service)

    def _save_to_file(self, service: str, key: str) -> bool:
        """Saves key to a local JSON file using PyHivePayloadEncryptor."""
        data = self._read_file_safe()
        encryptor = self._get_encryptor()
        
        if encryptor:
            try:
                encrypted_token = encryptor.encrypt(key)
                data[service] = "ENC:" + encrypted_token
            except Exception as e:
                logging.error(f"PyHiveKeyManager: Encryption failed: {e}. Falling back to Base64.")
                data[service] = "B64:" + base64.b64encode(key.encode()).decode()
        else:
            data[service] = "B64:" + base64.b64encode(key.encode()).decode()
        
        try:
            with open(self._storage_file, 'w') as f:
                json.dump(data, f)
            return True
        except IOError as e:
            logging.error(f"PyHiveKeyManager: File write failed: {e}")
            return False

    def _load_from_file(self, service: str) -> Optional[str]:
        """Loads and decrypts key from local file."""
        data = self._read_file_safe()
        
        if service not in data:
            return None
            
        stored_value = data[service]
        
        if stored_value.startswith("ENC:"):
            encryptor = self._get_encryptor()
            if not encryptor:
                logging.error("PyHiveKeyManager: Found encrypted key but encryption lib is missing.")
                return None
            try:
                token = stored_value[4:] # Strip "ENC:"
                result = encryptor.decrypt(token, as_dict=False)
                return result if isinstance(result, str) else result.decode()
            except Exception as e:
                logging.error(f"PyHiveKeyManager: Decryption failed for {service}: {e}")
                return None

        elif stored_value.startswith("B64:"):
            try:
                return base64.b64decode(stored_value[4:]).decode()
            except Exception:
                return None

        else:
            try:
                return base64.b64decode(stored_value).decode()
            except Exception:
                return None

    def _remove_from_file(self, service: str) -> bool:
        """Removes key from local file."""
        data = self._read_file_safe()
        if service in data:
            del data[service]
            try:
                with open(self._storage_file, 'w') as f:
                    json.dump(data, f)
                return True
            except IOError:
                return False
        return True

    def _read_file_safe(self) -> Dict:
        """Helper to read the JSON file safely."""
        if not os.path.exists(self._storage_file):
            return {}
        try:
            with open(self._storage_file, 'r') as f:
                return json.load(f)
        except (IOError, json.JSONDecodeError):
            return {}


class PyHivePayloadEncryptor(object):
    """
    Symmetric encryption engine for securing payloads between PyHive components.
    
    Uses Fernet (AES-128 in CBC mode with PKCS7 padding + HMAC-SHA256).
    This ensures both CONFIDENTIALITY (cannot be read) and INTEGRITY (cannot be altered).


    >>> key = PyHivePayloadEncryptor.generate_new_key()
    >>> save_to_file("~/.pyhive/encryption.key", key)
    >>> # 2. Worker sending sensitive PDF data:
    >>> encryptor = PyHivePayloadEncryptor(key=key)
    >>> secure_payload = encryptor.encrypt({
    >>>     "status": "success",
    >>>     "extracted_data": "CONFIDENTIAL BANK STATEMENT..."
    >>> })
    >>> socket.emit("job_complete", secure_payload)
    >>> # 3. Frontend / Broker receiving:
    >>> # (Frontend needs the key injected or Broker decrypts before forwarding to authorized room)
    >>> data = encryptor.decrypt(secure_payload)

    """

    def __init__(self, key: Optional[Union[str, bytes]] = None):
        super().__init__()
        
        self._key = key
        
        if not self._key:
            self._key = os.environ.get("PYHIVE_ENCRYPTION_KEY")
            
        if not self._key:
            secret_path = os.path.join(os.path.expanduser("~"), ".pyhive", "encryption.key")
            if os.path.exists(secret_path):
                with open(secret_path, "rb") as f:
                    self._key = f.read().strip()

        if not self._key:
            print("[WARNING] PyHivePayloadEncryptor: No key found. Using transient key (Data lost on restart).")
            self._key = Fernet.generate_key()

        if isinstance(self._key, str):
            self._key = self._key.encode('utf-8')
            
        try:
            self._engine = Fernet(self._key)
        except ValueError:
            raise ValueError("Invalid Key Format. PyHive Encryption Key must be 32 url-safe base64-encoded bytes.")

    def encrypt(self, payload: Union[Dict, str, bytes]) -> str:
        """
        Encrypts a dictionary, string, or bytes into a URL-safe token.
        
        Args:
            payload: The data to protect (e.g., {'pdf_id': 123, 'ssn': '...'})
            
        Returns:
            str: The encrypted Fernet token (safe for WebSocket transmission)
        """
        if isinstance(payload, dict):
            data_bytes = json.dumps(payload, sort_keys=True).encode('utf-8')
        elif isinstance(payload, str):
            data_bytes = payload.encode('utf-8')
        elif isinstance(payload, bytes):
            data_bytes = payload
        else:
            raise TypeError("Payload must be Dict, Str, or Bytes.")

        encrypted_bytes = self._engine.encrypt(data_bytes)
        
        return encrypted_bytes.decode('utf-8')

    def decrypt(self, token: Union[str, bytes], as_dict: bool = True) -> Union[Dict, str, bytes]:
        """
        Decrypts a token back into raw data.
        
        Args:
            token: The encrypted string.
            as_dict: If True, attempts to parse result as JSON dictionary.
            
        Returns:
            The decrypted data.
            
        Raises:
            InvalidToken: If the key is wrong or data was tampered with.
        """
        if isinstance(token, str):
            token = token.encode('utf-8')

        try:
            decrypted_bytes = self._engine.decrypt(token)
            
            if as_dict:
                try:
                    return json.loads(decrypted_bytes.decode('utf-8'))
                except json.JSONDecodeError:
                    return decrypted_bytes.decode('utf-8')
            
            return decrypted_bytes
            
        except InvalidToken:
            raise PermissionError("Decryption Failed: Invalid Token or Signature Mismatch.")

    @staticmethod
    def generate_new_key() -> str:
        """Helper to generate a valid key for the Bootstrapper to save."""
        return Fernet.generate_key().decode('utf-8')


class PyHiveAuthenticator(object):
    """
    Middleware layer that enforces Authentication (Who are you?) 
    and Authorization (What are you allowed to do?).
    
    It unifies validation for both ephemeral User Tokens (Frontend) 
    and persistent API Keys (Scripts/CLI).

    >>> def execute_tool(tool_name, args, token):
    >>>     # 1. Authenticate
    >>>     authenticator = PyHiveAuthenticator(token_engine)
    >>>     user_identity = authenticator.authenticate(token)
    >>>     # 2. Check Registration for required scopes
    >>>     tool_def = registry.get_tool(tool_name)
    >>>     required_scopes = tool_def.scopes  # e.g., ["filesystem_write"]
    >>>     # 3. Authorize
    >>>     authenticator.check_permissions(user_identity, required_scopes)
    >>>     # 4. Run
    >>>     return tool_def.run(args)
    """

    def __init__(self, token_engine: Any, key_manager: Optional[Any] = None):
        """
        Args:
            token_engine: Instance of PyHiveToken for validating signed payloads.
            key_manager: Instance of PyHiveKeyManager for validating static API keys.
        """
        super().__init__()
        self.token_engine = token_engine
        self.key_manager = key_manager
        
        # Default policy: Deny everything unless explicitly allowed
        self._strict_mode = True 

    def authenticate(self, credential: str, credential_type: str = "token") -> Dict[str, Any]:
        """
        Validates the provided credential and returns the User Context.
        
        Args:
            credential: The raw token string or API key.
            credential_type: 'token' (for Frontend/Socket) or 'api_key' (for CLI/Scripts).
            
        Returns:
            Dict: The 'Identity Context' containing user_id, scopes, and role.
            
        Raises:
            PermissionError: If authentication fails.
        """
        if not credential:
            raise PermissionError("Authentication Failed: No credential provided.")

        if credential_type == "token":
            try:
                payload = self.token_engine.validate(credential)
                
                return {
                    "user_id": payload.get("user_id"),
                    "job_id": payload.get("job_id"),
                    "scopes": payload.get("scope", "").split(","), # "read,write" -> ["read", "write"]
                    "type": "session"
                }
            except Exception as e:
                raise PermissionError(f"Authentication Failed: Invalid Session Token. {str(e)}")

        elif credential_type == "api_key":
            if not self.key_manager:
                raise PermissionError("API Key authentication is disabled on this node.")
                
            service_id = self.key_manager.validate_api_key(credential) 
            
            if service_id:
                return {
                    "user_id": f"service:{service_id}",
                    "scopes": ["*"], # API Keys usually get Super Admin (or we fetch from DB)
                    "type": "service"
                }
            else:
                raise PermissionError("Authentication Failed: Invalid API Key.")

        else:
            raise ValueError("Unsupported credential type.")

    def check_permissions(self, user_context: Dict[str, Any], required_scopes: List[str]) -> bool:
        """
        Verifies if the authenticated user has the specific rights to run a tool.
        
        Args:
            user_context: The dict returned by authenticate().
            required_scopes: List of scopes required by the tool (e.g. ['filesystem_write']).
            
        Returns:
            True if authorized.
            
        Raises:
            PermissionError: If the user lacks necessary scopes.
        """
        if not required_scopes:
            return True # Public tool, no restrictions

        user_scopes = user_context.get("scopes", [])

        if "*" in user_scopes or "admin" in user_scopes:
            return True

        missing = [scope for scope in required_scopes if scope not in user_scopes]
        
        if missing:
            user_id = user_context.get("user_id", "unknown")
            logging.warning(f"Access Denied for {user_id}. Missing scopes: {missing}")
            raise PermissionError(f"Authorization Failed. You lack permissions: {', '.join(missing)}")

        return True
