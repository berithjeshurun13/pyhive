# pyhive/core/workflows.py
import uuid
import time
import json
import concurrent.futures
from typing import List, Dict, Any, Optional, Callable, Union, Set
from collections import deque, defaultdict
from pydantic import BaseModel, Field
from .._logging import logger
from .base import PyHiveResponse, ToolExecutionError
from .registry import PyHiveRegistry
from .context import PyHiveContext
import inspect


class PyHiveMemory:
    """
    Short-term memory management for Agents and Chains.
    
    Features:
    - Role-based storage (System, User, Assistant, Tool).
    - Sliding Window: Automatically drops old messages to fit context limits.
    """
    def __init__(self, max_messages: int = 20):
        self.max_messages = max_messages
        self._history: deque = deque(maxlen=max_messages)

    def add(self, role: str, content: str, metadata: Dict = None):
        """Adds a message to history."""
        self._history.append({
            "role": role,
            "content": content,
            "metadata": metadata or {},
            "timestamp": time.time()
        })

    def get_context(self, format_type: str = "list") -> Union[List[Dict], str]:
        """
        Returns history in requested format.
        'list': List of dicts (for APIs).
        'text': String buffer (for raw prompts).
        """
        if format_type == "text":
            return "\n".join([f"{msg['role'].upper()}: {msg['content']}" for msg in self._history])
        return list(self._history)

    def clear(self):
        self._history.clear()


class PyHiveChain:
    """
    Deterministic Sequence of Tools.
    
    Data flows from one step to the next based on an 'input_map'.
    Useful for fixed workflows like "Extract Text -> Summarize -> Translate".
    """
    
    def __init__(self, registry: PyHiveRegistry):
        self.registry = registry
        self.steps: List[Dict[str, Any]] = []

    def add_step(self, tool_name: str, input_map: Dict[str, str] = None):
        """
        Adds a tool to the chain.
        input_map: Maps output keys from previous step to args of this step.
                   e.g., {"text": "$prev"} means 'use previous result as text arg'.
        """
        self.steps.append({
            "tool": tool_name,
            "map": input_map or {}
        })

    def execute(self, initial_input: Dict[str, Any], context: PyHiveContext) -> PyHiveResponse:
        """Runs the pipeline sequentially."""
        current_data = initial_input
        history = []

        try:
            for i, step in enumerate(self.steps):
                tool_name = step["tool"]
                mapping = step["map"]
                
                tool_args = {}
                for arg_name, source in mapping.items():
                    if source == "$prev":
                        tool_args[arg_name] = current_data
                    elif source.startswith("$input."):
                        key = source.split(".")[1]
                        tool_args[arg_name] = initial_input.get(key)
                    else:
                        tool_args[arg_name] = source
                
                if not mapping:
                    if isinstance(current_data, dict):
                        tool_args = current_data
                    else:
                        pass

                tool = self.registry.get_tool(tool_name)
                logger.info(f"Chain Step {i+1}: {tool_name}")
                
                response = tool.execute(**tool_args)
                
                if not response.success:
                    raise ToolExecutionError(f"Step '{tool_name}' failed: {response.error}")

                current_data = response.data
                history.append(response)

            return PyHiveResponse(success=True, data=current_data)

        except Exception as e:
            logger.error(f"Chain Failed: {e}")
            return PyHiveResponse(success=False, error=str(e))


class PyHiveDAG:
    """
    Directed Acyclic Graph Executor.
    
    Allows parallel execution of independent tasks.
    Example: 
    1. Fetch URL (A)
    2. Extract Images (B) & Extract Text (C) [Run in Parallel]
    3. Generate Report (D) [Runs when B and C finish]
    """
    
    def __init__(self, registry: PyHiveRegistry, max_workers: int = 4):
        self.registry = registry
        self.nodes: Dict[str, Dict] = {} # {id: {tool, args}}
        self.edges: Dict[str, List[str]] = defaultdict(list) # {parent: [children]}
        self.in_degree: Dict[str, int] = defaultdict(int)
        self.max_workers = max_workers

    def add_node(self, node_id: str, tool_name: str, args: Dict[str, Any]):
        self.nodes[node_id] = {"tool": tool_name, "args": args}
        self.in_degree[node_id] = 0

    def add_edge(self, parent_id: str, child_id: str):
        self.edges[parent_id].append(child_id)
        self.in_degree[child_id] += 1

    def execute(self, context: PyHiveContext) -> Dict[str, Any]:
        """
        Executes the graph using a ThreadPool for parallelism.
        """
        results = {}
        queue = deque([n for n in self.nodes if self.in_degree[n] == 0])
        
        active_futures = set()
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            while queue or active_futures:
                
                while queue:
                    node_id = queue.popleft()
                    node_data = self.nodes[node_id]
                    
                    # Resolve args (simplified dependency injection)
                    # In a real DAG, we'd merge outputs from parents into args here.
                    
                    future = executor.submit(self._run_node, node_id, node_data, context)
                    active_futures.add(future)

                if not active_futures:
                    break
                    
                done, _ = concurrent.futures.wait(active_futures, return_when=concurrent.futures.FIRST_COMPLETED)
                
                for f in done:
                    active_futures.remove(f)
                    try:
                        nid, result = f.result()
                        results[nid] = result
                        
                        for child in self.edges[nid]:
                            self.in_degree[child] -= 1
                            if self.in_degree[child] == 0:
                                queue.append(child)
                                
                    except Exception as e:
                        logger.error(f"DAG Execution Error: {e}")
                        raise

        return results

    def _run_node(self, node_id: str, data: Dict, context: PyHiveContext):
        tool = self.registry.get_tool(data["tool"])
        logger.info(f"DAG Node {node_id}: Running {tool.name}")
        response = tool.execute(**data["args"])
        if not response.success:
            raise ToolExecutionError(f"Node {node_id} failed: {response.error}")
        return node_id, response.data



class PyHiveAgent:
    """
    Production-Grade ReAct (Reasoning + Acting) Agent.
    
    Loop:
    1. Think: Ask LLM what to do next based on history.
    2. Act: Execute the tool chosen by LLM.
    3. Observe: Add tool output to memory.
    4. Repeat until goal is met or max_steps reached.
    """

    def __init__(
        self, 
        name: str, 
        registry: PyHiveRegistry, 
        llm_callable: Callable, 
        system_prompt: str = "You are a helpful AI assistant. Use tools to answer questions.",
        max_steps: int = 10
    ):
        self.name = name
        self.registry = registry
        self.llm = llm_callable
        self.memory = PyHiveMemory()
        self.max_steps = max_steps
        
        self.memory.add("system", system_prompt)

    def run(self, goal: str, context: PyHiveContext) -> str:
        """Synchronous execution of the agent loop."""
        self.memory.add("user", goal)
        tools_schema = self.registry.get_llm_definitions()
        
        emitter = context.emitter 

        if emitter:
            emitter.send_progress(0.1, f"Agent '{self.name}' starting task...")

        for step in range(self.max_steps):
            logger.info(f"Agent '{self.name}' Step {step+1}/{self.max_steps}")
            
            try:
                response = self.llm(
                    messages=self.memory.get_context("list"),
                    tools=tools_schema
                )
            except Exception as e:
                error_msg = f"LLM Connection Error: {str(e)}"
                logger.error(error_msg)
                if emitter: emitter.send_error(error_msg)
                return error_msg
            
            tool_calls = response.get("tool_calls", [])
            
            if not tool_calls:
                final_answer = response.get("content", "")
                self.memory.add("assistant", final_answer)
                
                if emitter:
                    emitter.send_progress(1.0, "Task complete.")
                    
                return final_answer

            content = response.get("content", "")
            self.memory.add("assistant", content, metadata={"tool_calls": tool_calls})
            
            for call in tool_calls:
                func_name = call["function"]["name"]
                args_str = call["function"]["arguments"]
                call_id = call["id"]
                
                if emitter:
                    emitter.send_log(f"Calling tool: {func_name}", level="INFO")
                
                try:
                    args = json.loads(args_str)
                    logger.info(f"Agent calling: {func_name}({args})")
                    
                    tool = self.registry.get_tool(func_name)
                    
                    sig = inspect.signature(tool._func)
                    if "context" in sig.parameters:
                        args["context"] = context
                    
                    result_obj = tool.execute(**args)
                    
                    if result_obj.success:
                        result_str = str(result_obj.data)
                    else:
                        result_str = f"Tool Error: {result_obj.error}"
                        if emitter: emitter.send_error(f"{func_name} failed: {result_obj.error}")
                        
                except json.JSONDecodeError:
                    result_str = "Execution Exception: Failed to parse tool arguments as valid JSON."
                except Exception as e:
                    result_str = f"Execution Exception: {str(e)}"
                    logger.error(f"Tool execution failed: {e}")

                self.memory.add("tool", result_str, metadata={"tool_call_id": call_id})

        error_msg = f"Max steps ({self.max_steps}) reached without final answer."
        if emitter: emitter.send_error(error_msg)
        return error_msg