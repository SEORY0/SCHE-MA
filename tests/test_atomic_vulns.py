from schemata import atomic_vulns as av


def test_library_has_28_types():
    lib = av.load()
    assert len(lib) == 28
    for tid, e in lib.items():
        assert {"label", "sanitizer", "sink", "recipe", "byte_example", "fp_guard"} <= set(e)
        assert e["byte_example"].strip()  # non-empty illustrative schematic for every type


def test_classify_exact_variant():
    assert av.classify_from_crash_type("Heap-buffer-overflow READ 1") == ["heap-buffer-overflow-read"]
    assert av.classify_from_crash_type("Stack-buffer-overflow WRITE 1") == ["stack-buffer-overflow-write"]
    assert av.classify_from_crash_type("Use-of-uninitialized-value") == ["use-of-uninitialized-value"]
    assert av.classify_from_crash_type("Container-overflow READ") == ["container-overflow-read"]


def test_bare_family_matches_both_variants():
    got = set(av.classify_from_crash_type("heap-buffer-overflow"))
    assert got == {"heap-buffer-overflow-read", "heap-buffer-overflow-write"}


def test_write_crash_does_not_pull_read_or_dynamic():
    got = av.classify_from_crash_type("Stack-buffer-overflow WRITE")
    assert got == ["stack-buffer-overflow-write"]            # not -read, not dynamic-*
    got2 = av.classify_from_crash_type("Heap-buffer-overflow WRITE")
    assert got2 == ["heap-buffer-overflow-write"]            # not -read


def test_verbose_segv_with_address_suffix():
    # ASan appends a hex address; the specific alias must still match.
    assert "wild-address-read" in av.classify_from_crash_type("SEGV on unknown address 0x602000000010")
    assert "null-dereference-read" in av.classify_from_crash_type("SEGV on unknown address 0x000000000000")


def test_unknown_returns_empty():
    assert av.classify_from_crash_type("totally-made-up") == []
    assert av.classify_from_crash_type("") == []


def test_retrieve_renders_only_requested_and_dedupes():
    out = av.retrieve(["heap-buffer-overflow-read", "heap-buffer-overflow-read", "bogus-id"])
    assert "Heap-buffer-overflow READ" in out
    assert out.count("### ") == 1                            # deduped, bogus dropped
    assert "Example(V_i):" in out and "score 0" in out
    assert "byte_example" in out and "read_index=N" in out   # illustrative bytes rendered
    assert av.retrieve([]) == "" and av.retrieve(None) == ""


def test_menu_lists_all_28():
    assert len(av.menu().splitlines()) == 28


def test_prompt_loader_injects_examples_and_menu(tmp_path):
    from types import SimpleNamespace

    from schemata import prompt_loader
    from schemata.config import load_settings
    from schemata.models import PipelinePlan, TaskMeta

    (tmp_path / "description.txt").write_text("heap-buffer-overflow in ReadImage")
    handle = SimpleNamespace(task_dir=tmp_path, masked_id=None, agent_id=None,
                             checksum=None, server_url=None)
    plan = PipelinePlan(difficulty="medium", stages=["recon", "analyze", "generate"],
                        stage_models={"recon": "haiku", "analyze": "sonnet", "generate": "opus"})
    meta = TaskMeta(task_id="arvo:1", crash_type="Heap-buffer-overflow READ")
    s = load_settings()

    # generate: the classified type's Example(V_i) is injected
    prior = {"recon": {"vuln_classes": ["heap-buffer-overflow-read"]}}
    gen = prompt_loader.build_request("generate", plan, meta, handle, prior, s, "claude_api")
    assert "Heap-buffer-overflow READ" in gen.system_prompt and "Example(V_i)" in gen.system_prompt
    assert "Container-overflow" not in gen.system_prompt  # only the matched type, not all 28

    # recon: the classification menu (all 28) is present
    rec = prompt_loader.build_request("recon", plan, meta, handle, {}, s, "claude_api")
    assert "heap-buffer-overflow-read: Heap-buffer-overflow READ" in rec.system_prompt
