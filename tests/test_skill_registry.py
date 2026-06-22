"""Tests for the skill registry: frontmatter parsing, selection, and assembly."""
from __future__ import annotations

from pathlib import Path

import pytest

from schemata.knowledge.skill_registry import (
    SkillRegistry,
    SkillSelector,
    _parse_frontmatter,
)


@pytest.fixture
def skills_dir(tmp_path):
    tools = tmp_path / "tools"
    tools.mkdir()
    agents = tmp_path / "agents"
    agents.mkdir()

    (tools / "alpha.md").write_text(
        "---\n"
        "name: alpha_tool\n"
        "description: Alpha tool for testing\n"
        "type: tool\n"
        "availability: always\n"
        "triggers: [no_crash, wrong_sink]\n"
        "token_cost: low\n"
        "---\n"
        "<tool_skill>Alpha body content</tool_skill>\n"
    )
    (tools / "beta.md").write_text(
        "---\n"
        "name: beta_instrument\n"
        "description: Beta instrument-only tool\n"
        "type: tool\n"
        "availability: instrument_container\n"
        "requires_tools: [gdb_script]\n"
        "triggers: [no_crash, coverage_unknown]\n"
        "token_cost: medium\n"
        "---\n"
        "<tool_skill>Beta body with gdb</tool_skill>\n"
    )
    (tools / "gamma.md").write_text(
        "---\n"
        "name: gamma_pkg\n"
        "description: Gamma requires missing package\n"
        "type: tool\n"
        "availability: always\n"
        "required_package: nonexistent_package_xyz\n"
        "---\n"
        "<tool_skill>Gamma body</tool_skill>\n"
    )
    (agents / "reader.md").write_text(
        "---\n"
        "name: harness-reader\n"
        "description: Read fuzz harness\n"
        "type: agent\n"
        "stage: recon\n"
        "model: haiku\n"
        "tools: [read_file, grep, glob, bash]\n"
        "permission_tier: read_only\n"
        "---\n"
        "# Harness Reader body\n"
    )
    # A file without frontmatter should be ignored by the registry
    (tools / "no_frontmatter.md").write_text("# Just a plain markdown file\n")
    return tmp_path


def test_parse_frontmatter_valid(tmp_path):
    p = tmp_path / "test.md"
    p.write_text("---\nname: test\ntype: tool\n---\nbody\n")
    fm = _parse_frontmatter(p)
    assert fm is not None
    assert fm["name"] == "test"
    assert fm["type"] == "tool"


def test_parse_frontmatter_list(tmp_path):
    p = tmp_path / "test.md"
    p.write_text("---\nname: test\ntriggers: [a, b, c]\n---\nbody\n")
    fm = _parse_frontmatter(p)
    assert fm["triggers"] == ["a", "b", "c"]


def test_parse_frontmatter_bool(tmp_path):
    p = tmp_path / "test.md"
    p.write_text("---\nname: test\nenabled: true\ndisabled: false\n---\nbody\n")
    fm = _parse_frontmatter(p)
    assert fm["enabled"] is True
    assert fm["disabled"] is False


def test_parse_frontmatter_null(tmp_path):
    p = tmp_path / "test.md"
    p.write_text("---\nname: test\nrequired_package: ~\n---\nbody\n")
    fm = _parse_frontmatter(p)
    assert fm["required_package"] is None


def test_parse_frontmatter_missing(tmp_path):
    p = tmp_path / "test.md"
    p.write_text("# No frontmatter\n")
    assert _parse_frontmatter(p) is None


def test_registry_scans_tools_and_agents(skills_dir):
    reg = SkillRegistry(skills_dir)
    assert reg.get("alpha_tool") is not None
    assert reg.get("beta_instrument") is not None
    assert reg.get("gamma_pkg") is not None
    assert reg.get("harness-reader") is not None
    # File without frontmatter is ignored
    assert reg.get("no_frontmatter") is None


def test_registry_by_type(skills_dir):
    reg = SkillRegistry(skills_dir)
    tools = reg.tools()
    agents = reg.agents()
    assert len(tools) == 3
    assert len(agents) == 1
    assert agents[0].name == "harness-reader"
    assert agents[0].stage == "recon"
    assert agents[0].model == "haiku"


def test_registry_body_strips_frontmatter(skills_dir):
    reg = SkillRegistry(skills_dir)
    body = reg.body("alpha_tool")
    assert "---" not in body
    assert "name: alpha_tool" not in body
    assert "<tool_skill>Alpha body content</tool_skill>" in body


def test_registry_menu(skills_dir):
    reg = SkillRegistry(skills_dir)
    menu = reg.menu()
    assert "alpha_tool" in menu
    assert "beta_instrument" in menu
    assert "[requires: instrument_container]" in menu


def test_selector_always_available(skills_dir):
    reg = SkillRegistry(skills_dir)
    sel = SkillSelector(reg)
    skills = sel.select_tool_skills(has_instrument=False)
    names = [s.name for s in skills]
    assert "alpha_tool" in names
    assert "beta_instrument" not in names  # needs instrument
    assert "gamma_pkg" not in names  # missing package


def test_selector_with_instrument(skills_dir):
    reg = SkillRegistry(skills_dir)
    sel = SkillSelector(reg)
    skills = sel.select_tool_skills(has_instrument=True)
    names = [s.name for s in skills]
    assert "alpha_tool" in names
    assert "beta_instrument" in names
    assert "gamma_pkg" not in names


def test_selector_not_in_recon(skills_dir):
    reg = SkillRegistry(skills_dir)
    sel = SkillSelector(reg)
    skills = sel.select_tool_skills(stage="recon")
    assert len(skills) == 0


def test_selector_assemble_tool_advice(skills_dir):
    reg = SkillRegistry(skills_dir)
    sel = SkillSelector(reg)
    advice = sel.assemble_tool_advice(has_instrument=True)
    assert "Alpha body content" in advice
    assert "Beta body with gdb" in advice
    assert "Gamma body" not in advice  # missing package


def test_selector_agent_lookup(skills_dir):
    reg = SkillRegistry(skills_dir)
    sel = SkillSelector(reg)
    agent = sel.select_agent("recon")
    assert agent is not None
    assert agent.name == "harness-reader"
    assert sel.select_agent("nonexistent") is None


def test_real_skills_dir():
    """Smoke test against the actual skills/ directory."""
    reg = SkillRegistry()
    tools = reg.tools()
    agents = reg.agents()
    assert len(tools) >= 4, f"Expected >= 4 tool skills, got {len(tools)}: {[t.name for t in tools]}"
    assert len(agents) >= 4, f"Expected >= 4 agent roles, got {len(agents)}: {[a.name for a in agents]}"
    # Verify frontmatter was parsed correctly for known skills
    construct = reg.get("construct_format_builder")
    assert construct is not None
    assert construct.availability == "always"
    assert construct.required_package == "construct"
    gdb = reg.get("gdb_dynamic_analysis")
    assert gdb is not None
    assert gdb.availability == "instrument_container"
    assert "gdb_script" in gdb.requires_tools
