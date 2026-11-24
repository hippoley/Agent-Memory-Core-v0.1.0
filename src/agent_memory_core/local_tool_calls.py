"""Decode one native Qwen tool call, never inventing an action from free text."""
from __future__ import annotations

import json
import re
from uuid import uuid4


class NativeToolCallError(ValueError):
    pass


def decode_qwen_tool_call(text: str, declared_tools: list[dict]) -> dict:
    if not isinstance(text, str) or not isinstance(declared_tools, list) or not declared_tools:
        raise NativeToolCallError("A real native tool call and declared tools are required")
    match = re.fullmatch(
        r"\s*(?:<think>.*?</think>\s*)?<tool_call>\s*(\{.*\})\s*</tool_call>\s*",
        text, re.S)
    if match is None:
        raise NativeToolCallError("No single native Qwen tool-call frame")
    try:
        decoded = json.loads(match.group(1), parse_constant=lambda value: (
            _ for _ in ()).throw(ValueError("Non-finite value")))
    except (ValueError, TypeError) as exc:
        raise NativeToolCallError("Malformed native tool-call JSON") from exc
    if not isinstance(decoded, dict) or set(decoded) != {"name", "arguments"}:
        raise NativeToolCallError("Native tool-call shape is invalid")
    names = [t.get("function", {}).get("name") for t in declared_tools
             if isinstance(t, dict) and t.get("type") == "function"
             and isinstance(t.get("function"), dict)]
    if (not isinstance(decoded["name"], str) or decoded["name"] not in names
            or len(names) != len(declared_tools) or len(set(names)) != len(names)
            or not isinstance(decoded["arguments"], dict)):
        raise NativeToolCallError("Undeclared tool or invalid native arguments")
    return {"id": "call_" + uuid4().hex[:16], "type": "function",
            "function": {"name": decoded["name"],
                         "arguments": json.dumps(decoded["arguments"], ensure_ascii=False,
                                                 separators=(",", ":"), allow_nan=False)}}
