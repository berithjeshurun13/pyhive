# PyHive

**PyHive** is a Python framework for building LLM tool-calling agents and orchestration pipelines. It wraps callable Python functions as typed tools, exposes them as OpenAI/Gemini-compatible JSON schemas, and runs them through queues, chains, DAGs, or ReAct-style agents — with middleware, RBAC, and local storage built in.

( Currently in developmetal phase )

Originally oriented around Ollama-style local LLM workflows, PyHive has grown into a general-purpose **tool registry + agent runtime**.

---

## Features

| Area | What you get |
|------|----------------|
| **Tools** | Wrap any `def` / `async def` as a `PyHiveTool` with Pydantic validation and auto JSON Schema |
| **Registry** | Thread-safe registration, modules, scopes, and LLM function definitions |
| **Agents** | ReAct loop (`PyHiveAgent`) with short-term memory |
| **Workflows** | Sequential chains (`PyHiveChain`) and parallel DAGs (`PyHiveDAG`) |
| **Execution** | Priority job queue, background workers, and job tracking |
| **Security** | Path sanitizer, rate limiter, RBAC scopes, AST sandbox, timeouts |
| **Runtime** | Config via `PYHIVE_*` env vars, blob/vector storage, plugins, bootstrap assets |
| **Built-ins** | Web scrape/search, RSS, OCR (Tesseract), downloads (Aria2), audio waveforms, networking |

---

## Requirements

- Python 3.10+
- Core deps: `pydantic`, `pydantic-settings`, `loguru`
- Optional / tool-specific: `httpx`, `beautifulsoup4`, `wikipedia-api`, `pytesseract`, `Pillow`, `chromadb`, `cryptography`, `msgpack`, `aiohttp`, `opencv-python`, `numpy`, `python-socketio`, `requests`

---

## Quick start

### Register and run a tool

```python
from pyhive.core.registry import PyHiveRegistry
from pyhive.core.context import PyHiveContext

registry = PyHiveRegistry()

@registry.register(name="add", module="math", description="Add two numbers")
def add(a: int, b: int) -> int:
    """Add two integers.

    Args:
        a: First number
        b: Second number
    """
    return a + b

tool = registry.get_tool("add")
response = tool.execute(a=2, b=3)

assert response.success
print(response.data)  # 5

# Schemas ready for OpenAI / Gemini / Ollama tool calling
print(registry.get_llm_definitions())
```

### Run a ReAct agent

```python
from pyhive.core.workflows import PyHiveAgent
from pyhive.core.context import PyHiveContext

def my_llm(messages, tools):
    # Return OpenAI-style dict: {"content": "...", "tool_calls": [...]}
    ...

agent = PyHiveAgent(
    name="assistant",
    registry=registry,
    llm_callable=my_llm,
    system_prompt="You are a helpful assistant. Use tools when needed.",
    max_steps=10,
)

ctx = PyHiveContext(job_id="job-1", user_id="user-1")
answer = agent.run("What is 2 + 3?", context=ctx)
```

### Chain tools

```python
from pyhive.core.workflows import PyHiveChain

chain = PyHiveChain(registry)
chain.add_step("fetch_url", input_map={"url": "$input.url"})
chain.add_step("summarize", input_map={"text": "$prev"})

result = chain.execute({"url": "https://example.com"}, context=ctx)
```

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        Your App / CLI / API                   │
└────────────────────────────┬────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────┐
│  Middleware (sanitize, rate-limit)  →  Policies (RBAC)       │
└────────────────────────────┬────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────┐
│  Registry  ←→  Tools (Pydantic + JSON Schema)                │
└──────┬───────────────┬──────────────────┬───────────────────┘
       │               │                  │
       ▼               ▼                  ▼
  PyHiveAgent     PyHiveChain        PyHiveDAG
  (ReAct)         (pipeline)         (parallel)
       │               │                  │
       └───────────────┼──────────────────┘
                       ▼
              Queue / Worker / Tracker
                       │
                       ▼
         Context (config, storage, emitter)
```

### Core concepts

| Type | Role |
|------|------|
| `PyHiveTool` | Callable wrapper: validates args, runs sync/async, returns `PyHiveResponse` |
| `PyHiveRegistry` | Central catalog of tools; builds LLM definitions |
| `PyHiveContext` | Per-job identity + injected services (`db`, `storage`, `vectors`, `emitter`) |
| `PyHiveConfig` | Settings from env (`PYHIVE_HOME`, broker, workers, default model, …) |
| `PyHiveJob` / `PyHiveWorker` | Queued execution unit and background consumer |
| `PyHiveAgent` | LLM → tool → observe loop until a final answer |
| `PyHiveEmitter` / `PyHiveRoom` | Stream progress/logs to subscribers (local or broker) |

---

## Package layout

```
pyhive/
├── __init__.py
├── assembler.py          # High-level assembly helpers
├── ens.py                # Thread-safe external binary discovery
├── wrappers.py           # Cache / decorator utilities (optional)
├── _logging.py           # Loguru logger + process updater (Socket.IO)
│
├── core/
│   ├── base.py           # PyHiveTool, PyHiveResponse, exceptions
│   ├── tool.py           # Alternate / lean tool wrapper
│   ├── registry.py       # Tool & module registration
│   ├── context.py        # PyHiveConfig, PyHiveContext
│   ├── execution.py      # Jobs, queue, tracker, workers
│   ├── workflows.py      # Memory, Chain, DAG, Agent
│   ├── middleware.py     # Sanitizer, rate limiter, pipeline
│   ├── policies.py       # RBAC, sandbox, timeouts
│   ├── parser.py         # Docstring → JSON Schema for LLMs
│   ├── broker_client.py  # Emitter & room pub/sub
│   └── service.py        # .rnv runtime manager (msgpack)
│
├── tools/
│   ├── ocr.py            # Tesseract OCR helpers
│   ├── download.py       # Async Aria2 controller
│   ├── audiowaveform.py  # Async waveform generation
│   └── builtins/
│       ├── web.py        # HTML clean, DuckDuckGo / Wikipedia helpers
│       ├── rss.py        # RSS/XML read & write
│       ├── net.py        # Connections & port checks
│       ├── image.py      # OpenCV image utilities
│       └── mario.py      # Procedural noise (Perlin, FBM, Worley, …)
│
├── utils/
│   ├── bootstrapper.py   # First-run asset download & verify
│   ├── env.py            # PYHIVE_HOME path resolver
│   ├── storage.py        # Content-addressable blobs (+ optional Chroma)
│   ├── crypto.py         # HMAC tokens & payload encryption
│   ├── plugins.py        # Plugin manifests, hot reload
│   ├── scaling.py        # Scaling helpers
│   └── data.py           # Data utilities
│
└── internals/            # Internal link / web helpers
```

---

## Configuration

Settings load from environment variables (and optional `.env`) via `PyHiveConfig`:

| Variable | Default | Purpose |
|----------|---------|---------|
| `PYHIVE_HOME` | `~/.pyhive` | Data root (blobs, cache, logs) |
| `PYHIVE_DEBUG` | `false` | Debug mode |
| `PYHIVE_SECRET_KEY` | `changeme-in-production` | Signing / crypto |
| `PYHIVE_BROKER_URL` | `ws://localhost:8080` | Event broker |
| `PYHIVE_API_HOST` | `127.0.0.1` | API bind host |
| `PYHIVE_API_PORT` | `8000` | API bind port |
| `PYHIVE_MAX_WORKERS` | `4` | Worker pool size |
| `PYHIVE_TASK_TIMEOUT` | `300` | Task timeout (seconds) |
| `PYHIVE_DEFAULT_MODEL` | `gemini-1.5-pro` | Default LLM model id |

On first use, `PyHiveConfig` creates:

```
$PYHIVE_HOME/
  data/blobs/
  data/cache/
  logs/
```

External binaries (Tesseract, FFmpeg, etc.) resolve via `PyHivePathResolver` / `PyHiveEnvironment` under `$PYHIVE_HOME/bin` or the system `PATH`.

---

## Security & middleware

```python
from pyhive.core.middleware import MiddlewarePipeline, PyHiveSanitizer, PyHiveRateLimiter
from pyhive.core.policies import PyHiveRBAC, PyHiveSandbox

pipeline = MiddlewarePipeline()
pipeline.add(PyHiveSanitizer(strict_paths=True))
pipeline.add(PyHiveRateLimiter(limit=60, window=60))

rbac = PyHiveRBAC()
rbac.assign_role("alice", "admin")
assert rbac.verify("alice", "filesystem:read:/tmp")

sandbox = PyHiveSandbox(strict_mode=True)
sandbox.inspect_tool(my_safe_function)  # AST check for banned imports/builtins
```

Scopes use `domain:action:resource` with wildcards (`filesystem:*`, `*`).

---

## Plugins

Plugins live in a directory with a `manifest.json`:

```json
{
  "name": "my_plugin",
  "version": "1.0.0",
  "author": "you",
  "description": "Example plugin",
  "entry_point": "main.py",
  "min_pyhive_version": "0.1.0",
  "dependencies": []
}
```

Subclass `PyHivePlugin`, implement `setup()`, and register tools on the injected registry. Use `PyHiveHotReloader` during development to reload tool modules on file change.

---

## Storage & crypto

- **`PyHiveBlobStorage`** — content-addressable (SHA-256) local object store with deduplication and streaming
- **ChromaDB** (optional) — vector ops when `chromadb` is installed
- **`PyHiveToken`** — HMAC-SHA256 signed, URL-safe job/access tokens
- **Fernet encryptor** — payload encryption via `cryptography`

---

## Built-in tools (highlights)

| Module | Capabilities |
|--------|----------------|
| `tools.builtins.web` | Clean HTML for LLMs, DuckDuckGo redirects, Wikipedia |
| `tools.builtins.rss` | Read/write RSS/XML as dicts |
| `tools.builtins.net` | List connections, test host:port |
| `tools.ocr` | Tesseract plain text, boxes, PDF/hOCR export |
| `tools.download` | Async Aria2 RPC downloads |
| `tools.audiowaveform` | Async audio waveform generation |

---

## Response contract

Every tool execution returns a uniform `PyHiveResponse`:

```python
{
  "success": true,
  "data": ...,
  "error": null,
  "error_code": null,
  "execution_time": 0.012,
  "timestamp": 1710000000.0
}
```

Error codes include `INVALID_ARGUMENTS`, `EXECUTION_FAILED`, `TOOL_NOT_FOUND`, `FILE_NOT_FOUND`, `SECURITY_VIOLATION`, `RATE_LIMIT_EXCEEDED`, and related registry/runtime codes.

---

## Logging

PyHive uses **loguru**. On Windows, logs default under:

`Documents/PyLMHiveSTEAM/logs/`

`ProcessUpdater` can stream job progress over HTTP + Socket.IO to a broker for UI clients.

---

## Status

This package is under active development. Some surfaces (assembler one-shot runners, cross-OS logging, distributed queue backends) are partial or platform-specific. Prefer the `core/` APIs above for production-shaped usage.

---

## License

Licensed under [Apache 2.0](LIC.md).
