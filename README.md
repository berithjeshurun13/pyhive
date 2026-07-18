# PyHive

**PyHive** is a Python framework for building LLM tool-calling agents and orchestration pipelines. It wraps callable Python functions as typed tools, exposes them as OpenAI/Gemini-compatible JSON schemas, and runs them through queues, chains, DAGs, or ReAct-style agents — with middleware, RBAC, and local storage built in.

( Currently in developmental phase )

Originally oriented around Ollama-style local LLM workflows, PyHive has grown into a general-purpose **tool registry + agent runtime**.

PyHive is the framework; **HiveSTEAM** is the on-disk runtime pack it needs for full tool support. Point `PYHIVE_HOME` at a HiveSTEAM tree — see [HiveSTEAM](#hivesteam) below.

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
| **HiveSTEAM** | External dependency pack: binaries, models, NLP data, and language runtimes |
| **Built-ins** | Web scrape/search, RSS, OCR (Tesseract), downloads (Aria2), audio waveforms, networking |

---

## Requirements

- Python 3.10+
- Core deps: `pydantic`, `pydantic-settings`, `loguru`
- Optional / tool-specific: `httpx`, `beautifulsoup4`, `wikipedia-api`, `pytesseract`, `Pillow`, `chromadb`, `cryptography`, `msgpack`, `aiohttp`, `opencv-python`, `numpy`, `python-socketio`, `requests`
- **HiveSTEAM** (recommended for full tool support): local asset pack with FFmpeg, Tesseract, Aria2, models, and portable runtimes — see [HiveSTEAM](#hivesteam)

---

## HiveSTEAM

**HiveSTEAM** is the local runtime / dependency pack that PyHive needs to run tool-heavy agent workflows. It is not the Python package; it is the on-disk home for binaries, models, language runtimes, blobs, and logs that PyHive resolves through `PYHIVE_HOME`.

| Item | Value |
|------|--------|
| **Role** | External asset + runtime pack for PyHive |
| **Env var** | `PYHIVE_HOME` → path to the HiveSTEAM directory |
| **Approx. size** | ~5.5 GB (`binaries` ~3.5 GB, `runtime` ~2.0 GB) |
| **Platform** | Windows-oriented pack (WinPython, portable Java, MinGW, `.exe` tools) |

PyHive’s bootstrap / path code refers to this as the **dependency pack**. Without HiveSTEAM (or an equivalent home), core registry/agent APIs still work for pure-Python tools; binary-backed tools (OCR, Aria2, waveforms, FFmpeg, bundled models) need matching executables on `PATH` or under this pack.

### Point PyHive at HiveSTEAM

```bash
# Linux / macOS
export PYHIVE_HOME=/path/to/HiveSTEAM

# Windows (PowerShell)
$env:PYHIVE_HOME = "G:\HiveSTEAM"
```

Or persist with `PyHiveEnvManager`:

```python
from pyhive.utils.env import PyHiveEnvManager

mgr = PyHiveEnvManager()
mgr.persist("PYHIVE_HOME", r"G:\HiveSTEAM")
```

`PyHivePathResolver` looks under that home (and falls back to OS defaults / `PATH`) for tools and models. First-run setup can also be driven by `PyHiveBootstrapper`, which verifies and downloads assets from a manifest when configured.

### Layout

```
HiveSTEAM/                 # = $PYHIVE_HOME
├── config.json            # Local config stub
├── binaries/              # Tools, packages, models, NLP assets (~3.5 GB)
│   ├── etc/               # Shared CLI binaries & DLLs
│   ├── mdl/               # ML / TTS model trees
│   │   ├── emb/           # Embeddings (e.g. all-MiniLM-L6-v2)
│   │   └── vce/           # Voice / TTS voices (Piper-style ONNX packs)
│   ├── pkg/               # Packaged suites (Tesseract, Nmap)
│   └── req/               # Language / NLP requirements (spaCy, NLTK)
├── blobs/                 # One-shot / large model blobs
│   └── oneshot/           # e.g. yolov7.pt
├── data/                  # Writable PyHive data root
│   ├── blobs/             # Content-addressable / runtime blobs
│   ├── cache/
│   └── vectors/           # Vector store data
├── logs/
└── runtime/               # Language & service environments (~2.0 GB)
    ├── env/
    │   ├── python3/       # WinPython toolchain
    │   ├── java/          # Portable Java
    │   ├── perl/          # Perl distribution
    │   ├── lua/           # Lua 5.4
    │   └── c/             # MinGW-w64 toolchain
    ├── services/          # Background helpers (e.g. HxLogging.exe)
    └── standalone/        # Standalone utilities (minify, py2exe, ...)
```

### What lives where

**`binaries/etc` — shared tools**

CLI/DLL pack used by PyHive tool modules and external process wrappers:

- **Media:** `ffmpeg`, `ffprobe`, `ffplay`, ImageMagick (`magick`, `identify`, ...)
- **Download / capture:** `aria2c`, `yt-dlp`, `scrcpy` + `adb`
- **Docs / audio:** `wkhtmltopdf`, `audiowaveform`
- **Vision / science:** StarNet (`stnet` + TensorFlow weights)

**`binaries/mdl` — models**

| Path | Contents |
|------|----------|
| `mdl/emb/all-MiniLM-L6-v2` | Sentence-transformer style embedding model |
| `mdl/vce/*` | English TTS voice packs (`en_US-*`, `en_GB-*`) |

**`binaries/pkg` — packaged suites**

- **Tesseract** — OCR (`pyhive.tools.ocr`)
- **Nmap** — network scanning suite

**`binaries/req` — NLP data**

- spaCy `en_core_web_sm-3.7.1`
- NLTK data (`punkt`, taggers, models, ...)

**`blobs/`** — large one-shot assets not folded into `mdl/` (e.g. `oneshot/yolov7.pt`)

**`data/`** — writable tree: blobs, cache, vectors (storage / Chroma-style workloads)

**`runtime/`** — isolated language environments so tools can run without a system-wide install:

- WinPython (`runtime/env/python3`)
- Portable Java, Perl, Lua 5.4
- MinGW-w64 under `runtime/env/c`
- Service binaries under `runtime/services`
- Standalone helpers under `runtime/standalone/direct`

### How HiveSTEAM connects to PyHive

1. Point **`PYHIVE_HOME`** at the HiveSTEAM folder.
2. Install / import the **`pyhive`** package.
3. Path resolution (`PyHivePathResolver`) and bootstrap (`PyHiveBootstrapper`) use that home for binaries, models, and data.
4. Built-in tools (OCR, Aria2, waveforms, FFmpeg pipelines, etc.) expect matching executables from this pack.

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
                       │
                       ▼
         HiveSTEAM ($PYHIVE_HOME): binaries, models, data, runtime
```

### Core concepts

| Type | Role |
|------|------|
| `PyHiveTool` | Callable wrapper: validates args, runs sync/async, returns `PyHiveResponse` |
| `PyHiveRegistry` | Central catalog of tools; builds LLM definitions |
| `PyHiveContext` | Per-job identity + injected services (`db`, `storage`, `vectors`, `emitter`) |
| `PyHiveConfig` | Settings from env (`PYHIVE_HOME`, broker, workers, default model, ...) |
| `PyHiveJob` / `PyHiveWorker` | Queued execution unit and background consumer |
| `PyHiveAgent` | LLM → tool → observe loop until a final answer |
| `PyHiveEmitter` / `PyHiveRoom` | Stream progress/logs to subscribers (local or broker) |
| HiveSTEAM | On-disk dependency pack resolved via `PYHIVE_HOME` |

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
│       └── mario.py      # Procedural noise (Perlin, FBM, Worley, ...)
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
| `PYHIVE_HOME` | `~/.pyhive` (or OS-specific AppData path) | HiveSTEAM / data root (blobs, cache, logs, binaries) |
| `PYHIVE_DEBUG` | `false` | Debug mode |
| `PYHIVE_SECRET_KEY` | `changeme-in-production` | Signing / crypto |
| `PYHIVE_BROKER_URL` | `ws://localhost:8080` | Event broker |
| `PYHIVE_API_HOST` | `127.0.0.1` | API bind host |
| `PYHIVE_API_PORT` | `8000` | API bind port |
| `PYHIVE_MAX_WORKERS` | `4` | Worker pool size |
| `PYHIVE_TASK_TIMEOUT` | `300` | Task timeout (seconds) |
| `PYHIVE_DEFAULT_MODEL` | `gemini-1.5-pro` | Default LLM model id |

On first use, `PyHiveConfig` creates (under the home root when using the default layout):

```
$PYHIVE_HOME/
  data/blobs/
  data/cache/
  logs/
```

A full HiveSTEAM checkout also includes `binaries/`, `blobs/`, and `runtime/` as described in [HiveSTEAM](#hivesteam).

External binaries (Tesseract, FFmpeg, etc.) resolve via `PyHivePathResolver` under `$PYHIVE_HOME` (or HiveSTEAM’s `binaries/` tree) or the system `PATH`.

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

Several of these expect matching binaries from **HiveSTEAM** (or system installs).

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

When using a HiveSTEAM checkout as `PYHIVE_HOME`, you may also use `$PYHIVE_HOME/logs/` for runtime-adjacent logs.

`ProcessUpdater` can stream job progress over HTTP + Socket.IO to a broker for UI clients.

---

## Status

This package is under active development. Some surfaces (assembler one-shot runners, cross-OS logging, distributed queue backends) are partial or platform-specific. Prefer the `core/` APIs above for production-shaped usage. HiveSTEAM packing and path layouts are Windows-first today.

---

## License

Licensed under [Apache 2.0](LICENSE).
