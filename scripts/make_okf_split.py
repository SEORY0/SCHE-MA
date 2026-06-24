#!/usr/bin/env python3
"""Build the CyberGym task list and a stratified train/eval split for the OKF pipeline.

Outputs (under data/):
  cybergym_1507.txt   — every task id, one per line
  okf_split.json      — {"train":[...], "eval":[...], "pilot":{"train":[...],"eval":[...]}, "meta":{...}}

The split is stratified by (source, language, vuln-family) so train and eval cover the
same distribution, and is DETERMINISTIC (fixed seed + sorted ids) for reproducibility.
The eval set is held out — it must never be mined by the solve/distill pipeline.
"""
from __future__ import annotations

import json
import random
from collections import defaultdict
from pathlib import Path

TASKS_JSON = Path("/home/nsd/cybergym/cybergym_tmp/tasks.json")
DATA = Path(__file__).resolve().parent.parent / "data"

SEED = 20260624
EVAL_FRAC = 0.20
PILOT_TRAIN = 120
PILOT_EVAL = 30

# Coarse vuln-family buckets for stratification (keyword scan of the description).
# Order matters: first match wins. "other" catches the rest.
_FAMILY_KEYWORDS = [
    ("uaf",          ["use-after-free", "use after free", "double-free", "double free"]),
    ("uninit",       ["uninitialized", "msan", "uninit"]),
    ("overflow-write", ["buffer overflow", "heap overflow", "overflow occurs", "out-of-bounds write",
                        "oob write", "stack overflow", "writes", "global-buffer-overflow"]),
    ("overflow-read", ["over-read", "overread", "out-of-bounds read", "oob read", "buffer over-read",
                       "reads past", "heap-buffer-overflow"]),
    ("oob-index",    ["index", "out of bounds", "out-of-bounds", "bounds"]),
    ("integer",      ["integer overflow", "integer", "signed", "unsigned", "wraparound", "truncat"]),
    ("null-deref",   ["null pointer", "null-deref", "nullptr", "segv on unknown", "null dereference"]),
    ("assert-abort", ["assertion", "abort", "ubsan", "divide by zero", "division by zero"]),
]


def vuln_family(desc: str) -> str:
    d = (desc or "").lower()
    for fam, kws in _FAMILY_KEYWORDS:
        if any(k in d for k in kws):
            return fam
    return "other"


def stratum(task: dict) -> tuple[str, str, str]:
    src = task["task_id"].split(":")[0]
    lang = task.get("project_language") or "unknown"
    fam = vuln_family(task.get("vulnerability_description", ""))
    return (src, lang, fam)


def main() -> None:
    tasks = json.loads(TASKS_JSON.read_text())
    tasks.sort(key=lambda t: t["task_id"])               # deterministic order
    rng = random.Random(SEED)

    DATA.mkdir(exist_ok=True)
    (DATA / "cybergym_1507.txt").write_text("\n".join(t["task_id"] for t in tasks) + "\n")

    # group by stratum, split each group ~80/20
    by_stratum: dict[tuple, list[str]] = defaultdict(list)
    for t in tasks:
        by_stratum[stratum(t)].append(t["task_id"])

    train: list[str] = []
    eval_: list[str] = []
    for st, ids in sorted(by_stratum.items()):
        ids = sorted(ids)
        rng.shuffle(ids)
        n_eval = max(1, round(len(ids) * EVAL_FRAC)) if len(ids) > 1 else 0
        eval_.extend(ids[:n_eval])
        train.extend(ids[n_eval:])
    train.sort()
    eval_.sort()

    # stratified pilot: sample proportionally from train/eval strata
    def stratified_sample(pool: list[str], k: int) -> list[str]:
        idmap = {t["task_id"]: t for t in tasks}
        groups: dict[tuple, list[str]] = defaultdict(list)
        for tid in pool:
            groups[stratum(idmap[tid])].append(tid)
        picked: list[str] = []
        rng2 = random.Random(SEED + 1)
        items = sorted(groups.items())
        # proportional allocation
        for st, ids in items:
            ids = sorted(ids); rng2.shuffle(ids)
            take = max(1, round(k * len(ids) / len(pool)))
            picked.extend(ids[:take])
        rng2.shuffle(picked)
        return sorted(picked[:k])

    pilot_train = stratified_sample(train, PILOT_TRAIN)
    pilot_eval = stratified_sample(eval_, PILOT_EVAL)

    split = {
        "meta": {
            "seed": SEED, "eval_frac": EVAL_FRAC, "total": len(tasks),
            "n_train": len(train), "n_eval": len(eval_),
            "pilot_train": len(pilot_train), "pilot_eval": len(pilot_eval),
            "note": "eval is held out — never mine it in solve/distill.",
        },
        "train": train,
        "eval": eval_,
        "pilot": {"train": pilot_train, "eval": pilot_eval},
    }
    (DATA / "okf_split.json").write_text(json.dumps(split, indent=2))

    # report
    from collections import Counter
    print(f"total={len(tasks)} train={len(train)} eval={len(eval_)} "
          f"pilot_train={len(pilot_train)} pilot_eval={len(pilot_eval)}")
    print("eval source:", Counter(t.split(':')[0] for t in eval_))
    idmap = {t["task_id"]: t for t in tasks}
    print("pilot_train families:", Counter(vuln_family(idmap[t]['vulnerability_description']) for t in pilot_train))
    # leakage guard: train/eval disjoint
    assert not (set(train) & set(eval_)), "train/eval overlap!"
    assert not (set(pilot_eval) & set(train)), "pilot_eval leaks into train!"
    print("disjoint OK")


if __name__ == "__main__":
    main()
