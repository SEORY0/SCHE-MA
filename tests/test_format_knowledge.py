from schemata.knowledge import format_knowledge as fk


def test_harness_conventions_loads():
    convs = fk.load_harness_conventions()
    assert "libfuzzer" in convs
    assert "afl" in convs
    assert "custom-main" in convs
    for key, entry in convs.items():
        assert "input_contract" in entry
        assert "poc_shape" in entry


def test_harness_advice_libfuzzer():
    out = fk.harness_advice("libfuzzer")
    assert "harness_convention" in out
    assert "FDP consumption patterns" in out
    assert "ConsumeIntegral" in out


def test_harness_advice_afl():
    out = fk.harness_advice("afl")
    assert "harness_convention" in out
    assert "complete file" in out.lower() or "structurally complete" in out.lower()


def test_harness_advice_unknown():
    assert fk.harness_advice("totally-unknown") == ""
    assert fk.harness_advice(None) == ""
    assert fk.harness_advice("") == ""


def test_format_templates_loads():
    templates = fk.load_format_templates()
    assert len(templates) >= 15
    for key, entry in templates.items():
        assert "structure" in entry
        assert "key_fields" in entry


def test_format_advice_by_project():
    out = fk.format_advice(None, "binutils")
    assert "format_template" in out
    assert "ELF" in out


def test_format_advice_by_input_format():
    out = fk.format_advice("png", None)
    assert "format_template" in out
    assert "raster" in out.lower() or "PNG" in out


def test_format_advice_unknown():
    assert fk.format_advice(None, None) == ""
    assert fk.format_advice("totally-unknown-format", "totally-unknown-project") == ""


def test_project_to_format_coverage():
    for project, fmt_key in fk._PROJECT_TO_FORMAT.items():
        templates = fk.load_format_templates()
        assert fmt_key in templates, f"project {project} maps to {fmt_key} which is not in format_templates"


def test_format_advice_ghostscript():
    out = fk.format_advice(None, "ghostscript")
    assert "PostScript" in out


def test_format_advice_ffmpeg():
    out = fk.format_advice(None, "ffmpeg")
    assert "media" in out.lower() or "container" in out.lower()
