"""
BIFROST Session O -- Local 3-agent self-improvement cycle.

Cross-origin assignment:
  Proposer:  mistral-small3.1:24b (Mistral/EU) on Bifrost -- crisp proposals
  Executor:  gemma3:12b (Google/US) on Bifrost -- fast coding
  Evaluator: gemma4:26b (Google/US) on Forge -- deep judgment

No external SDKs -- uses httpx to call Ollama /v1/chat/completions directly.
No max_tokens -- let models respond at full length.
"""

import argparse
import ast
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

# ============================================================================
# ENDPOINT CONFIG
# ============================================================================

PROPOSER_URL    = "http://192.168.2.33:11434/v1/chat/completions"
PROPOSER_MODEL  = "mistral-small3.1:24b"   # Mistral/EU on Bifrost

EXECUTOR_URL    = "http://192.168.2.33:11434/v1/chat/completions"
EXECUTOR_MODEL  = "bifrost-t1b"            # bifrost-t1b on Bifrost -- fixes Forge contention

EVALUATOR_URL  = "http://192.168.2.50:11434/v1/chat/completions"
EVALUATOR_MODEL = "bifrost-t2-gemma4"                  # Meta/US llama4:scout Q3_K_S (bartowski) on Forge :8005 via llama-server

TIMEOUT = 360

# Profile-level overrides (set by load_profile)
ACTIVE_NUM_CTX = 8192
ACTIVE_SYSTEM_RULES = ""
ACTIVE_EVALUATOR_PROMPT_EXTRA = ""


# ============================================================================
# LLM CALL — DO NOT MODIFY (self-modification breaks all agents)
# ============================================================================

def call_llm(url: str, model: str, system: str, user: str,
             num_ctx: int = 8192) -> str:
    """Call an Ollama endpoint. No max_tokens -- full-length response."""
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": False,
        "temperature": 0.7,
        "options": {"num_ctx": num_ctx},
    }
    r = httpx.post(url, json=payload, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


# ============================================================================
# HELPERS
# ============================================================================

def read_file(path: str) -> str:
    p = Path(path)
    return p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""


def strip_fences(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:python|py)?\s*\n?", "", text)
    text = re.sub(r"\n?```\s*$", "", text)
    return text.strip()


def smart_apply(target_path: str, original: str, new_code: str) -> str:
    """Apply new_code to target file. Partial patch if function detected."""
    new_code = strip_fences(new_code)
    if not new_code or len(new_code) < 20:
        return "SKIPPED: executor output too short"

    fn_match = re.search(r"(async\s+)?def\s+(\w+)", new_code.strip())
    if fn_match:
        fn_name = fn_match.group(2)
        # DO NOT patch call_llm — it breaks the harness
        if fn_name == "call_llm":
            return "SKIPPED: refusing to self-modify call_llm (core function)"
        pattern = re.compile(
            rf"(async\s+)?def\s+{re.escape(fn_name)}\b.*?"
            rf"(?=\n(?:async\s+)?def\s|\nclass\s|\Z)",
            re.DOTALL,
        )
        if pattern.search(original):
            try:
                with open(target_path, 'w', encoding='utf-8') as file:
                    file.write(re.sub(pattern, new_code, original))
                return "Successfully applied patch."
            except Exception as e:
                return f"Failed to write to file: {str(e)}"
        else:
            try:
                with open(target_path, 'w', encoding='utf-8') as file:
                    file.write(
                        original.rstrip() + "\n\n\n" + new_code
                    )
                return f"appended new function {fn_name}"
            except Exception as e:
                return f"Failed to write to file: {str(e)}"
    else:
        try:
            with open(target_path, 'w', encoding='utf-8') as file:
                file.write(new_code)
            return "Successfully applied new code."
        except Exception as e:
            return f"Failed to write to file: {str(e)}"
def log_decision(path: str, cycle: int, proposal: str, evaluation: str,
                 accepted: bool, executor_len: int = 0):
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    decision = 'YES' if accepted else 'NO'
    entry = (
        f"\n## Cycle {cycle} -- {timestamp}\n"
        f"**Proposal:** {proposal}\n\n"
        f"**Executor output:** {executor_len} chars\n\n"
        f"**Evaluator:** {evaluation}\n\n"
        f"**Accepted:** {decision}\n"
    )
    with Path(path).open("a", encoding="utf-8") as file:
        file.write(entry)
def log_run_summary(path: str, accepted: int, total: int):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    summary = (
        f"\n## Run Summary -- {ts}\n"
        f"Accepted: {accepted}/{total} | "
        f"Models: {PROPOSER_MODEL} -> {EXECUTOR_MODEL} -> {EVALUATOR_MODEL}\n"
    )
    Path(path).open("a", encoding="utf-8").write(summary)


def backup_target(target_path: str):
    p = Path(target_path)
    if p.exists():
        bak = p.with_suffix(".py.bak")
        bak.write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"  [Backup] {p.name} -> {bak.name}")


def extract_signatures(code: str) -> str:
    """Extract function/class signatures for proposer context."""
    lines = []
    try:
        for node in ast.walk(ast.parse(code)):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
                lines.append(f"  {prefix} {node.name}(...) [line {node.lineno}]")
    except SyntaxError:
        lines.append("  (could not parse file -- syntax error)")
    return "\n".join(lines)


def syntax_check(code: str) -> tuple[bool, str]:
    """Quick syntax check via py_compile. Returns (passed, error_message)."""
    try:
        fd, tmp_path = tempfile.mkstemp(suffix='.py')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                f.write(code)
            result = subprocess.run(
                [sys.executable, '-m', 'py_compile', tmp_path],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode != 0:
                return False, result.stderr.strip()
            return True, ""
        finally:
            os.unlink(tmp_path)
    except Exception as e:
        return True, ""  # on error, skip check rather than block


# Rejection feedback buffer (persists across cycles within a run)
_recent_rejections: list[str] = []
MAX_REJECTION_HISTORY = 3


def parse_program(path: str) -> dict:
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
# PROPOSAL WITH RETRY
# ============================================================================

def propose(objective: str, target_path: str, target_code: str) -> str | None:
    print(f"\n  [Proposer] {PROPOSER_MODEL} @ Bifrost ...")

    # Build signature summary for proposer context
    sig_summary = extract_signatures(target_code)

    # Build rejection feedback section
    rejection_section = ""
    if _recent_rejections:
        items = "\n".join(f"  {i+1}. {r}" for i, r in enumerate(_recent_rejections))
        rejection_section = f"\n\nAVOID THESE PATTERNS (recently rejected):\n{items}"

    # Use profile-specific rules if active, otherwise default coding rules
    if ACTIVE_SYSTEM_RULES:
        rules_block = f"PROFILE RULES:\n{ACTIVE_SYSTEM_RULES}"
    else:
        rules_block = (
            "CRITICAL CODE RULES FOR THIS FILE:\n"
            "- LangGraph node functions are SYNC (def, not async def)\n"
            "- To call async functions from sync context, use: _run_async(async_coroutine)\n"
            "- NEVER use raw 'await' in a sync def -- always wrap with _run_async()\n"
            "- The _run_async() helper already exists in the file\n"
            "- Check the function signatures below to know which functions are async vs sync"
        )

    system_prompt = (
        "You are a senior Python developer. Suggest ONE small, safe improvement. "
        "Keep your response under 800 chars. Just state WHAT to change and WHY in 2-3 sentences, "
        "then show the improved code snippet. No markdown headers, no numbered lists, no verbose analysis. "
        "Do not suggest changes to call_llm or imports. No new dependencies. No signature changes.\n\n"
        f"{rules_block}\n\n"
        f"FUNCTION SIGNATURES:\n{sig_summary}"
        f"{rejection_section}"
    )

    t0 = time.time()
    try:
        proposal = call_llm(
            PROPOSER_URL, PROPOSER_MODEL,
            system_prompt,
            f"Objective: {objective}\n\nCode:\n```python\n{target_code[:5000]}{'...[TRUNCATED]' if len(target_code) > 5000 else ''}\n```\n\nSuggest one improvement:",
            num_ctx=ACTIVE_NUM_CTX,
        )
    except Exception as e:
        print(f"  [Proposer] FAIL ({time.time()-t0:.0f}s): {e}")
        proposal = ""

    if proposal and len(proposal.strip()) >= 50:
        print(f"  [Proposer] {len(proposal)} chars ({time.time()-t0:.0f}s)")
        print(f"  Preview: {proposal[:300]}...")
        return proposal

    print(f"  [Proposer] Empty/short, retrying simpler prompt...")
    t0 = time.time()
    try:
        proposal = call_llm(
            PROPOSER_URL, PROPOSER_MODEL,
            "You are a code expert. Be brief.",
            f"Suggest one small improvement to this Python code (not call_llm). NEVER use raw 'await' in sync def -- use _run_async() instead.\n```python\n{target_code[:5000]}{chr(10) + '...[TRUNCATED]' if len(target_code) > 5000 else ''}\n```",
            num_ctx=ACTIVE_NUM_CTX,
        )
    except Exception as e:
        print(f"  [Proposer] Retry FAIL ({time.time()-t0:.0f}s): {e}")
        return None

    if proposal and len(proposal.strip()) >= 30:
        print(f"  [Proposer] Retry OK: {len(proposal)} chars ({time.time()-t0:.0f}s)")
        print(f"  Preview: {proposal[:300]}...")
        return proposal
    print("  [Proposer] No usable proposal after retry")
    return None
def run_cycle(cycle_num: int, objective: str, target_path: str,
              decisions_path: str) -> bool:
    target_code = read_file(target_path)
    if not target_code:
        print(f"  WARNING: target file empty: {target_path}")

    print(f"\n{'='*60}")
    print(f"  CYCLE {cycle_num}")
    print(f"{'='*60}")

    proposal = propose(objective, target_path, target_code)
    if not proposal:
        log_decision(decisions_path, cycle_num, "PROPOSER_FAIL", "", False)
        return False

    MAX_RETRIES = 3
    retries = 0

    while retries < MAX_RETRIES:
        print(f"\n  [Executor] {EXECUTOR_MODEL} @ Bifrost ... (Retry {retries + 1})")
        t0 = time.time()
        try:
            new_code = call_llm(
                EXECUTOR_URL, EXECUTOR_MODEL,
                "You are a Python implementer. Output ONLY the modified function. "
                "Start with def. No markdown fences, no explanations, no full file rewrites.",
                f"Proposed change:\n{proposal[:1500]}\n\nCurrent code:\n```python\n{target_code[:6000]}{'...[TRUNCATED]' if len(target_code) > 6000 else ''}\n```\n\nOutput ONLY the modified function:",
                num_ctx=ACTIVE_NUM_CTX,
            )
        except httpx.HTTPStatusError as e:
            error_message = f"HTTP error: {e.response.status_code} - {e.response.text}" if e.response else str(e)
            print(f"  [Executor] FAIL ({time.time()-t0:.0f}s): {error_message}")
            retries += 1
            continue
        except httpx.RequestError as e:
            error_message = f"Request error: {str(e)}"
            print(f"  [Executor] RequestError ({time.time()-t0:.0f}s): {error_message}")
            retries += 1
            continue
        except httpx.HTTPError as e:
            error_message = f"HTTP error: {str(e)}"
            print(f"  [Executor] HTTPError ({time.time()-t0:.0f}s): {error_message}")
            retries += 1
            continue
        except Exception as e:
            print(f"  [Executor] FAIL ({time.time()-t0:.0f}s): {str(e)}")
            retries += 1
            continue

        new_code = strip_fences(new_code)
        print(f"  [Executor] {len(new_code)} chars ({time.time()-t0:.0f}s)")

        if len(new_code) < 100:
            print("  [Executor] Output too short")
            retries += 1
            continue

        # Syntax pre-filter: catch basic errors before burning an evaluator call
        syn_ok, syn_err = syntax_check(new_code)
        if not syn_ok:
            print(f"  [SyntaxCheck] FAIL: {syn_err[:200]}")
            log_decision(decisions_path, cycle_num, proposal[:800],
                         f"SYNTAX_ERROR: {syn_err[:400]}", False, len(new_code))
            reason = f"SYNTAX_ERROR: {syn_err[:120]}"
            _recent_rejections.append(reason)
            if len(_recent_rejections) > MAX_REJECTION_HISTORY:
                _recent_rejections.pop(0)
            retries += 1
            continue
        print("  [SyntaxCheck] OK")

        print(f"\n  [Evaluator] {EVALUATOR_MODEL} @ Forge ... (Retry {retries + 1})")
        t0 = time.time()
        eval_system = (
            "You are a code quality evaluator. Reply PASS or FAIL on the FIRST LINE "
            "followed by reasoning. PASS only if the change is correct and improves the code. "
            "FAIL if it introduces breaking changes, wrong imports, or missing call-site updates."
        )
        if ACTIVE_EVALUATOR_PROMPT_EXTRA:
            eval_system += "\n\n" + ACTIVE_EVALUATOR_PROMPT_EXTRA
        try:
            evaluation = call_llm(
                EVALUATOR_URL, EVALUATOR_MODEL,
                eval_system,
                f"Objective: {objective}\n\n"
                f"Original:\n```python\n{target_code[-4000:]}\n```\n\n"
                f"Proposed:\n{proposal[:1000]}\n\n"
                f"New code:\n```python\n{new_code}\n```\n\n"
                f"PASS or FAIL?",
                num_ctx=ACTIVE_NUM_CTX,
            )
        except httpx.HTTPStatusError as e:
            error_message = f"HTTP error: {e.response.status_code} - {e.response.text}" if e.response else str(e)
            print(f"  [Evaluator] FAIL ({time.time()-t0:.0f}s): {error_message}")
            retries += 1
            continue
        except httpx.RequestError as e:
            error_message = f"Request error: {str(e)}"
            print(f"  [Evaluator] RequestError ({time.time()-t0:.0f}s): {error_message}")
            retries += 1
            continue
        except httpx.HTTPError as e:
            error_message = f"HTTP error: {str(e)}"
            print(f"  [Evaluator] HTTPError ({time.time()-t0:.0f}s): {error_message}")
            retries += 1
            continue
        except Exception as e:
            print(f"  [Evaluator] FAIL ({time.time()-t0:.0f}s): {str(e)}")
            retries += 1
            continue

        print(f"  [Evaluator] {len(evaluation)} chars ({time.time()-t0:.0f}s)")
        print(f"  Response: {evaluation}")

        first_line = evaluation.strip().split("\n")[0].strip().upper()
        accepted = first_line.startswith("PASS")
        print(f"\n  Result: {'ACCEPTED' if accepted else 'REJECTED'}")

        log_decision(decisions_path, cycle_num, proposal[:800], evaluation[:800], accepted, len(new_code))

        if accepted:
            try:
                apply_result = smart_apply(target_path, target_code, new_code)
                print(f"  [Apply] {apply_result}")
                return True
            except Exception as e:
                print(f"  [Apply] FAIL: {str(e)}")
                retries += 1
                continue

        # Track rejection reason for feedback loop
        reason = evaluation.strip().split("\n")[0][:120]
        _recent_rejections.append(reason)
        if len(_recent_rejections) > MAX_REJECTION_HISTORY:
            _recent_rejections.pop(0)

        retries += 1

    log_decision(decisions_path, cycle_num, proposal[:800], "EXECUTOR/EVALUATOR FAIL after retries", False)
    return False
def load_profile(profile_name: str) -> dict | None:
    """Load a named profile from profiles.json and override global config."""
    global PROPOSER_URL, PROPOSER_MODEL, EXECUTOR_URL, EXECUTOR_MODEL
    global EVALUATOR_URL, EVALUATOR_MODEL, ACTIVE_NUM_CTX, ACTIVE_SYSTEM_RULES
    global ACTIVE_EVALUATOR_PROMPT_EXTRA

    profiles_path = Path(__file__).parent / "profiles.json"
    if not profiles_path.exists():
        print(f"  WARNING: profiles.json not found at {profiles_path}")
        return None

    with open(profiles_path, encoding="utf-8") as f:
        data = json.load(f)

    profiles = data.get("profiles", {})
    if profile_name not in profiles:
        print(f"  ERROR: profile '{profile_name}' not found. Available: {list(profiles.keys())}")
        return None

    profile = profiles[profile_name]

    # Override global model/URL config
    PROPOSER_MODEL = profile["proposer"]["model"]
    PROPOSER_URL = profile["proposer"]["url"]
    EXECUTOR_MODEL = profile["executor"]["model"]
    EXECUTOR_URL = profile["executor"]["url"]
    EVALUATOR_MODEL = profile["evaluator"]["model"]
    EVALUATOR_URL = profile["evaluator"]["url"]
    ACTIVE_NUM_CTX = profile.get("num_ctx", 8192)
    ACTIVE_SYSTEM_RULES = profile.get("system_rules", "")
    ACTIVE_EVALUATOR_PROMPT_EXTRA = profile.get("evaluator_prompt_extra", "")

    return profile


def main():
    if sys.stdout.encoding != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="BIFROST Session O")
    parser.add_argument("--program", default="program.md")
    parser.add_argument("--decisions", default="decisions.md")
    parser.add_argument("--max-cycles", type=int, default=5)
    parser.add_argument("--profile", choices=["coding", "general", "research"],
                        default=None, help="Named profile to load from profiles.json")
    args = parser.parse_args()

    # --- Profile loading ---
    profile = None
    profile_name = args.profile

    if not profile_name:
        # Check active_profile.txt fallback
        active_file = Path(__file__).parent / "active_profile.txt"
        if active_file.exists():
            candidate = active_file.read_text(encoding="utf-8").strip().lower()
            if candidate in ("coding", "general", "research"):
                profile_name = candidate

    if profile_name:
        profile = load_profile(profile_name)
        if profile is None:
            print(f"  WARNING: Failed to load profile '{profile_name}', using defaults")

    # Determine target and decisions path
    if profile:
        target = profile["targets"][0]  # primary target
        profile_upper = profile_name.upper()
        decisions_path = str(Path(__file__).parent / "profiles" / profile_upper / f"decisions_{profile_name}.md")
        objective = profile.get("system_rules", "Improve the code quality and robustness.")
        num_ctx = profile.get("num_ctx", 8192)
    else:
        program = parse_program(args.program)
        objective = program["objective"] or "Improve the code quality and robustness."
        target = program["target"] or ""
        decisions_path = args.decisions
        num_ctx = 8192

    print("BIFROST Session O -- Local 3-Agent Cycle")
    if profile_name:
        print(f"  Profile:   /{profile_name}")
    print(f"  Proposer:  {PROPOSER_MODEL}")
    print(f"  Executor:  {EXECUTOR_MODEL}")
    print(f"  Evaluator: {EVALUATOR_MODEL}")
    print(f"  Objective: {objective[:120]}")
    print(f"  Target:    {target or '(none)'}")
    print(f"  Decisions: {decisions_path}")
    print(f"  Cycles:    {args.max_cycles}")
    print(f"  num_ctx:   {num_ctx}")

    if not target:
        print("\nERROR: No target file specified")
        return

    backup_target(target)

    accepted_count = 0
    for cycle in range(1, args.max_cycles + 1):
        try:
            ok = run_cycle(cycle, objective, target, decisions_path)
            if ok:
                accepted_count += 1
        except Exception as e:
            print(f"\n  CYCLE {cycle} EXCEPTION: {e}")
            log_decision(decisions_path, cycle, f"EXCEPTION: {e}", "", False)

    log_run_summary(decisions_path, accepted_count, args.max_cycles)

    print(f"\n{'='*60}")
    print(f"  SESSION COMPLETE: {accepted_count}/{args.max_cycles} accepted")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
