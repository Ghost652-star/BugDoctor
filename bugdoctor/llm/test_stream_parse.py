"""验证 Anthropic SSE event → StreamEvent 的解析逻辑。

不发起真实 HTTP，直接喂 ``_dispatch_sse_event`` 模拟 Anthropic 服务端
发过来的事件 dict，验证产出的 StreamEvent 序列是否符合预期。
"""

from __future__ import annotations

from bugdoctor.llm.anthropic_client import _dispatch_sse_event
from bugdoctor.llm.events import (
    TextDelta,
    ToolCallComplete,
    ToolCallDelta,
    ToolCallStart,
)


def _empty_state():
    return {}, {}, {}


def test_text_delta_yields_text_delta():
    evt = {
        "type": "content_block_delta",
        "index": 0,
        "delta": {"type": "text_delta", "text": "你好"},
    }
    out = _dispatch_sse_event(evt, "content_block_delta", *_empty_state())
    assert len(out) == 1
    assert isinstance(out[0], TextDelta)
    assert out[0].text == "你好"


def test_tool_use_round_trip():
    """一个完整的 tool_use 流程: start → 两次 input_json_delta → stop"""
    pending, meta, ids = _empty_state()

    start_evt = {
        "type": "content_block_start",
        "index": 1,
        "content_block": {
            "type": "tool_use",
            "id": "toolu_abc123",
            "name": "read_file",
            "input": {},
        },
    }
    out = _dispatch_sse_event(start_evt, "content_block_start", pending, meta, ids)
    assert len(out) == 1
    assert isinstance(out[0], ToolCallStart)
    assert out[0].tool_call_id == "toolu_abc123"
    assert out[0].tool_name == "read_file"

    delta1 = {
        "type": "content_block_delta",
        "index": 1,
        "delta": {"type": "input_json_delta", "partial_json": '{"file_p'},
    }
    out1 = _dispatch_sse_event(delta1, "content_block_delta", pending, meta, ids)
    assert isinstance(out1[0], ToolCallDelta)
    assert out1[0].arguments_delta == '{"file_p'

    delta2 = {
        "type": "content_block_delta",
        "index": 1,
        "delta": {"type": "input_json_delta", "partial_json": 'ath": "user.py"}'},
    }
    out2 = _dispatch_sse_event(delta2, "content_block_delta", pending, meta, ids)
    assert isinstance(out2[0], ToolCallDelta)
    assert pending[1] == '{"file_path": "user.py"}'

    stop = {"type": "content_block_stop", "index": 1}
    out3 = _dispatch_sse_event(stop, "content_block_stop", pending, meta, ids)
    assert isinstance(out3[0], ToolCallComplete)
    assert out3[0].tool_call_id == "toolu_abc123"
    assert out3[0].tool_name == "read_file"
    assert out3[0].arguments == {"file_path": "user.py"}


def test_malformed_json_falls_back_to_empty_dict():
    pending, meta, ids = _empty_state()
    pending[0] = "{not valid json"
    meta[0] = "broken"
    ids[0] = "tid-bad"
    stop = {"type": "content_block_stop", "index": 0}
    out = _dispatch_sse_event(stop, "content_block_stop", pending, meta, ids)
    assert isinstance(out[0], ToolCallComplete)
    assert out[0].arguments == {}


def _run_all():
    cases = [
        test_text_delta_yields_text_delta,
        test_tool_use_round_trip,
        test_malformed_json_falls_back_to_empty_dict,
    ]
    failed = 0
    for fn in cases:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL  {fn.__name__}: {e}")
    print(f"\n{'3/3 passed' if failed == 0 else f'{failed} failed'}")
    raise SystemExit(0 if failed == 0 else 1)


if __name__ == "__main__":
    _run_all()
