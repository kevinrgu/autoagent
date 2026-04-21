"""
overnight_run.py — Overnight multi-profile training rotation.

Rotates through coding, general, and research profiles (5 cycles each).
Each profile has a 1-hour timeout. Calls pk_sync at the end.

Usage:
    python overnight_run.py
    python overnight_run.py --cycles-per-profile 10
"""

import json
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import httpx
import pk_sync

PROFILES = ["coding", "general", "research"]
CYCLES_PER_PROFILE = 5
TIMEOUT_SECONDS = 3600  # 1 hour per profile

SCRIPT_DIR = Path(__file__).parent
CYCLE_SCRIPT = SCRIPT_DIR / "bifrost_cycle.py"
PROFILES_JSON = SCRIPT_DIR / "profiles.json"
COOLDOWN_SECONDS = 30


def keep_alive_loop(endpoints: list[tuple[str, str]], stop_event: threading.Event):
    """Ping endpoints every 60s to prevent Ollama model unloading."""
    while not stop_event.is_set():
        for url, model in endpoints:
            try:
                httpx.post(
                    url,
                    json={"model": model, "messages": [{"role": "user", "content": "keepalive"}], "max_tokens": 1},
                    timeout=30,
                )
            except Exception:
                pass
        stop_event.wait(60)


def collect_endpoints() -> list[tuple[str, str]]:
    """Gather unique (url, model) tuples from all profiles."""
    try:
        data = json.loads(PROFILES_JSON.read_text(encoding="utf-8"))
    except Exception:
        return []
    seen = set()
    endpoints = []
    for profile in data.get("profiles", {}).values():
        for role in ["proposer", "executor", "evaluator"]:
            cfg = profile.get(role, {})
            pair = (cfg.get("url", ""), cfg.get("model", ""))
            if pair[0] and pair[1] and pair not in seen:
                seen.add(pair)
                endpoints.append(pair)
    return endpoints


def check_worktree_divergence() -> str | None:
    """Check if main is behind other branches. Returns warning string or None."""
    try:
        main_hash = subprocess.run(
            ["git", "log", "--oneline", "-1", "main"],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
        all_latest = subprocess.run(
            ["git", "log", "--all", "--oneline", "-1"],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
        if main_hash and all_latest:
            m_h = main_hash.split()[0]
            a_h = all_latest.split()[0]
            if m_h != a_h:
                return f"WARNING: main ({main_hash}) is behind latest commit ({all_latest})"
    except Exception:
        pass
    return None


def _warmup_proposer(profiles_path: str, profile_name: str, timeout: int = 180) -> bool:
    """Warm up proposer model before cycles. Prevents cold-load PROPOSER_FAIL timeouts."""
    import requests as _req
    import time as _time
    try:
        profiles = json.loads(Path(profiles_path).read_text(encoding="utf-8"))
        prof = profiles["profiles"].get(profile_name, {})
        url   = prof.get("proposer", {}).get("url", "")
        model = prof.get("proposer", {}).get("model", "")
        if not url or not model:
            print(f"  [warmup] no proposer config for {profile_name}, skipping")
            return False
        print(f"  [warmup] pinging {model} (timeout={timeout}s)...")
        t0 = _time.time()
        r = _req.post(url,
            json={"model": model,
                  "messages": [{"role": "user", "content": "Ready?"}],
                  "stream": False, "max_tokens": 5, "keep_alive": "2h"},
            timeout=timeout)
        elapsed = _time.time() - t0
        ok = r.status_code == 200
        print(f"  [warmup] {'warm' if ok else 'failed'} in {elapsed:.1f}s (status={r.status_code})")
        return ok
    except Exception as e:
        print(f"  [warmup] error: {e} -- continuing anyway")
        return False


def warmup_endpoints(profile_name: str):
    """Ping proposer/executor/evaluator to ensure models are loaded before run."""
    try:
        data = json.loads(PROFILES_JSON.read_text(encoding="utf-8"))
        profile_config = data.get("profiles", {}).get(profile_name, {})
    except Exception as e:
        print(f"  warmup: could not read profiles.json: {e}")
        return

    for role in ["proposer", "executor", "evaluator"]:
        cfg = profile_config.get(role, {})
        url = cfg.get("url", "")
        model = cfg.get("model", "")
        if url and model:
            try:
                resp = httpx.post(
                    url,
                    json={"model": model, "messages": [{"role": "user", "content": "ping"}], "max_tokens": 5},
                    timeout=120,
                )
                print(f"  warmup {role} ({model}): {resp.status_code}")
            except Exception as e:
                print(f"  warmup {role} ({model}): FAILED - {e}")


def make_decisions_path(profile_name: str, run_stamp: str) -> Path:
    """Create a timestamped decisions file path for this run."""
    profile_upper = profile_name.upper()
    decisions_file = SCRIPT_DIR / "profiles" / profile_upper / f"decisions_{profile_name}_{run_stamp}.md"
    decisions_file.parent.mkdir(parents=True, exist_ok=True)
    with open(decisions_file, "a", encoding="utf-8") as f:
        f.write(f"# Overnight Run {run_stamp} — /{profile_name}\n")
    return decisions_file


def run_profile(profile: str, cycles: int, timeout: int,
                decisions_path: str | None = None) -> dict:
    """Run bifrost_cycle.py for a given profile with timeout."""
    print(f"\n{'='*60}")
    print(f"  OVERNIGHT: Starting profile={profile} cycles={cycles} timeout={timeout}s")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}\n")

    cmd = [
        sys.executable, str(CYCLE_SCRIPT),
        "--profile", profile,
        "--max-cycles", str(cycles),
    ]
    if decisions_path:
        cmd.extend(["--decisions", decisions_path])

    t0 = time.time()
    try:
        result = subprocess.run(
            cmd,
            timeout=timeout,
            capture_output=False,
            cwd=str(SCRIPT_DIR),
        )
        elapsed = time.time() - t0
        return {
            "profile": profile,
            "returncode": result.returncode,
            "elapsed_s": round(elapsed, 1),
            "timed_out": False,
        }
    except subprocess.TimeoutExpired:
        elapsed = time.time() - t0
        print(f"\n  TIMEOUT: {profile} exceeded {timeout}s limit")
        return {
            "profile": profile,
            "returncode": -1,
            "elapsed_s": round(elapsed, 1),
            "timed_out": True,
        }
    except Exception as e:
        elapsed = time.time() - t0
        print(f"\n  ERROR: {profile} failed: {e}")
        return {
            "profile": profile,
            "returncode": -1,
            "elapsed_s": round(elapsed, 1),
            "timed_out": False,
            "error": str(e),
        }


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Overnight multi-profile training")
    parser.add_argument("--cycles-per-profile", type=int, default=CYCLES_PER_PROFILE)
    parser.add_argument("--timeout", type=int, default=TIMEOUT_SECONDS,
                        help="Timeout per profile in seconds (default: 3600)")
    args = parser.parse_args()

    run_stamp = datetime.now().strftime("%Y%m%d_%H%M")

    print("BIFROST Overnight Training Run")
    print(f"  Profiles: {PROFILES}")
    print(f"  Cycles per profile: {args.cycles_per_profile}")
    print(f"  Timeout per profile: {args.timeout}s")
    print(f"  Run stamp: {run_stamp}")
    print(f"  Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # Worktree divergence guard
    divergence = check_worktree_divergence()
    if divergence:
        print(f"\n  {divergence}")
        log_file = SCRIPT_DIR / "overnight_log.txt"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now().isoformat()} {divergence}\n")

    # Start keep-alive daemon thread
    endpoints = collect_endpoints()
    stop_event = threading.Event()
    if endpoints:
        ka_thread = threading.Thread(target=keep_alive_loop, args=(endpoints, stop_event), daemon=True)
        ka_thread.start()
        print(f"  Keep-alive: pinging {len(endpoints)} endpoints every 60s")

    results = []
    for i, profile in enumerate(PROFILES):
        # Warmup endpoints before each profile
        print(f"\n--- Warming up endpoints for {profile} ---")
        warmup_endpoints(profile)
        _warmup_proposer(
            str(Path(__file__).parent / "profiles.json"),
            profile,
        )

        # Create timestamped decisions file
        decisions_path = str(make_decisions_path(profile, run_stamp))
        print(f"  Decisions: {decisions_path}")

        result = run_profile(profile, args.cycles_per_profile, args.timeout, decisions_path)
        results.append(result)
        print(f"\n  >> {profile}: rc={result['returncode']} "
              f"elapsed={result['elapsed_s']}s "
              f"{'TIMEOUT' if result['timed_out'] else 'OK'}")

        # Cooldown between profiles to let Ollama unload/swap models
        if i < len(PROFILES) - 1:
            print(f"\n  Cooldown {COOLDOWN_SECONDS}s before next profile...")
            time.sleep(COOLDOWN_SECONDS)

    # Stop keep-alive thread
    stop_event.set()

    # Sync reports to pk-upload
    print("\n\n--- pk_sync ---")
    try:
        pk_sync.sync()
    except Exception as e:
        print(f"  pk_sync failed: {e}")

    # Auto-commit accepted changes
    log_lines = []
    BIFROST_ROUTER = r"D:\Projects\bifrost-router"
    AUTOAGENT = str(SCRIPT_DIR)
    try:
        targets = [
            r"D:\Projects\bifrost-router\autopilot_graph.py",
            r"D:\Projects\bifrost-router\subtask_graph.py",
        ]
        for t in targets:
            subprocess.run(["git", "-C", str(Path(t).parent), "add", str(t)],
                           capture_output=True)

        subprocess.run(["git", "add", "-A"], capture_output=True, cwd=AUTOAGENT)

        commit_ts = datetime.now().strftime("%Y-%m-%d %H:%M")

        # Commit bifrost-router if targets changed
        rc = subprocess.run(["git", "diff", "--cached", "--quiet"],
                            capture_output=True, cwd=BIFROST_ROUTER)
        if rc.returncode != 0:
            subprocess.run(
                ["git", "commit", "-m",
                 f"overnight training {commit_ts}: auto-committed accepted changes"],
                capture_output=True, cwd=BIFROST_ROUTER)
            log_lines.append("git commit: bifrost-router (accepted target changes)")

        # Commit autoagent (decisions + logs)
        rc2 = subprocess.run(["git", "diff", "--cached", "--quiet"],
                             capture_output=True, cwd=AUTOAGENT)
        if rc2.returncode != 0:
            subprocess.run(
                ["git", "commit", "-m",
                 f"overnight training {commit_ts}: decisions + logs"],
                capture_output=True, cwd=AUTOAGENT)
            log_lines.append("git commit: autoagent (decisions + logs)")
    except Exception as e:
        log_lines.append(f"git auto-commit failed: {e}")

    if log_lines:
        print("\n--- git auto-commit ---")
        for line in log_lines:
            print(f"  {line}")

    # Write overnight log
    log_file = SCRIPT_DIR / "overnight_log.txt"
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(f"\n# Run {run_stamp} — {datetime.now().isoformat()}\n")
        for r in results:
            status = "TIMEOUT" if r["timed_out"] else ("OK" if r["returncode"] == 0 else "FAIL")
            f.write(f"  {r['profile']:10s} {status:7s} {r['elapsed_s']:8.1f}s\n")
        for line in log_lines:
            f.write(f"  {line}\n")

    # Summary
    print(f"\n{'='*60}")
    print("  OVERNIGHT RUN COMPLETE")
    print(f"  Finished: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    for r in results:
        status = "TIMEOUT" if r["timed_out"] else ("OK" if r["returncode"] == 0 else "FAIL")
        print(f"    {r['profile']:10s} {status:7s} {r['elapsed_s']:8.1f}s")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
