"""
S8 ablation harness — one command emits the ablation table.

Usage (from backend/):
    uv run python eval/run_ablation.py [--dry-run]

Starts a server subprocess per config, runs all 18 cases via HTTP, scores with
LLM-as-judge, saves eval/ablation_results.json, prints the table.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests

# Ensure backend/ is on path so `from eval.judge import judge` and `from app...` work.
_BACKEND = Path(__file__).parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

DATASET_PATH = Path(__file__).parent / "dataset" / "ablation_cases.json"
RESULTS_PATH = Path(__file__).parent / "ablation_results.json"
SEED_DB = _BACKEND / "data" / "npc.db"
LIGHTRAG_PATH = _BACKEND / "data" / "lightrag"
PORT = 8099
SERVER_TIMEOUT = 60   # seconds to poll /healthz
CASE_TIMEOUT = 90     # seconds per /talk request
CASE_DELAY = 16       # seconds between cases — keeps us under Kira's 4 RPM free limit

CONFIGS = [
    {"name": "baseline",    "EPISODIC_MEMORY": "false", "GROUNDING_GATE": "false", "MEMORY_STREAM": "false", "REFLECTION": "false"},
    {"name": "+gate",       "EPISODIC_MEMORY": "false", "GROUNDING_GATE": "true",  "MEMORY_STREAM": "false", "REFLECTION": "false"},
    {"name": "+episodic",   "EPISODIC_MEMORY": "true",  "GROUNDING_GATE": "true",  "MEMORY_STREAM": "false", "REFLECTION": "false"},
    {"name": "+stream",     "EPISODIC_MEMORY": "true",  "GROUNDING_GATE": "true",  "MEMORY_STREAM": "true",  "REFLECTION": "false"},
    {"name": "+reflection", "EPISODIC_MEMORY": "true",  "GROUNDING_GATE": "true",  "MEMORY_STREAM": "true",  "REFLECTION": "true"},
]


def load_cases() -> list[dict]:
    with open(DATASET_PATH) as f:
        return json.load(f)


def _wait_for_server() -> bool:
    deadline = time.time() + SERVER_TIMEOUT
    while time.time() < deadline:
        try:
            if requests.get(f"http://localhost:{PORT}/healthz", timeout=1).status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(1)
    return False


def _get_state(player_id: str) -> dict:
    try:
        return requests.get(
            f"http://localhost:{PORT}/npc/shopkeeper/state",
            params={"player_id": player_id},
            timeout=10,
        ).json()
    except Exception:
        return {"disposition": 0}


def _run_case(case: dict, config_name: str) -> dict:
    player_id = case["player_id"]

    for msg in case.get("setup", []):
        try:
            requests.post(
                f"http://localhost:{PORT}/npc/shopkeeper/talk",
                json={"player_id": player_id, "message": msg},
                timeout=CASE_TIMEOUT,
            )
        except Exception as exc:
            print(f"    ⚠ setup turn failed: {exc}")

    before = _get_state(player_id)
    try:
        reply = requests.post(
            f"http://localhost:{PORT}/npc/shopkeeper/talk",
            json={"player_id": player_id, "message": case["message"]},
            timeout=CASE_TIMEOUT,
        ).text
    except Exception as exc:
        reply = f"[request failed: {exc}]"
    after = _get_state(player_id)

    return {
        "case_id": case["id"],
        "config": config_name,
        "reply": reply,
        "disposition_delta": after.get("disposition", 0) - before.get("disposition", 0),
    }


def _run_config(config: dict, tmpdir: str, smoke: int = 0, on_result=None) -> list[dict]:
    name = config["name"]
    db_path = Path(tmpdir) / "npc.db"
    shutil.copy(SEED_DB, db_path)

    env = {
        **os.environ,
        **{k: v for k, v in config.items() if k != "name"},
        "DB_PATH": str(db_path),
        "CHROMA_PATH": str(Path(tmpdir) / "chroma"),
        "CHECKPOINT_PATH": str(Path(tmpdir) / "checkpoints.db"),
        "LIGHTRAG_PATH": str(LIGHTRAG_PATH),
        "LANGCHAIN_TRACING_V2": "false",  # don't pollute traces during ablation
    }

    log_path = Path(tempfile.gettempdir()) / f"abl_{name.replace('+','p')}.log"
    print(f"\n[{name}] starting server (log: {log_path})...", flush=True)
    log_fh = open(log_path, "w")
    proc = subprocess.Popen(
        ["uv", "run", "uvicorn", "app.main:app", f"--port={PORT}"],
        env=env,
        cwd=_BACKEND,
        stdout=log_fh,
        stderr=log_fh,
    )

    results = []
    try:
        if not _wait_for_server():
            print(f"  ⚠ server failed to start for [{name}]")
            return results

        cases = load_cases()
        if smoke:
            cases = cases[:smoke]

        print(f"[{name}] ready — running {len(cases)} cases...", flush=True)
        for case in cases:
            print(f"  {case['id']}...", end=" ", flush=True)
            results.append(_run_case(case, name))
            print("done", flush=True)
            if on_result:
                on_result(results[-1])
            time.sleep(CASE_DELAY)
    finally:
        proc.terminate()
        proc.wait(timeout=10)
        log_fh.close()
        print(f"[{name}] server stopped.", flush=True)

    return results


def _score(all_results: list[dict], cases: list[dict]) -> dict:
    from concurrent.futures import ThreadPoolExecutor, as_completed

    try:
        from eval.judge_local import judge
    except ImportError:
        from eval.judge import judge  # type: ignore[assignment]

    by_id = {c["id"]: c for c in cases}

    def _judge_one(r: dict):
        return r["config"], r["case_id"], judge(by_id[r["case_id"]], r["reply"], r["disposition_delta"])

    scores: dict = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(_judge_one, r): r for r in all_results}
        done = 0
        for f in as_completed(futures):
            config, case_id, s = f.result()
            scores.setdefault(config, {})[case_id] = s
            done += 1
            print(f"  scored {done}/{len(all_results)}", end="\r", flush=True)
    print()
    return scores


def _avg(vals: list) -> str:
    vals = [v for v in vals if v is not None]
    return f"{sum(vals)/len(vals):.1f}/3" if vals else " N/A "


def _print_table(scores: dict, cases: list[dict]) -> None:
    by_type: dict[str, list[str]] = {}
    for c in cases:
        by_type.setdefault(c["type"], []).append(c["id"])

    header = f"{'Config':<14} {'Persona':>9} {'Lore acc':>9} {'Tool acc':>9} {'Memory':>9} {'Overall':>9}"
    sep = "-" * len(header)
    print(f"\n{sep}\n{header}\n{sep}")

    for cfg in CONFIGS:
        name = cfg["name"]
        cs = scores.get(name, {})
        persona = _avg([cs.get(i, {}).get("persona") for i in by_type.get("persona", [])])
        lore    = _avg([cs.get(i, {}).get("lore")    for i in by_type.get("lore", [])])
        tool    = _avg([cs.get(i, {}).get("tool")    for i in by_type.get("tool", [])])
        memory  = _avg([cs.get(i, {}).get("persona") for i in by_type.get("memory", [])])
        all_v   = [v for i, s in cs.items() for v in [s.get("persona"), s.get("lore"), s.get("tool")] if v is not None]
        overall = _avg(all_v)
        print(f"{name:<14} {persona:>9} {lore:>9} {tool:>9} {memory:>9} {overall:>9}")

    print(sep)


def _smoke_n() -> int:
    """Return --smoke N value if set, else 0 (full run)."""
    for i, arg in enumerate(sys.argv[:-1]):
        if arg == "--smoke":
            try:
                return int(sys.argv[i + 1])
            except (ValueError, IndexError):
                pass
    return 0


def main() -> None:
    if "--score-only" in sys.argv:
        cases = load_cases()
        all_results = json.loads(RESULTS_PATH.read_text())
        print(f"Scoring {len(all_results)} cached results with LLM judge...", flush=True)
        scores = _score(all_results, cases)
        _print_table(scores, cases)
        return

    if "--dry-run" in sys.argv:
        cases = load_cases()
        print(f"Dry run — {len(cases)} cases × {len(CONFIGS)} configs = {len(cases)*len(CONFIGS)} total turns\n")
        for c in cases:
            print(f"  [{c['id']}] {c['type']:7} player={c['player_id']:12} setup={len(c['setup'])} | {c['message'][:65]}")
        return

    smoke = _smoke_n()
    if smoke:
        print(f"Smoke mode: running first {smoke} cases per config ({smoke * len(CONFIGS)} total turns)")

    replace = "--replace" in sys.argv

    # Merge by default: keep results for configs not being re-run.
    # Pass --replace to start fresh.
    existing: list[dict] = []
    if not replace and RESULTS_PATH.exists():
        try:
            existing = json.loads(RESULTS_PATH.read_text())
        except Exception:
            pass

    cached_names = {r["config"] for r in existing} if not replace else set()
    kept = [r for r in existing if r["config"] in cached_names]

    new_results: list[dict] = []

    def _save(result: dict) -> None:
        new_results.append(result)
        RESULTS_PATH.write_text(json.dumps(kept + new_results, indent=2))

    for config in CONFIGS:
        if config["name"] in cached_names:
            print(f"\n[{config['name']}] cached — skipping (use --replace to re-run)")
            continue
        with tempfile.TemporaryDirectory() as tmpdir:
            _run_config(config, tmpdir, smoke=smoke, on_result=_save)

    all_results = kept + new_results
    RESULTS_PATH.write_text(json.dumps(all_results, indent=2))
    print(f"\nRaw results → {RESULTS_PATH}")

    print("Scoring with LLM judge...", flush=True)
    cases = load_cases()
    scores = _score(all_results, cases)
    _print_table(scores, cases)


if __name__ == "__main__":
    main()
