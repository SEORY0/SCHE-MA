"""A scripted fake AsyncOpenAI for offline OpenAI-backend testing (no API key).

Mimics just the slice the OpenAI Responses backend uses:

    resp = await client.responses.create(...)
    resp.output / resp.usage / resp.status / resp.incomplete_details

`FakeOpenAI(script)` returns the scripted responses in order (reusing the last one if the
loop runs longer), recording per-call instructions/tools/tool_choice and the input length.
"""
from __future__ import annotations

from types import SimpleNamespace


def usage(inp=1000, out=50, cached=0, reasoning=0):
    return SimpleNamespace(
        input_tokens=inp,
        output_tokens=out,
        input_tokens_details=SimpleNamespace(cached_tokens=cached),
        output_tokens_details=SimpleNamespace(reasoning_tokens=reasoning),
    )


def output_text(s: str):
    """An assistant message output item carrying output_text."""
    return SimpleNamespace(
        type="message", role="assistant",
        content=[SimpleNamespace(type="output_text", text=s)],
    )


def function_call(call_id: str, name: str, arguments: str):
    """A function_call output item. `arguments` is a JSON string, per the Responses API."""
    return SimpleNamespace(type="function_call", call_id=call_id, name=name,
                           arguments=arguments, id="fc_" + call_id)


def response(output, status="completed", u=None, incomplete_reason=None):
    return SimpleNamespace(
        output=list(output), status=status, usage=u or usage(),
        incomplete_details=SimpleNamespace(reason=incomplete_reason), id="resp_1",
    )


class _Responses:
    def __init__(self, script, calls):
        self._script = list(script)
        self._calls = calls
        self._i = 0

    async def create(self, **kwargs):
        inp = kwargs.get("input", []) or []
        self._calls.append({
            "instructions": kwargs.get("instructions"),
            "tools": kwargs.get("tools"),
            "tool_choice": kwargs.get("tool_choice"),
            "n_input": len(inp),
        })
        r = self._script[min(self._i, len(self._script) - 1)]
        self._i += 1
        return r


class FakeOpenAI:
    def __init__(self, script):
        self.calls: list[dict] = []
        self.responses = _Responses(script, self.calls)
