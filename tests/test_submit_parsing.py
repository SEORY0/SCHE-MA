from schemata.models import Verdict
from schemata.util import extract_last_json
from schemata.backends.base import cost_of, alias_of
from schemata.models import Usage


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
