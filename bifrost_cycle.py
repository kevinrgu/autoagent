"""
BIFROST Session O — Local 3-agent improvement cycle.

Proposer (llama4:scout on Forge) suggests one improvement.
Executor (qwen2.5-coder:7b on Hearth) implements it.
Evaluator (gemma4:26b on Forge) reviews and accepts/rejects.

No external SDKs — uses httpx to call Ollama /v1/chat/completions directly.
"""

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

# ============================================================================
# BIFROST LOCAL ENDPOINT CONFIG
# ============================================================================

PROPOSER_URL   = "http://192.168.2.50:11434/v1/chat/completions"
PROPOSER_MODEL = "bifrost-t2-gemma4"   # gemma4:26b on Forge — proposes improvements

EXECUTOR_URL   = "http://192.168.2.4:11434/v1/chat/completions"
EXECUTOR_MODEL = "bifrost-1a-hearth"   # qwen2.5-coder:7b on Hearth — implements changes

EVALUATOR_URL  = "http://192.168.2.50:11434/v1/chat/completions"
EVALUATOR_MODEL = "bifrost-t2-gemma4"  # gemma4:26b on Forge — reviews (same model, different system prompt)

TIMEOUT = 120  # seconds per LLM call


def call_llm(url: str, model: str, system: str, user: str) -> str:
    """Call an Ollama endpoint and return the assistant response."""
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": False,
        "temperature": 0.7,
        "max_tokens": 2048,
    }
    r = httpx.post(url, json=payload, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def read_target(target_path: str) -> str:
    """Read the target file, return contents or empty string."""
    p = Path(target_path)
    if p.exists():
        return p.read_text(encoding="utf-8", errors="replace")
    return ""


def log_decision(decisions_path: str, cycle: int, proposal: str, result: str, accepted: bool):
    """Append a structured entry to decisions.md."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    entry = (
        f"\n## Cycle {cycle} -- {ts}\n"
        f"**Proposal:** {proposal[:500]}\n\n"
        f"**Result:** {result[:500]}\n\n"
        f"**Accepted:** {'YES' if accepted else 'NO'}\n"
    )
    Path(decisions_path).open("a", encoding="utf-8").write(entry)


def run_cycle(cycle_num: int, program: dict, decisions_path: str) -> bool:
    """Run one propose-execute-evaluate cycle. Returns True if accepted."""
    target_path = program.get("target", "")
    target_code = read_target(target_path)
    objective = program.get("objective", "Improve the code.")

    print(f"\n{'='*60}")
    print(f"CYCLE {cycle_num}")
    print(f"{'='*60}")

    # --- PROPOSE ---
    print(f"[Proposer] {PROPOSER_MODEL} @ Forge ...")
    propose_prompt = (
        f"Objective: {objective}\n\n"
        f"Current code ({target_path}):\n```\n{target_code[:3000]}\n```\n\n"
        "Suggest ONE specific, bounded improvement. Be precise about what to change "
        "and why. Do not suggest rewrites. Output format:\n"
        "CHANGE: <what to change>\n"
        "REASON: <why this improves the code>\n"
        "DIFF: <before/after snippet>"
    )
    try:
        proposal = call_llm(PROPOSER_URL, PROPOSER_MODEL,
            "You are a code improvement proposer. Suggest one bounded change.", propose_prompt)
    except Exception as e:
        print(f"[Proposer] FAIL: {e}")
        log_decision(decisions_path, cycle_num, f"PROPOSER_FAIL: {e}", "", False)
        return False

    print(f"[Proposer] Proposal: {proposal[:200]}...")

    # --- EXECUTE ---
    print(f"[Executor] {EXECUTOR_MODEL} @ Hearth ...")
    execute_prompt = (
        f"Apply this change to the code below.\n\n"
        f"Proposed change:\n{proposal}\n\n"
        f"Current code ({target_path}):\n```\n{target_code[:3000]}\n```\n\n"
        "Output ONLY the modified code. No explanation."
    )
    try:
        new_code = call_llm(EXECUTOR_URL, EXECUTOR_MODEL,
            "You are a code executor. Apply the proposed change exactly.", execute_prompt)
    except Exception as e:
        print(f"[Executor] FAIL: {e}")
        log_decision(decisions_path, cycle_num, proposal[:300], f"EXECUTOR_FAIL: {e}", False)
        return False

    print(f"[Executor] Generated {len(new_code)} chars")

    # --- EVALUATE ---
    print(f"[Evaluator] {EVALUATOR_MODEL} @ Forge ...")
    evaluate_prompt = (
        f"Objective: {objective}\n\n"
        f"Original code:\n```\n{target_code[:2000]}\n```\n\n"
        f"Proposed change:\n{proposal[:500]}\n\n"
        f"New code:\n```\n{new_code[:2000]}\n```\n\n"
        "Does the new code improve on the original? Is it correct?\n"
        "Reply with EXACTLY one of:\n"
        "ACCEPT: <reason>\n"
        "REJECT: <reason>"
    )
    try:
        evaluation = call_llm(EVALUATOR_URL, EVALUATOR_MODEL,
            "You are a cross-family code reviewer. Accept only measurable improvements.", evaluate_prompt)
    except Exception as e:
        print(f"[Evaluator] FAIL: {e}")
        log_decision(decisions_path, cycle_num, proposal[:300], f"EVALUATOR_FAIL: {e}", False)
        return False

    accepted = evaluation.strip().upper().startswith("ACCEPT")
    print(f"[Evaluator] {'ACCEPTED' if accepted else 'REJECTED'}: {evaluation[:200]}")

    log_decision(decisions_path, cycle_num, proposal[:500], evaluation[:500], accepted)

    if accepted and target_path:
        # Write the new code to the target file
        Path(target_path).write_text(new_code, encoding="utf-8")
        print(f"[Written] {target_path} updated ({len(new_code)} chars)")

    return accepted


def parse_program(program_path: str) -> dict:
    """Parse program.md into a dict with objective, target, constraints."""
    p = Path(program_path)
    if not p.exists():
        return {"objective": "Improve the code.", "target": ""}

    text = p.read_text(encoding="utf-8", errors="replace")
    result = {"objective": "", "target": "", "raw": text}

    for line in text.split("\n"):
        line_stripped = line.strip()
        if line_stripped.startswith("## Objective"):
            continue
        if line_stripped.startswith("## Target"):
            continue
        if "Objective" in line and not result["objective"]:
            # Next non-empty line after ## Objective is the objective text
            pass
        if line_stripped and not line_stripped.startswith("#") and not line_stripped.startswith("-"):
            if not result["objective"]:
                result["objective"] = line_stripped

    # Extract target from "## Target" section
    in_target = False
    for line in text.split("\n"):
        if "## Target" in line:
            in_target = True
            continue
        if in_target and line.strip() and not line.startswith("#"):
            result["target"] = line.strip()
            break

    return result


def main():
    parser = argparse.ArgumentParser(description="BIFROST Session O — 3-agent cycle")
    parser.add_argument("--program", default="program.md", help="Path to program.md")
    parser.add_argument("--decisions", default="decisions.md", help="Path to decisions.md")
    parser.add_argument("--max-cycles", type=int, default=5, help="Max cycles per run")
    args = parser.parse_args()

    print("BIFROST Session O — Local 3-Agent Cycle")
    print(f"  Proposer:  {PROPOSER_MODEL} @ Forge")
    print(f"  Executor:  {EXECUTOR_MODEL} @ Hearth")
    print(f"  Evaluator: {EVALUATOR_MODEL} @ Forge")
    print(f"  Program:   {args.program}")
    print(f"  Decisions: {args.decisions}")
    print(f"  Max cycles: {args.max_cycles}")

    program = parse_program(args.program)
    if not program["objective"]:
        program["objective"] = "Improve the BIFROST AUTOPILOT agent pipeline."
    if not program["target"]:
        print("WARNING: No target file specified in program.md")

    print(f"  Objective: {program['objective'][:100]}")
    print(f"  Target:    {program['target'] or '(none)'}")

    accepted_count = 0
    for cycle in range(1, args.max_cycles + 1):
        try:
            ok = run_cycle(cycle, program, args.decisions)
            if ok:
                accepted_count += 1
        except Exception as e:
            print(f"CYCLE {cycle} EXCEPTION: {e}")
            log_decision(args.decisions, cycle, f"EXCEPTION: {e}", "", False)

    print(f"\n{'='*60}")
    print(f"SESSION COMPLETE: {accepted_count}/{args.max_cycles} accepted")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
