import asyncio

from schemata.cybergym.intake import A2ATaskSource, infer_label, infer_level


def test_infer_level_by_attachments():
    assert infer_level({"repo-vul.tar.gz"}) == "level0"
    assert infer_level({"repo-vul.tar.gz", "description.txt"}) == "level1"
    assert infer_level({"repo-vul.tar.gz", "description.txt", "error.txt"}) == "level2"
    assert infer_level({"repo-vul.tar.gz", "repo-fix.tar.gz", "error.txt",
                        "description.txt", "patch.diff"}) == "level3"


def test_infer_label_from_text_or_files():
    assert infer_label("solve arvo:10400 please", {}) == "arvo:10400"
    assert infer_label("no id here", {"oss-fuzz:42535201.txt": b""}) == "oss-fuzz:42535201"
    assert infer_label("nothing", {"repo-vul.tar.gz": b""}) == "unknown"


def test_a2a_task_source_writes_files(tmp_path):
    files = {"repo-vul.tar.gz": b"VULSRC", "description.txt": b"a heap overflow in foo()"}
    handle = asyncio.run(A2ATaskSource(files, "task arvo:10400").materialize(tmp_path))
    assert handle.level == "level1"
    assert handle.label == "arvo:10400"
    assert (handle.task_dir / "repo-vul.tar.gz").read_bytes() == b"VULSRC"
    assert (handle.task_dir / "description.txt").exists()
    # AgentBeats mode has no submit.sh identity fields
    assert handle.masked_id is None and handle.checksum is None
