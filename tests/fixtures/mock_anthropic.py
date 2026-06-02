"""A scripted fake AsyncAnthropic for offline backend testing (M3-4, no API key).

Mimics just the slice the Claude API backend uses:

    async with client.messages.stream(...) as stream:
        msg = await stream.get_final_message()

`FakeAnthropic(script)` returns the scripted messages in order (reusing the last one
if the loop runs longer). It records, per stream() call, the system/tools/tool_choice
and a snapshot of message count + whether the last message carries a cache breakpoint
(captured at call time, since the backend mutates the messages list in place).
"""
from __future__ import annotations

from types import SimpleNamespace


def usage(inp=1000, out=50, cache_write=0, cache_read=0):
    return SimpleNamespace(
        input_tokens=inp, output_tokens=out,
        cache_creation_input_tokens=cache_write, cache_read_input_tokens=cache_read,
    )


def text(s: str):
    return SimpleNamespace(type="text", text=s)


def tool_use(id: str, name: str, input: dict):
    return SimpleNamespace(type="tool_use", id=id, name=name, input=input)


def message(content, stop_reason, u=None):
    return SimpleNamespace(content=content, stop_reason=stop_reason, usage=u or usage())


class _Stream:
    def __init__(self, msg):
        self._msg = msg

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get_final_message(self):
        return self._msg


class _Messages:
    def __init__(self, script, calls):
        self._script = list(script)
        self._calls = calls
        self._i = 0

    def stream(self, **kwargs):
        msgs = kwargs.get("messages", [])
        last = msgs[-1] if msgs else None
        marked = bool(
            last and isinstance(last.get("content"), list)
            and any(isinstance(b, dict) and "cache_control" in b for b in last["content"])
        )
        self._calls.append({
            "system": kwargs.get("system"),
            "tools": kwargs.get("tools"),
            "tool_choice": kwargs.get("tool_choice"),
            "n_messages": len(msgs),
            "last_marked": marked,
        })
        msg = self._script[min(self._i, len(self._script) - 1)]
        self._i += 1
        return _Stream(msg)


class FakeAnthropic:
    def __init__(self, script):
        self.calls: list[dict] = []
        self.messages = _Messages(script, self.calls)
