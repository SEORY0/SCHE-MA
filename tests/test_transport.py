import asyncio

from schemata.cybergym.transport import A2AGreenSubmit


def _run(coro):
    return asyncio.run(coro)


def _submit_with_reply(tmp_path, reply, *, emitted):
    poc = tmp_path / "poc"
    poc.write_bytes(b"\x00\x01\x02\x03")
    q: asyncio.Queue = asyncio.Queue()

    async def emit_test(poc_bytes):
        emitted.append(poc_bytes)

    async def go():
        if reply is not None:
            await q.put(reply)
        return await A2AGreenSubmit(emit_test, q, timeout=2).submit(poc)

    return _run(go())


def test_crash_via_exit_code(tmp_path):
    emitted = []
    v = _submit_with_reply(tmp_path, {"exit_code": 1, "output": "boom", "poc_id": "p1"}, emitted=emitted)
    assert v is not None and v.crashed is True and v.exit_code == 1 and v.poc_id == "p1"
    assert emitted and emitted[0] == b"\x00\x01\x02\x03"  # PoC was emitted to the green


def test_crash_via_sanitizer_keyword(tmp_path):
    emitted = []
    v = _submit_with_reply(tmp_path, {"exit_code": 0, "output": "ERROR: AddressSanitizer: heap-buffer-overflow"}, emitted=emitted)
    assert v.crashed is True and v.exit_code == 1  # keyword promotes 0 -> crash


def test_no_crash(tmp_path):
    emitted = []
    v = _submit_with_reply(tmp_path, {"exit_code": 0, "output": "ran cleanly"}, emitted=emitted)
    assert v.crashed is False and v.exit_code == 0


def test_timeout_returns_none(tmp_path):
    emitted = []
    v = _submit_with_reply(tmp_path, None, emitted=emitted)  # no reply queued -> timeout
    assert v is None
