# pyhive/core/parser.py

import inspect
import re
from typing import Any, Dict, Optional, Type
from pydantic import BaseModel, ValidationError
from .._logging import logger
from .base import ToolValidationError
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from .base import PyHiveTool
    from .base import PyHiveException

class PyHiveSchemaParser:
    """
    Converts Python Tools into LLM-compatible JSON Schemas.
    
    Features:
    - Pydantic Integration: Extracts types directly from the tool's validation model.
    - Docstring Parsing: Scrapes Google-style docstrings to find descriptions for arguments 
      that weren't explicitly annotated with Field(description=...).
    - Format Standardization: Outputs strict JSON Schema draft 2020-12 (OpenAI/Gemini standard).
    """

    def __init__(self):
        self._param_regex = re.compile(r"^\s*(\w+)\s*\(.*?\):\s*(.*)$")

    def to_json_schema(self, tool: 'PyHiveTool') -> Dict[str, Any]:
        """
        Generates the function definition payload for the LLM.
        """
        try:
            model_schema = tool._model.model_json_schema()
            
            properties = model_schema.get("properties", {})
            required = model_schema.get("required", [])
            
            doc_params = self._parse_docstring(tool.description)
            
            for prop_name, prop_def in properties.items():
                prop_def.pop("title", None)
                prop_def.pop("default", None)
                
                if "description" not in prop_def and prop_name in doc_params:
                    prop_def["description"] = doc_params[prop_name]

            return {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": self._clean_description(tool.description),
                    "parameters": {
                        "type": "object",
                        "properties": properties,
                        "required": required
                    }
                }
            }
        except Exception as e:
            logger.error(f"Schema generation failed for tool '{tool.name}': {e}")
            return {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": f"Error generating schema: {str(e)}",
                    "parameters": {"type": "object", "properties": {}}
                }
            }

    def _parse_docstring(self, docstring: str) -> Dict[str, str]:
        """
        Simple parser for Google-style docstrings.
        Extracts the 'Args:' section to a dictionary.
        """
        params = {}
        if not docstring:
            return params

        lines = docstring.split('\n')
        in_args_section = False
        
        for line in lines:
            line = line.strip()
            if line.lower().startswith("args:"):
                in_args_section = True
                continue
            
            if in_args_section:
                if not line: continue
                if line.lower().startswith("returns:") or line.lower().startswith("raises:"):
                    break
                
                match = re.match(r"^(\w+)(?:\s*\(.*?\))?\s*:\s*(.*)$", line)
                if match:
                    param_name, description = match.groups()
                    params[param_name] = description.strip()
        
        return params

    def _clean_description(self, docstring: str) -> str:
        """Removes the Args/Returns sections from the main description sent to LLM."""
        if not docstring: return ""
        split_tokens = ["Args:", "Returns:", "Raises:", "Example:"]
        cleaned = docstring
        for token in split_tokens:
            if token in cleaned:
                cleaned = cleaned.split(token)[0]
        return cleaned.strip()


class PyHiveValidator:
    """
    Central Validation Engine.
    
    Validates incoming data against tool definitions.
    Used by the API Gateway and Worker nodes to ensure integrity.
    """

    def __init__(self):
        pass

    def validate_request(self, tool: 'PyHiveTool', args: Dict[str, Any]) -> Dict[str, Any]:
        """
        Validates a raw dictionary of arguments against the tool's Pydantic model.
        
        Returns:
            Dict: The cleaned, type-coerced arguments.
            
        Raises:
            ToolValidationError: If inputs are invalid.
        """
        
        
        try:
            validated_model = tool._model(**args)
            return validated_model.model_dump()
        except ValidationError as e:
            error_messages = []
            for err in e.errors():
                loc = ".".join(map(str, err['loc']))
                msg = err['msg']
                error_messages.append(f"Field '{loc}': {msg}")
            
            error_str = " | ".join(error_messages)
            logger.warning(f"Validation failed for tool '{tool.name}': {error_str}")
            raise ToolValidationError(f"Invalid arguments: {error_str}")

    def check_schema_compatibility(self, stored_schema: Dict, current_tool: 'PyHiveTool') -> bool:
        """
        Versioning Check.
        
        Checks if the currently loaded tool code matches the schema stored in the database.
        Useful for distributed workers to know if they need to update their definitions.
        """
        parser = PyHiveSchemaParser()
        current_schema = parser.to_json_schema(current_tool)
        
        # Deep comparison (excluding descriptions which might change without breaking logic)
        # TODO: compare signature hashes.
        
        curr_params = set(current_schema['function']['parameters']['properties'].keys())
        stored_params = set(stored_schema['function']['parameters']['properties'].keys())
        
        return curr_params == stored_params