"""
morning_report.py — Post-overnight health check and summary.

Reads overnight_log.txt, counts accepted/rejected decisions per profile,
syntax-checks key graph files, writes MORNING_REPORT.md, and runs pk_sync.

Usage:
    python morning_report.py
"""

import re
import sys
import subprocess
from datetime import datetime
from pathlib import Path

AUTOAGENT_DIR = Path(r"C:\Users\jhpri\projects\autoagent")
BIFROST_DIR = Path(r"D:\Projects\bifrost-router")

OVERNIGHT_LOG = AUTOAGENT_DIR / "overnight_log.txt"
REPORT_OUT = AUTOAGENT_DIR / "MORNING_REPORT.md"

PROFILES = ["CODING", "GENERAL", "RESEARCH"]

SYNTAX_CHECK_FILES = [
    AUTOAGENT_DIR.parent / "bifrost-platform" / "autopilot_graph.py",
    BIFROST_DIR / "subtask_graph.py",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def read_overnight_log() -> str:
    if OVERNIGHT_LOG.exists():
        return OVERNIGHT_LOG.read_text(encoding="utf-8", errors="replace")
    return ""


def count_decisions(profile: str) -> dict:
    """Count Accepted YES/NO in profiles/<PROFILE>/decisions_*.md files."""
    profile_dir = AUTOAGENT_DIR / "profiles" / profile
    accepted = 0
    rejected = 0
    files_found = []

    for md_file in sorted(profile_dir.glob("decisions_*.md")):
        files_found.append(md_file.name)
        text = md_file.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r"\*\*Accepted:\*\*\s*(YES|NO)", text, re.IGNORECASE):
            if m.group(1).upper() == "YES":
                accepted += 1
            else:
                rejected += 1

    return {
        "profile": profile,
        "accepted": accepted,
        "rejected": rejected,
        "total": accepted + rejected,
        "files": files_found,
    }


def check_syntax(path: Path) -> tuple[bool, str]:
    """Return (ok, message) for py_compile of path."""
    if not path.exists():
        return False, f"NOT FOUND: {path}"
    result = subprocess.run(
        [sys.executable, "-m", "py_compile", str(path)],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        return True, f"OK: {path.name}"
    else:
        err = (result.stderr or result.stdout).strip()
        return False, f"SYNTAX ERROR in {path.name}: {err}"


def extract_overnight_summary(log_text: str) -> str:
    """Pull the OVERNIGHT RUN COMPLETE block from the log, if present."""
    if not log_text:
        return "(overnight_log.txt not found — task may write to stdout only)"
    marker = "OVERNIGHT RUN COMPLETE"
    idx = log_text.rfind(marker)
    if idx == -1:
        # Return last 40 lines as a fallback
        lines = log_text.splitlines()
        tail = lines[-40:] if len(lines) > 40 else lines
        return "\n".join(tail)
    # Include from the preceding === line
    start = log_text.rfind("=" * 10, 0, idx)
    if start == -1:
        start = idx
    return log_text[start:].strip()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"morning_report.py — {now}")

    # 1. Read overnight log
    log_text = read_overnight_log()
    overnight_summary = extract_overnight_summary(log_text)

    # 2. Count decisions per profile
    decision_rows = [count_decisions(p) for p in PROFILES]

    # 3. Syntax checks
    syntax_results = [check_syntax(f) for f in SYNTAX_CHECK_FILES]
    all_syntax_ok = all(ok for ok, _ in syntax_results)

    # 4. Overall status
    total_accepted = sum(r["accepted"] for r in decision_rows)
    total_rejected = sum(r["rejected"] for r in decision_rows)
    total_cycles = total_accepted + total_rejected
    accept_rate = (total_accepted / total_cycles * 100) if total_cycles > 0 else 0.0

    status = "READY" if all_syntax_ok else "NEEDS ATTENTION"

    # 5. Write report
    lines = [
        f"# BIFROST Morning Report",
        f"",
        f"**Generated:** {now}  ",
        f"**Status:** {status}",
        f"",
        f"---",
        f"",
        f"## Overnight Run Summary",
        f"",
        f"```",
        overnight_summary,
        f"```",
        f"",
        f"---",
        f"",
        f"## Decision Counts by Profile",
        f"",
        f"| Profile | Accepted | Rejected | Total | Accept Rate |",
        f"|---------|----------|----------|-------|-------------|",
    ]
    for r in decision_rows:
        rate = (r["accepted"] / r["total"] * 100) if r["total"] > 0 else 0.0
        lines.append(
            f"| {r['profile']:8s} | {r['accepted']:8d} | {r['rejected']:8d} | "
            f"{r['total']:5d} | {rate:10.1f}% |"
        )
    lines += [
        f"| **TOTAL** | {total_accepted:8d} | {total_rejected:8d} | "
        f"{total_cycles:5d} | {accept_rate:10.1f}% |",
        f"",
        f"---",
        f"",
        f"## Syntax Checks",
        f"",
    ]
    for ok, msg in syntax_results:
        icon = "✓" if ok else "✗"
        lines.append(f"- {icon} {msg}")
    lines += [
        f"",
        f"---",
        f"",
        f"## Raw Decisions Files Found",
        f"",
    ]
    for r in decision_rows:
        files_str = ", ".join(r["files"]) if r["files"] else "(none)"
        lines.append(f"- **{r['profile']}**: {files_str}")

    report_text = "\n".join(lines) + "\n"
    REPORT_OUT.write_text(report_text, encoding="utf-8")
    print(f"  Wrote {REPORT_OUT}")

    # 6. Print summary to stdout
    print(f"\n  Status: {status}")
    print(f"  Total cycles: {total_cycles}  accepted: {total_accepted}  rejected: {total_rejected}  rate: {accept_rate:.1f}%")
    for ok, msg in syntax_results:
        print(f"  {'OK' if ok else 'FAIL'} — {msg}")

    # 7. pk_sync
    print("\n--- pk_sync ---")
    sys.path.insert(0, str(AUTOAGENT_DIR))
    try:
        import pk_sync
        pk_sync.sync()
    except Exception as e:
        print(f"  pk_sync failed: {e}")


if __name__ == "__main__":
    main()
