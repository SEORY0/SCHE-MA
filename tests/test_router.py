from schemata.config import load_settings
from schemata.models import TaskMeta
from schemata import router


def _meta(diff):
    return TaskMeta(task_id="arvo:1", project="p", crash_type="x", difficulty_estimate=diff)


def test_easy_skips_analyze_and_uses_sonnet():
    s = load_settings()
    p = router.plan(_meta("easy"), s)
    assert p.stages == ["recon", "generate"]
    assert p.has_instrument is False
    assert p.minimize_info is True
    assert p.stage_models["recon"] == "haiku"
    assert p.stage_models["generate"] == "sonnet"


def test_medium_full_pipeline_cost_routed():
    """Medium tasks: cheapest-model-per-stage routing for cost. analyze on sonnet (3x
    cheaper than opus), generate also on sonnet for medium. Hard escalates to opus."""
    s = load_settings()
    p = router.plan(_meta("medium"), s)
    assert p.stages == ["recon", "analyze", "generate"]
    assert p.has_instrument is True
    assert p.has_mcp_index is False
    assert p.stage_models["recon"] == "haiku"
    assert p.stage_models["analyze"] == "sonnet"
    assert p.stage_models["generate"] == "sonnet"


def test_hard_has_mcp_and_thinking():
    s = load_settings()
    p = router.plan(_meta("hard"), s)
    assert p.has_mcp_index is True
    assert p.thinking is True
    assert p.stage_models["generate"] == "opus"
