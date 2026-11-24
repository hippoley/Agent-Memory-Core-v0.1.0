import pytest
from agent_memory_core.local_tool_calls import decode_qwen_tool_call, NativeToolCallError

TOOLS = [{"type": "function", "function": {"name": "set_device_property", "parameters": {"type": "object"}}}]


def test_native_qwen_call_converts_to_openai_tool_call():
    native = '<tool_call>{"name":"set_device_property","arguments":{"device_id":"bedroom","property":"temperature","value":26}}</tool_call>'
    call = decode_qwen_tool_call(native, TOOLS)
    assert call["function"]["name"] == "set_device_property"
    assert '"value":26' in call["function"]["arguments"]


def test_native_qwen_thinking_prefix():
    native = '<think>Choose the tool.</think>\n<tool_call>{"name":"set_device_property","arguments":{}}</tool_call>'
    assert decode_qwen_tool_call(native, TOOLS)["type"] == "function"


@pytest.mark.parametrize("output", [
    "执行成功", '{"name":"set_device_property","arguments":{}}',
    '<tool_call>{"name":"unknown","arguments":{}}</tool_call>',
    '<tool_call>{"name":"set_device_property","arguments":{}}</tool_call><tool_call>{"name":"set_device_property","arguments":{}}</tool_call>',
    '<tool_call>{"name":"set_device_property","arguments":[]}</tool_call>',
    '<tool_call>{"name":"set_device_property","arguments":{}}</tool_call>执行成功',
    '<tool_call>{"name":"set_device_property","arguments":{"x":NaN}}</tool_call>',
])
def test_no_fake_tool_calls_from_invalid_output(output):
    with pytest.raises(NativeToolCallError):
        decode_qwen_tool_call(output, TOOLS)
