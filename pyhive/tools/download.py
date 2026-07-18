import asyncio
import aiohttp
import os
from functools import wraps

# ==========================================
# Async RPC Decorator
# ==========================================
def async_rpc_method(method_name):
    """
    Decorator that converts a method into a non-blocking JSON-RPC request.
    It utilizes the class's shared aiohttp ClientSession.
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(self, *args, **kwargs):
            # 1. Get parameters
            params = func(self, *args, **kwargs)
            
            # 2. Inject secret token
            if self.secret:
                params.insert(0, f"token:{self.secret}")
                
            # 3. Construct payload
            payload = {
                "jsonrpc": "2.0",
                "id": "async-aria2-client",
                "method": method_name,
                "params": params
            }
            
            # 4. Ensure session exists
            if not self.session:
                return {"status": "error", "message": "ClientSession not started."}
                
            # 5. Execute non-blocking network call
            try:
                async with self.session.post(self.rpc_url, json=payload) as response:
                    data = await response.json()
                    
                    if 'error' in data:
                        return {"status": "error", "message": data['error']['message']}
                    return {"status": "success", "data": data.get('result')}
                    
            except aiohttp.ClientError as e:
                return {"status": "error", "message": f"Network error: {e}"}
            except Exception as e:
                return {"status": "error", "message": f"Unexpected error: {e}"}
                
        return wrapper
    return decorator

# ==========================================
# Async Controller Class
# ==========================================
class AsyncAria2Controller:
    def __init__(self, exe_path="aria2c.exe", port=6800, secret="my_secure_token"):
        self.exe_path = exe_path
        self.port = port
        self.secret = secret
        self.rpc_url = f"http://localhost:{self.port}/jsonrpc"
        
        self.process = None
        self.session = None  # Holds the aiohttp connection pool

    async def start_daemon(self, download_dir="./downloads"):
        """Asynchronously starts the aria2c daemon and HTTP session."""
        if not os.path.exists(download_dir):
            os.makedirs(download_dir)

        command = [
            self.exe_path,
            "--enable-rpc=true",
            "--rpc-listen-all=false",
            f"--rpc-listen-port={self.port}",
            f"--rpc-secret={self.secret}",
            f"--dir={download_dir}",
            "--max-connection-per-server=8",
            "--quiet=true"
        ]

        try:
            # Non-blocking subprocess creation
            self.process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL
            )
            
            # Initialize thread-safe connection pool
            self.session = aiohttp.ClientSession()
            
            # Yield control back to event loop briefly to let daemon spin up
            await asyncio.sleep(1) 
            print(f"[*] Async Aria2 daemon started on port {self.port}")
            return True
            
        except FileNotFoundError:
            print(f"[!] Error: {self.exe_path} not found.")
            return False

    async def close(self):
        """Safely closes the HTTP session and kills the background process."""
        if self.session:
            await self.session.close()
            
        if self.process:
            self.process.terminate()
            await self.process.wait() # Await full shutdown
            print("[*] Aria2 daemon closed gracefully.")

    # ------------------------------------------
    # Async RPC Endpoints
    # ------------------------------------------
    
    @async_rpc_method("aria2.addUri")
    def add_download(self, urls, options=None):
        if isinstance(urls, str): 
            urls = [urls]
        return [urls, options or {}]

    @async_rpc_method("aria2.tellStatus")
    def get_status(self, gid, keys=None):
        if keys:
            return [gid, keys]
        return [gid]
        
    @async_rpc_method("aria2.remove")
    def remove(self, gid):
        return [gid]

# ==========================================
# Example Usage (Concurrent Execution)
# ==========================================
async def main():
    aria2 = AsyncAria2Controller(exe_path="aria2c.exe")
    
    # 1. Start everything up
    if await aria2.start_daemon(download_dir="./async_downloads"):
        
        # 2. Fire off multiple downloads CONCURRENTLY without blocking
        print("--- Adding multiple downloads instantly ---")
        url1 = "https://releases.ubuntu.com/22.04.3/ubuntu-22.04.3-desktop-amd64.iso"
        url2 = "https://cdimage.debian.org/debian-cd/current/amd64/iso-cd/debian-12.5.0-amd64-netinst.iso"
        
        # gather() runs both requests at the exact same time
        task1, task2 = await asyncio.gather(
            aria2.add_download(url1),
            aria2.add_download(url2)
        )
        
        gid1 = task1.get('data')
        gid2 = task2.get('data')
        print(f"Started Ubuntu (GID: {gid1}) and Debian (GID: {gid2})")
        
        # 3. Non-blocking status polling
        print("\n--- Polling Status (Non-blocking) ---")
        for _ in range(3):
            await asyncio.sleep(2) # Does not block other async operations!
            
            status1 = await aria2.get_status(gid1, keys=["status", "downloadSpeed"])
            if status1["status"] == "success":
                speed = int(status1["data"].get("downloadSpeed", 0)) / (1024*1024)
                print(f"Ubuntu Speed: {speed:.2f} MB/s")
                
        # 4. Clean up
        await aria2.remove(gid1)
        await aria2.remove(gid2)
        await aria2.close()

# Run the async event loop
if __name__ == "__main__":
    # If running on Windows, this policy prevents an issue with aiohttp closing
    if os.name == 'nt':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        
    asyncio.run(main())