from schemata.backends.base import alias_of, cost_of
from schemata.core.models import Usage, Verdict
from schemata.core.util import extract_last_json
from schemata.cybergym.submit import SubmitClient


def test_verdict_crash_semantics():
    assert Verdict(exit_code=1).crashed is True
    assert Verdict(exit_code=0).crashed is False
    # server folds timeout 300 -> 0 before agent sees it; we only ever see != 0 as crash
    assert Verdict(exit_code=139).crashed is True


def test_extract_last_json_prefers_fenced():
    text = "blah\n```json\n{\"a\": 1, \"b\": [2,3]}\n```\nthanks"
    assert extract_last_json(text) == {"a": 1, "b": [2, 3]}


def test_extract_last_json_handles_bare_object():
    text = "no fence here {\"winning_poc_path\": \"poc\", \"final_exit_code\": 1}"
    out = extract_last_json(text)
    assert out["final_exit_code"] == 1


def test_extract_last_json_empty():
    assert extract_last_json("no json at all") == {}


def test_cost_of_haiku():
    u = Usage(model="haiku", input_tokens=1_000_000, output_tokens=0)
    assert abs(cost_of(u, "haiku") - 1.0) < 1e-9
    assert alias_of("claude-opus-4-6") == "opus"


def test_private_verify_and_query_endpoints(monkeypatch):
    calls = []

    class Resp:
        def __init__(self, body):
            self._body = body
        def raise_for_status(self): pass
        def json(self): return self._body

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        if url.endswith("/verify-agent-pocs"):
            return Resp({"poc_ids": ["p1"]})
        return Resp([{"poc_id": "p1", "vul_exit_code": 1, "fix_exit_code": 0}])

    monkeypatch.setattr("schemata.cybergym.submit.requests.post", fake_post)
    c = SubmitClient("http://server/", "m", "agent", "chk")
    assert c.verify_agent_pocs("agent", "key")["poc_ids"] == ["p1"]
    assert c.query_pocs("key", agent_id="agent")[0]["fix_exit_code"] == 0
    assert calls[0][1]["headers"] == {"X-API-Key": "key"}
    assert calls[1][1]["json"] == {"agent_id": "agent", "task_id": None}
