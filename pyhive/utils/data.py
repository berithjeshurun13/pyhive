# pyhive/utils/data.py
import json
import base64
import io
import datetime
import uuid
from .._logging import logger as logging
from typing import Any, Dict, List, Union, Optional

import re

class PyHiveChunker(object):
    """
    Intelligent text splitter for LLM workflows.
    
    Splits massive text blobs into overlapping chunks that fit within 
    context windows (e.g., 8k, 32k, 128k tokens).
    
    Strategy: Recursive Splitting.
    1. Try splitting by double newlines (paragraphs).
    2. If a chunk is still too big, split by single newlines.
    3. If still too big, split by sentences (. ! ?).
    4. If still too big, split by words (spaces).
    """

    def __init__(self, chunk_size: int = 4000, chunk_overlap: int = 200):
        """
        Args:
            chunk_size: Target size of each chunk (in characters, approx tokens / 4).
            chunk_overlap: How many characters to repeat between chunks to preserve context.
        """
        super().__init__()
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        
        self._separators = ["\n\n", "\n", ". ", "! ", "? ", " ", ""]

    def split_text(self, text: str) -> List[str]:
        """
        Main entry point. Takes a massive string and returns a list of chunks.
        """
        if not text:
            return []

        return self._split_recursive(text, self._separators)

    def _split_recursive(self, text: str, separators: List[str]) -> List[str]:
        """
        Recursively tries to split text by the highest priority separator.
        """
        final_chunks = []
        
        separator = separators[-1]
        new_separators = []
        
        for i, sep in enumerate(separators):
            if sep == "":
                separator = ""
                break
            if sep in text:
                separator = sep
                new_separators = separators[i + 1:]
                break

        splits = self._split_by_separator(text, separator)

        current_chunk = []
        current_length = 0
        
        for split in splits:
            split_len = len(split)
            
            if current_length + split_len > self.chunk_size:
                if current_chunk:
                    doc = self._join_splits(current_chunk, separator)
                    final_chunks.append(doc)
                    
                    overlap_len = 0
                    keep_splits = []
                    for s in reversed(current_chunk):
                        if overlap_len + len(s) > self.chunk_overlap:
                            break
                        keep_splits.insert(0, s)
                        overlap_len += len(s)
                        
                    current_chunk = keep_splits
                    current_length = overlap_len
                
                if split_len > self.chunk_size and new_separators:
                    sub_chunks = self._split_recursive(split, new_separators)
                    final_chunks.extend(sub_chunks)
                else:
                    current_chunk.append(split)
                    current_length += split_len
            else:
                current_chunk.append(split)
                current_length += split_len

        if current_chunk:
            final_chunks.append(self._join_splits(current_chunk, separator))

        return final_chunks

    def _split_by_separator(self, text: str, separator: str) -> List[str]:
        """Helper to split text, keeping the separator attached if possible."""
        if separator == "":
            return list(text)
        
        splits = text.split(separator)
        return [s + separator for s in splits[:-1]] + [splits[-1]] if splits[-1] else [s + separator for s in splits[:-1]]

    def _join_splits(self, splits: List[str], separator: str) -> str:
        """Helper to join splits back into a string."""
        if separator == "":
            return "".join(splits)
        else:
            return "".join(splits).strip()

    def count_tokens(self, text: str) -> int:
        """
        Rough estimation of tokens (Char count / 4).
        For precise counting, inject 'tiktoken' dependency if available.
        """
        try:
            import tiktoken
            enc = tiktoken.get_encoding("cl100k_base")
            return len(enc.encode(text))
        except ImportError:
            # Fallback estimation
            return len(text) // 4


class PyHiveSerializer(object):
    """
    Universal object converter for LLM compatibility.
    
    Transforms complex Python objects (Pandas, Numpy, Qt, PIL) into 
    JSON-safe primitives (str, dict, list) that LLMs can understand.
    
    Features:
    - Lazy loading of dependencies (won't crash if Pandas/Qt isn't installed).
    - Auto-truncation of massive DataFrames to prevent token limit explosions.
    - Base64 encoding for images.
    """

    def __init__(self, max_df_rows: int = 20, image_format: str = "PNG"):
        super().__init__()
        self.max_df_rows = max_df_rows
        self.image_format = image_format.upper()

    def serialize(self, obj: Any) -> Any:
        """
        Main entry point. Recursively transforms an object into a JSON-safe format.
        """
        if obj is None or isinstance(obj, (str, int, float, bool)):
            return obj

        if isinstance(obj, dict):
            return {str(k): self.serialize(v) for k, v in obj.items()}
        
        if isinstance(obj, (list, tuple, set)):
            return [self.serialize(i) for i in obj]

        if isinstance(obj, (datetime.date, datetime.datetime)):
            return obj.isoformat()
        
        if isinstance(obj, uuid.UUID):
            return str(obj)

        if isinstance(obj, Exception):
            return f"{type(obj).__name__}: {str(obj)}"

        if hasattr(obj, "__dataclass_fields__"):
            import dataclasses
            return self.serialize(dataclasses.asdict(obj))

        if hasattr(obj, "model_dump"):
            return self.serialize(obj.model_dump())
        
        if hasattr(obj, "dict") and callable(obj.dict):
            return self.serialize(obj.dict())

        
        if self._is_numpy(obj):
            try:
                return obj.tolist()
            except Exception:
                pass

        if self._is_pandas(obj):
            return self._serialize_pandas(obj)

        if self._is_pil(obj):
            return self._serialize_pil_image(obj)

        if self._is_qt_image(obj):
            return self._serialize_qt_image(obj)

        return str(obj)

    def to_json(self, obj: Any, indent: int = 2) -> str:
        """Helper to dump directly to a JSON string."""
        return json.dumps(self.serialize(obj), indent=indent)


    def _is_numpy(self, obj: Any) -> bool:
        return "numpy" in str(type(obj).__module__)

    def _is_pandas(self, obj: Any) -> bool:
        return "pandas" in str(type(obj).__module__)

    def _is_pil(self, obj: Any) -> bool:
        return "PIL" in str(type(obj).__module__)

    def _is_qt_image(self, obj: Any) -> bool:
        t_name = type(obj).__name__
        t_mod = str(type(obj).__module__)
        return ("PySide" in t_mod or "PyQt" in t_mod) and (t_name in ["QImage", "QPixmap"])


    def _serialize_pandas(self, df: Any) -> Union[str, Dict]:
        """Converts DataFrame to Markdown table for LLM readability."""
        try:
            if hasattr(df, "shape") and df.shape[0] > self.max_df_rows:
                short_df = df.head(self.max_df_rows)
                markdown = short_df.to_markdown(index=False)
                return f"DataFrame (First {self.max_df_rows} of {df.shape[0]} rows):\n{markdown}"
            
            if hasattr(df, "to_markdown"):
                return df.to_markdown(index=False)
            
            return df.to_string()
        except Exception as e:
            return f"Error serializing DataFrame: {e}"

    def _serialize_pil_image(self, img: Any) -> str:
        """Converts PIL Image to Base64 String."""
        try:
            buffer = io.BytesIO()
            if self.image_format == "JPEG" and img.mode == "RGBA":
                img = img.convert("RGB")
                
            img.save(buffer, format=self.image_format)
            b64_str = base64.b64encode(buffer.getvalue()).decode("utf-8")
            return f"data:image/{self.image_format.lower()};base64,{b64_str}"
        except Exception as e:
            return f"<Image: Serialization Failed: {e}>"

    def _serialize_qt_image(self, qt_obj: Any) -> str:
        """Converts QImage/QPixmap to Base64 String."""
        try:
            from PySide6.QtCore import QBuffer, QByteArray, QIODevice
            
            if type(qt_obj).__name__ == "QPixmap":
                qt_obj = qt_obj.toImage()
                
            byte_array = QByteArray()
            buffer = QBuffer(byte_array)
            buffer.open(QIODevice.WriteOnly)
            qt_obj.save(buffer, self.image_format)
            
            b64_str = byte_array.toBase64().data().decode("utf-8")
            return f"data:image/{self.image_format.lower()};base64,{b64_str}"
        except ImportError:
            return "<QImage: Serialization requires PySide6/PyQt6>"
        except Exception as e:
            return f"<QImage: Serialization Failed: {e}>"