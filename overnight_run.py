"""
overnight_run.py — Overnight multi-profile training rotation.

Rotates through coding, general, and research profiles (5 cycles each).
Each profile has a 1-hour timeout. Calls pk_sync at the end.

Usage:
    python overnight_run.py
    python overnight_run.py --cycles-per-profile 10
"""

import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import pk_sync

PROFILES = ["coding", "general", "research"]
CYCLES_PER_PROFILE = 5
TIMEOUT_SECONDS = 3600  # 1 hour per profile

SCRIPT_DIR = Path(__file__).parent
CYCLE_SCRIPT = SCRIPT_DIR / "bifrost_cycle.py"


def run_profile(profile: str, cycles: int, timeout: int) -> dict:
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

    print("BIFROST Overnight Training Run")
    print(f"  Profiles: {PROFILES}")
    print(f"  Cycles per profile: {args.cycles_per_profile}")
    print(f"  Timeout per profile: {args.timeout}s")
    print(f"  Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    results = []
    for profile in PROFILES:
        result = run_profile(profile, args.cycles_per_profile, args.timeout)
        results.append(result)
        print(f"\n  >> {profile}: rc={result['returncode']} "
              f"elapsed={result['elapsed_s']}s "
              f"{'TIMEOUT' if result['timed_out'] else 'OK'}")

    # Sync reports to pk-upload
    print("\n\n--- pk_sync ---")
    try:
        pk_sync.sync()
    except Exception as e:
        print(f"  pk_sync failed: {e}")

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
