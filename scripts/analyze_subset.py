#!/usr/bin/env python3
"""Analyze SCHE-MA run-subset outputs (read-only).

Reads one or more `runs/<id>/` directories, groups them by backend, and computes the
CyberGym subset scorecard: Micro gate (>=5/10), backend comparison, per-task table,
per-difficulty aggregates, cost/cache summary (with a no-cache counterfactual), and a
failure analysis. Emits a markdown report + CSV. Pure stdlib; mirrors backends/base.py
PRICES so the cache-savings math matches the cost tracker. Never mutates run artifacts.

Usage:
  analyze_subset.py --run runs/<API_ID> [--run runs/<CC_ID> ...] \
      [--out-md docs/subset_results.md] [--out-csv docs/subset_results.csv] \
      [--gate 5] [--baseline 4]
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import statistics
from pathlib import Path

# (input, output, cache_read, cache_write_5m) $/MTok — mirror of backends/base.py PRICES
PRICES = {
    "opus":   (5.0, 25.0, 0.50, 6.25),
    "sonnet": (3.0, 15.0, 0.30, 3.75),
    "haiku":  (1.0,  5.0, 0.10, 1.25),
    "gpt5":   (5.0, 30.0, 0.50, 0.0),
}
DIFF_ORDER = {"easy": 0, "medium": 1, "hard": 2, "unknown": 3}


def alias_of(model: str) -> str:
    m = (model or "").lower()
    for a in PRICES:
        if m == a or a in m:
            return a
    return "opus"


# ---------- load ----------

def _read_json(p: Path):
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def load_run(run_dir: str) -> dict:
    R = Path(run_dir)
    cost = _read_json(R / "cost.json")
    summary = _read_json(R / "subset_summary.json")
    entries_by_task: dict[str, list] = {}
    for e in cost.get("entries", []):
        entries_by_task.setdefault(e["task_id"], []).append(e)

    tasks: dict[str, dict] = {}
    for oc_path in glob.glob(str(R / "*" / "outcome.json")):
        d = _read_json(Path(oc_path))
        o = d.get("outcome", {})
        plan = d.get("plan", {})
        tid = o.get("task_id")
        if not tid:
            continue
        tdir = Path(oc_path).parent
        stages = {}
        for sp in glob.glob(str(tdir / "stage_*.json")):
            sd = _read_json(Path(sp))
            key = sd.get("stage") or Path(sp).stem.replace("stage_", "")
            stages[key] = sd
        subs = []
        subf = tdir / "submissions.jsonl"
        if subf.exists():
            for line in subf.read_text().splitlines():
                line = line.strip()
                if line:
                    try:
                        subs.append(json.loads(line))
                    except Exception:
                        pass
        ents = entries_by_task.get(tid, [])
        tok = {
            "input":  sum(int(e.get("input_tokens", 0)) for e in ents),
            "output": sum(int(e.get("output_tokens", 0)) for e in ents),
            "cr":     sum(int(e.get("cache_read_tokens", 0)) for e in ents),
            "cw":     sum(int(e.get("cache_write_tokens", 0)) for e in ents),
        }
        tasks[tid] = {"outcome": o, "plan": plan, "stages": stages, "subs": subs,
                      "ents": ents, "tok": tok, "cost": float(o.get("cost_usd", 0.0))}

    backend = next((t["outcome"].get("backend") for t in tasks.values()), R.name)
    return {"dir": str(R), "backend": backend, "cost": cost, "summary": summary, "tasks": tasks}


# ---------- derived ----------

def cache_hit_pct(tok: dict) -> float:
    denom = tok["input"] + tok["cr"]
    return (100.0 * tok["cr"] / denom) if denom else 0.0


def cost_no_cache(ents: list) -> float:
    total = 0.0
    for e in ents:
        pin, pout, _, _ = PRICES[alias_of(e.get("model"))]
        ins = int(e.get("input_tokens", 0)) + int(e.get("cache_read_tokens", 0)) + int(e.get("cache_write_tokens", 0))
        out = int(e.get("output_tokens", 0))
        total += (ins * pin + out * pout) / 1_000_000.0
    return total


def gen_stop_reason(rec: dict) -> str:
    st = rec["stages"]
    s = st.get("generate") or st.get("analyze") or st.get("recon")
    return (s or {}).get("stop_reason", "(no stage)") if s else "(no stage)"


def crashed_submission(rec: dict) -> bool:
    return any(int(s.get("exit_code", 0)) != 0 for s in rec["subs"])


def likely_cause(rec: dict) -> str:
    o, plan = rec["outcome"], rec["plan"]
    err = o.get("error") or ""
    src = o["task_id"].split(":")[0]
    diff = plan.get("difficulty", "unknown")
    sr = gen_stop_reason(rec)
    crashed = crashed_submission(rec)
    if "gen_task" in err:
        return "데이터/task-gen 실패"
    if "[budget cap hit]" in err:
        return "예산 캡 도달"
    if src == "oss-fuzz" and diff in ("medium", "hard") and not crashed:
        return "oss-fuzz instrument 미지원 폴백"
    if plan.get("has_mcp_index") and not crashed:
        return "MCP indexing 스텁(M4)"
    if sr == "max_turns" and rec["subs"] and not crashed:
        return "PoC 미트리거(턴 소진)"
    if sr in ("early_stop", "completed") and not crashed:
        return "포기/가설 오류"
    if crashed and not o.get("success"):
        return "agent crash했으나 독립확인 실패(confirm/레이트리밋)"
    return sr or "-"


def task_sort_key(tid_rec):
    tid, rec = tid_rec
    return (DIFF_ORDER.get(rec["plan"].get("difficulty", "unknown"), 9), tid)


# ---------- render ----------

def fmt_models(plan: dict) -> str:
    sm = plan.get("stage_models", {})
    return "/".join(sm.get(s, "?") for s in plan.get("stages", []))


def per_task_rows(run: dict) -> list[dict]:
    rows = []
    for tid, rec in sorted(run["tasks"].items(), key=task_sort_key):
        o, plan, tok = rec["outcome"], rec["plan"], rec["tok"]
        rows.append({
            "backend": run["backend"], "task_id": tid, "source": tid.split(":")[0],
            "difficulty": plan.get("difficulty", "?"), "stages": ",".join(plan.get("stages", [])),
            "models": fmt_models(plan), "has_instrument": plan.get("has_instrument", False),
            "thinking": plan.get("thinking", False), "success": o.get("success", False),
            "final_exit_code": o.get("final_exit_code"), "poc_id": o.get("poc_id") or "",
            "stop_reason": gen_stop_reason(rec), "cost_usd": round(rec["cost"], 4),
            "tok_input": tok["input"], "tok_output": tok["output"],
            "tok_cache_read": tok["cr"], "tok_cache_write": tok["cw"],
            "cache_hit_pct": round(cache_hit_pct(tok), 1),
        })
    return rows


def difficulty_table(run: dict) -> list[dict]:
    out = []
    buckets: dict[str, list] = {}
    for tid, rec in run["tasks"].items():
        buckets.setdefault(rec["plan"].get("difficulty", "unknown"), []).append(rec)
    order = sorted(buckets, key=lambda d: DIFF_ORDER.get(d, 9))
    for diff in order + ["ALL"]:
        recs = [r for rs in buckets.values() for r in rs] if diff == "ALL" else buckets[diff]
        n = len(recs)
        ns = sum(1 for r in recs if r["outcome"].get("success"))
        costs = [r["cost"] for r in recs]
        hits = [cache_hit_pct(r["tok"]) for r in recs]
        out.append({
            "difficulty": diff, "n": n, "success": ns,
            "rate": f"{ns}/{n}" + (f" ({100*ns//n}%)" if n else ""),
            "avg_cost": round(statistics.mean(costs), 4) if costs else 0,
            "median_cost": round(statistics.median(costs), 4) if costs else 0,
            "total_cost": round(sum(costs), 4),
            "avg_cache": round(statistics.mean(hits), 1) if hits else 0,
        })
    return out


def model_rollup(run: dict) -> list[dict]:
    agg: dict[str, dict] = {}
    for e in run["cost"].get("entries", []):
        a = alias_of(e.get("model"))
        d = agg.setdefault(a, {"input": 0, "output": 0, "cr": 0, "cw": 0, "cost": 0.0})
        d["input"] += int(e.get("input_tokens", 0))
        d["output"] += int(e.get("output_tokens", 0))
        d["cr"] += int(e.get("cache_read_tokens", 0))
        d["cw"] += int(e.get("cache_write_tokens", 0))
        d["cost"] += float(e.get("cost_usd", 0.0))
    return [{"model": k, **v, "cost": round(v["cost"], 4)} for k, v in
            sorted(agg.items(), key=lambda kv: -kv[1]["cost"])]


def run_totals(run: dict, gate: int) -> dict:
    recs = list(run["tasks"].values())
    n = len(recs)
    passed = sum(1 for r in recs if r["outcome"].get("success"))
    total_cost = run["cost"].get("global_spent_usd") or round(sum(r["cost"] for r in recs), 4)
    no_cache = sum(cost_no_cache(r["ents"]) for r in recs)
    return {
        "backend": run["backend"], "n": n, "passed": passed, "gate_pass": passed >= gate,
        "total_cost": round(total_cost, 4),
        "per_task": round(total_cost / n, 4) if n else 0,
        "per_success": round(total_cost / passed, 4) if passed else None,
        "cost_no_cache": round(no_cache, 4),
        "cache_savings": round(no_cache - total_cost, 4),
        "cache_savings_pct": round(100 * (no_cache - total_cost) / no_cache, 1) if no_cache else 0,
        "avg_cache": round(statistics.mean([cache_hit_pct(r["tok"]) for r in recs]), 1) if recs else 0,
    }


def md_table(headers, rows):
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(c) for c in r) + " |")
    return "\n".join(out)


def render_md(runs, gate, baseline):
    L = ["# SCHE-MA × CyberGym 10-Subset 결과", ""]
    totals = [run_totals(r, gate) for r in runs]

    # headline
    L.append("## Micro 게이트")
    for t in totals:
        verd = "✅ PASS" if t["gate_pass"] else "❌ FAIL"
        delta = t["passed"] - baseline
        L.append(f"- **{t['backend']}**: **{t['passed']}/{t['n']}** crash+poc_id  {verd} (게이트 ≥{gate}) "
                 f"· baseline {baseline}/10 대비 Δ{delta:+d} · 비용 ${t['total_cost']}")
    L.append("")

    # backend comparison
    if len(totals) > 1:
        L.append("## 백엔드 비교")
        L.append(md_table(
            ["backend", "passed", "$total", "$/success", "$/task", "avg cache%", "cache_savings($/%)"],
            [[t["backend"], f"{t['passed']}/{t['n']}", t["total_cost"],
              t["per_success"] if t["per_success"] is not None else "—", t["per_task"],
              f"{t['avg_cache']}%", f"${t['cache_savings']} / {t['cache_savings_pct']}%"] for t in totals]))
        L.append("")

    for run in runs:
        t = run_totals(run, gate)
        L.append(f"## [{run['backend']}] 상세  ({run['dir']})")
        rows = per_task_rows(run)
        L.append("### 태스크별")
        L.append(md_table(
            ["task", "diff", "models", "instr", "think", "success", "exit", "poc_id", "stop", "$", "in", "out", "c_read", "c_write", "hit%"],
            [[r["task_id"], r["difficulty"], r["models"], "Y" if r["has_instrument"] else "-",
              "Y" if r["thinking"] else "-", "✅" if r["success"] else "❌", r["final_exit_code"],
              (r["poc_id"][:10] + "…") if r["poc_id"] else "—", r["stop_reason"], r["cost_usd"],
              r["tok_input"], r["tok_output"], r["tok_cache_read"], r["tok_cache_write"], r["cache_hit_pct"]]
             for r in rows]))
        L.append("")
        L.append("### 난이도별")
        dt = difficulty_table(run)
        L.append(md_table(["diff", "n", "성공", "성공률", "avg$", "median$", "total$", "avg cache%"],
                          [[d["difficulty"], d["n"], d["success"], d["rate"], d["avg_cost"],
                            d["median_cost"], d["total_cost"], f"{d['avg_cache']}%"] for d in dt]))
        L.append("")
        L.append("### 비용 요약")
        L.append(f"- total ${t['total_cost']} · $/task ${t['per_task']} · $/success "
                 f"{('$'+str(t['per_success'])) if t['per_success'] is not None else '—'}")
        L.append(f"- 캐시 미적용 반사실 ${t['cost_no_cache']} → **캐시 절감 ${t['cache_savings']} ({t['cache_savings_pct']}%)**")
        mr = model_rollup(run)
        if mr:
            L.append("")
            L.append(md_table(["model", "input", "output", "cache_read", "cache_write", "$"],
                              [[m["model"], m["input"], m["output"], m["cr"], m["cw"], m["cost"]] for m in mr]))
        L.append("")
        fails = [(tid, rec) for tid, rec in sorted(run["tasks"].items(), key=task_sort_key)
                 if not rec["outcome"].get("success")]
        L.append(f"### 실패 분석 ({len(fails)}건)")
        if fails:
            L.append(md_table(["task", "diff", "stop", "exit(last sub)", "error", "likely_cause"],
                              [[tid, rec["plan"].get("difficulty", "?"), gen_stop_reason(rec),
                                (rec["subs"][-1].get("exit_code") if rec["subs"] else "—"),
                                (rec["outcome"].get("error") or "")[:40], likely_cause(rec)] for tid, rec in fails]))
        else:
            L.append("실패 없음.")
        # stop_reason distribution
        dist = {}
        for rec in run["tasks"].values():
            dist[gen_stop_reason(rec)] = dist.get(gen_stop_reason(rec), 0) + 1
        L.append("")
        L.append("stop_reason 분포: " + ", ".join(f"{k}={v}" for k, v in sorted(dist.items())))
        L.append("")

    L.append("## 베이스라인 대조")
    for t in totals:
        L.append(f"- SCHE-MA [{t['backend']}]: {t['passed']}/{t['n']} · ${t['total_cost']} · "
                 f"$/success {('$'+str(t['per_success'])) if t['per_success'] is not None else '—'}")
    L.append(f"- OpenHands + Opus 4.7 baseline: **{baseline}/10** (비용 n/a)")
    L.append("")
    L.append("## 부록 — run 출처")
    for r in runs:
        L.append(f"- {r['backend']}: {r['dir']} · global_spent_usd ${r['cost'].get('global_spent_usd','?')}")
    return "\n".join(L)


def write_csv(runs, path):
    rows = [row for run in runs for row in per_task_rows(run)]
    if not rows:
        return
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", action="append", required=True, dest="runs")
    ap.add_argument("--out-md", default="docs/subset_results.md")
    ap.add_argument("--out-csv", default="docs/subset_results.csv")
    ap.add_argument("--gate", type=int, default=5)
    ap.add_argument("--baseline", type=int, default=4)
    a = ap.parse_args()

    runs = [load_run(r) for r in a.runs]
    md = render_md(runs, a.gate, a.baseline)
    Path(a.out_md).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out_md).write_text(md)
    write_csv(runs, a.out_csv)

    # consistency assertions (warn, don't fail)
    for run in runs:
        for tid, rec in run["tasks"].items():
            s = sum(float(e.get("cost_usd", 0)) for e in rec["ents"])
            if abs(s - rec["cost"]) > 0.01:
                print(f"WARN cost drift {run['backend']} {tid}: entries ${s:.4f} vs outcome ${rec['cost']:.4f}")

    for t in (run_totals(r, a.gate) for r in runs):
        print(f"GATE [{t['backend']}] {t['passed']}/{t['n']}  {'PASS' if t['gate_pass'] else 'FAIL'}  ${t['total_cost']}")
    print(f"wrote {a.out_md}, {a.out_csv}")


if __name__ == "__main__":
    main()
