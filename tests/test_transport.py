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


def test_counters_track_calls_crashes_and_timeouts(tmp_path):
    # Health counters back the brain's METRICS line: a timeout returns None and is NOT
    # recorded as a submission, so without n_timeouts a dropped green reply is invisible.
    poc = tmp_path / "poc"
    poc.write_bytes(b"\x00")
    q: asyncio.Queue = asyncio.Queue()

    async def emit_test(_b):
        pass

    t = A2AGreenSubmit(emit_test, q, timeout=1)

    async def go():
        await q.put({"exit_code": 1, "output": "boom"})
        v1 = await t.submit(poc)   # green replies with a crash
        v2 = await t.submit(poc)   # no reply queued -> timeout
        return v1, v2

    v1, v2 = _run(go())
    assert v1 is not None and v1.crashed is True
    assert v2 is None
    assert t.n_calls == 2
    assert t.n_crashes == 1
    assert t.n_timeouts == 1
