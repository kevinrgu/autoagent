"""
BIFROST Session O -- Local 3-agent self-improvement cycle.

Proposer (gemma4:26b on Forge) reads the target file and suggests ONE improvement.
Executor (gemma3:12b on Bifrost) implements the change.
Evaluator (gemma4:26b on Forge) reviews and accepts/rejects.

No external SDKs -- uses httpx to call Ollama /v1/chat/completions directly.
No max_tokens -- let models respond at full length.
"""

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

# ============================================================================
# ENDPOINT CONFIG
# ============================================================================

PROPOSER_URL    = "http://192.168.2.50:11434/v1/chat/completions"
PROPOSER_MODEL  = "bifrost-t2-gemma4"    # gemma4:26b on Forge

EXECUTOR_URL    = "http://192.168.2.33:11434/v1/chat/completions"
EXECUTOR_MODEL  = "bifrost-t1b"          # gemma3:12b on Bifrost

EVALUATOR_URL   = "http://192.168.2.50:11434/v1/chat/completions"
EVALUATOR_MODEL = "bifrost-t2-gemma4"    # gemma4:26b on Forge

TIMEOUT = 300  # seconds per LLM call -- no max_tokens, let model finish


# ============================================================================
# LLM CALL
# ============================================================================

def call_llm(url: str, model: str, system: str, user: str) -> str:
    """Call an Ollama endpoint. No max_tokens -- full-length response."""
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": False,
        "temperature": 0.7,
    }
    r = httpx.post(url, json=payload, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


# ============================================================================
# HELPERS
# ============================================================================

def read_file(path: str) -> str:
    """Read a file, return empty string if missing."""
    p = Path(path)
    return p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""


def strip_fences(text: str) -> str:
    """Remove markdown code fences from LLM output."""
    text = text.strip()
    # Remove opening fence: ```python or ```
    text = re.sub(r"^```(?:python|py)?\s*\n?", "", text)
    # Remove closing fence
    text = re.sub(r"\n?```\s*$", "", text)
    return text.strip()


def smart_apply(target_path: str, original: str, new_code: str) -> str:
    """Apply new_code to target file. If new_code is a partial function,
    patch just that function. Otherwise full replace. Returns description."""
    new_code = strip_fences(new_code)

    if not new_code or len(new_code) < 20:
        return "SKIPPED: executor output too short"

    # Try to find the function name in the new code
    fn_match = re.search(r"(async\s+)?def\s+(\w+)", new_code.strip())
    if fn_match:
        fn_name = fn_match.group(2)
        # Find and replace the function in the original
        pattern = re.compile(
            rf"(async\s+)?def\s+{re.escape(fn_name)}\b.*?(?=\n(?:async\s+)?def\s|\nclass\s|$)",
            re.DOTALL,
        )
        if pattern.search(original):
            patched = pattern.sub(lambda _: new_code.rstrip(), original)
            Path(target_path).write_text(patched, encoding="utf-8")
            return f"patched function {fn_name}"
        else:
            # Function not found in original -- append it
            Path(target_path).write_text(original.rstrip() + "\n\n\n" + new_code, encoding="utf-8")
            return f"appended new function {fn_name}"

    # No function match -- full replace
    Path(target_path).write_text(new_code, encoding="utf-8")
    return f"full replace (no function pattern, {len(new_code)} chars)"
def log_decision(path: str, cycle: int, proposal: str, executor_len: int,
                 evaluation: str, accepted: bool):
    """Append a cycle entry to decisions.md."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    entry = (
        f"\n## Cycle {cycle} -- {ts}\n"
        f"**Proposal:** {proposal}\n\n"
        f"**Executor output:** {executor_len} chars\n\n"
        f"**Evaluator:** {evaluation}\n\n"
        f"**Accepted:** {'YES' if accepted else 'NO'}\n"
    )
    Path(path).open("a", encoding="utf-8").write(entry)


def parse_program(path: str) -> dict:
    """Parse program.md for objective and target."""
    text = read_file(path)
    result = {"objective": "", "target": ""}

    in_section = None
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("## Objective"):
            in_section = "objective"
            continue
        elif stripped.startswith("## Target"):
            in_section = "target"
            continue
        elif stripped.startswith("## "):
            in_section = None
            continue

        if in_section and stripped and not stripped.startswith("-"):
            if in_section == "objective" and not result["objective"]:
                result["objective"] = stripped
            elif in_section == "target" and not result["target"]:
                result["target"] = stripped

    return result


# ============================================================================
# CYCLE
# ============================================================================

def run_cycle(cycle_num: int, objective: str, target_path: str,
              decisions_path: str) -> bool:
    """Run one propose-execute-evaluate cycle. Returns True if accepted."""
    target_code = read_file(target_path)
    if not target_code:
        print(f"  WARNING: target file empty or missing: {target_path}")

    print(f"\n{'='*60}")
    print(f"  CYCLE {cycle_num}")
    print(f"{'='*60}")

    # --- PROPOSE ---
    print(f"\n  [Proposer] {PROPOSER_MODEL} @ Forge ...")
    t0 = time.time()
    try:
        proposal = call_llm(
            PROPOSER_URL, PROPOSER_MODEL,
            "You are a senior Python developer reviewing code for improvements. "
            "Suggest ONE specific, bounded improvement. Be precise about what "
            "function to change and why. Do not suggest full rewrites.",
            f"Objective: {objective}\n\n"
            f"File: {target_path}\n"
            f"Code:\n```python\n{target_code}\n```\n\n"
            f"Suggest one improvement:",
        )
    except Exception as e:
        print(f"  [Proposer] FAIL ({time.time()-t0:.0f}s): {e}")
        log_decision(decisions_path, cycle_num, f"PROPOSER_FAIL: {e}", 0, "", False)
        return False

    print(f"  [Proposer] {len(proposal)} chars ({time.time()-t0:.0f}s)")
    print(f"  Preview: {proposal[:300]}...")

    if len(proposal) < 50:
        print("  [Proposer] Response too short -- skipping cycle")
        log_decision(decisions_path, cycle_num, proposal, 0, "SKIPPED: proposal too short", False)
        return False

    # --- EXECUTE ---
    print(f"\n  [Executor] {EXECUTOR_MODEL} @ Bifrost ...")
    t0 = time.time()
    try:
        new_code = call_llm(
            EXECUTOR_URL, EXECUTOR_MODEL,
            "You are a Python code implementer. Output ONLY the modified function "
            "or code section. Start with the def/class line. No explanations, "
            "no markdown fences, no full file rewrites. Do not truncate.",
            f"Apply this change to the code.\n\n"
            f"Proposed change:\n{proposal}\n\n"
            f"Current code:\n```python\n{target_code}\n```\n\n"
            f"Output ONLY the modified function:",
        )
    except Exception as e:
        print(f"  [Executor] FAIL ({time.time()-t0:.0f}s): {e}")
        log_decision(decisions_path, cycle_num, proposal[:500], 0, f"EXECUTOR_FAIL: {e}", False)
        return False

    new_code = strip_fences(new_code)
    print(f"  [Executor] {len(new_code)} chars ({time.time()-t0:.0f}s)")

    if len(new_code) < 100:
        print("  [Executor] Output too short -- skipping cycle")
        log_decision(decisions_path, cycle_num, proposal[:500], len(new_code),
                     "SKIPPED: executor output too short", False)
        return False

    # --- EVALUATE ---
    print(f"\n  [Evaluator] {EVALUATOR_MODEL} @ Forge ...")
    t0 = time.time()
    try:
        evaluation = call_llm(
            EVALUATOR_URL, EVALUATOR_MODEL,
            "You are a code quality evaluator. Compare the original and new code. "
            "Reply with PASS or FAIL on the FIRST LINE, followed by one paragraph "
            "of reasoning. PASS means the change is correct and improves the code.",
            f"Objective: {objective}\n\n"
            f"Original code:\n```python\n{target_code[-4000:]}\n```\n\n"
            f"Proposed change:\n{proposal[:1000]}\n\n"
            f"New code section:\n```python\n{new_code}\n```\n\n"
            f"Is this an improvement? Reply PASS or FAIL on the first line:",
        )
    except Exception as e:
        print(f"  [Evaluator] FAIL ({time.time()-t0:.0f}s): {e}")
        log_decision(decisions_path, cycle_num, proposal[:500], len(new_code),
                     f"EVALUATOR_FAIL: {e}", False)
        return False

    print(f"  [Evaluator] {len(evaluation)} chars ({time.time()-t0:.0f}s)")
    print(f"  Response: {evaluation}")

    # Parse first line for PASS/FAIL
    first_line = evaluation.strip().split("\n")[0].strip().upper()
    accepted = first_line.startswith("PASS")

    print(f"\n  Result: {'ACCEPTED' if accepted else 'REJECTED'}")

    log_decision(decisions_path, cycle_num, proposal[:800], len(new_code),
                 evaluation[:800], accepted)

    if accepted:
        apply_result = smart_apply(target_path, target_code, new_code)
        print(f"  [Apply] {apply_result}")

    return accepted


# ============================================================================
# MAIN
# ============================================================================

def main():
    # Fix Windows console encoding
    if sys.stdout.encoding != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="BIFROST Session O -- 3-agent cycle")
    parser.add_argument("--program", default="program.md")
    parser.add_argument("--decisions", default="decisions.md")
    parser.add_argument("--max-cycles", type=int, default=5)
    args = parser.parse_args()

    print("BIFROST Session O -- Local 3-Agent Cycle")
    print(f"  Proposer:  {PROPOSER_MODEL} @ Forge :11434")
    print(f"  Executor:  {EXECUTOR_MODEL} @ Bifrost :11434")
    print(f"  Evaluator: {EVALUATOR_MODEL} @ Forge :11434")

    program = parse_program(args.program)
    objective = program["objective"] or "Improve the code quality and robustness."
    target = program["target"] or ""

    print(f"  Objective: {objective}")
    print(f"  Target:    {target or '(none)'}")
    print(f"  Decisions: {args.decisions}")
    print(f"  Cycles:    {args.max_cycles}")

    if not target:
        print("\nERROR: No target file in program.md")
        return

    accepted_count = 0
    for cycle in range(1, args.max_cycles + 1):
        try:
            ok = run_cycle(cycle, objective, target, args.decisions)
            if ok:
                accepted_count += 1
        except Exception as e:
            print(f"\n  CYCLE {cycle} EXCEPTION: {e}")
            log_decision(args.decisions, cycle, f"EXCEPTION: {e}", 0, "", False)

    print(f"\n{'='*60}")
    print(f"  SESSION COMPLETE: {accepted_count}/{args.max_cycles} accepted")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
