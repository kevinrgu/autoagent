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
import hashlib
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

from session_o_gates import run_all_gates

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
ACTIVE_TEMPERATURE = 0.7
ACTIVE_EXECUTOR_FLEET: list[dict] = []  # populated by load_profile when profile.executor_fleet present


# ============================================================================
# LLM CALL — DO NOT MODIFY (self-modification breaks all agents)
# ============================================================================

def call_llm(url: str, model: str, system: str, user: str,
             num_ctx: int = 8192, temperature: float | None = None,
             max_tokens: int = 1500) -> str:
    """Call an Ollama/llama-server endpoint.

    Fix 1: Ollama defaults num_predict=128, which truncates proposer/executor
    output at ~500 chars. We set both OpenAI-standard max_tokens and
    Ollama-specific options.num_predict so whichever backend is in use honors
    the cap. keep_alive=15m keeps big models (qwen3:30b) resident across the
    evaluator swap within a cycle.
    """
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": False,
        "temperature": temperature if temperature is not None else ACTIVE_TEMPERATURE,
        "max_tokens": max_tokens,
        "options": {"num_ctx": num_ctx, "num_predict": max_tokens},
        "keep_alive": "15m",
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
    text = text.strip()
    # Strip prose before first 'def ' or 'async def ' line
    match = re.search(r"^(async\s+)?def\s+", text, re.MULTILINE)
    if match and match.start() > 0:
        text = text[match.start():]
    return text


def apply_diff(original: str, diff_text: str) -> str | None:
    """Parse SEARCH/REPLACE blocks and apply them to original code.

    Returns patched code, or None if no SEARCH/REPLACE blocks found
    (signals caller to fall back to legacy behavior).
    """
    pattern = re.compile(
        r"<<<SEARCH\s*\n(.*?)>>>REPLACE\s*\n(.*?)<<<END",
        re.DOTALL,
    )
    blocks = pattern.findall(diff_text)
    if not blocks:
        return None  # no diff blocks → fall back to smart_apply

    patched = original
    for search_text, replace_text in blocks:
        search_text = search_text.rstrip("\n")
        replace_text = replace_text.rstrip("\n")

        if not search_text.strip():
            continue

        if search_text not in patched:
            search_norm = "\n".join(l.rstrip() for l in search_text.split("\n"))
            patched_norm = "\n".join(l.rstrip() for l in patched.split("\n"))
            if search_norm in patched_norm:
                patched = patched_norm.replace(search_norm, replace_text, 1)
            else:
                print(f"  [apply_diff] WARNING: search block not found ({len(search_text)} chars), skipping")
                continue
        else:
            patched = patched.replace(search_text, replace_text, 1)

    if patched == original:
        return None
    return patched


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


def warmup_models() -> None:
    """Fix 6: Pre-load proposer/executor/evaluator models with keep_alive=15m.

    Ollama defaults to OLLAMA_MAX_LOADED_MODELS=1, so calls that target
    different models will thrash the active model. Warming each model in
    sequence ensures the first real call does not pay a cold-load penalty,
    and keep_alive=15m (baked into call_llm) keeps them resident across the
    proposer -> executor -> evaluator swap within a cycle.
    """
    roles = [
        ("Proposer",  PROPOSER_URL,  PROPOSER_MODEL),
        ("Executor",  EXECUTOR_URL,  EXECUTOR_MODEL),
        ("Evaluator", EVALUATOR_URL, EVALUATOR_MODEL),
    ]
    print("\n  [Warmup] Pre-loading models with keep_alive=15m ...")
    for name, url, model in roles:
        if not url or not model:
            continue
        t0 = time.time()
        try:
            r = httpx.post(
                url,
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": "ping"}],
                    "stream": False,
                    "max_tokens": 1,
                    "options": {"num_predict": 1},
                    "keep_alive": "15m",
                },
                timeout=180,
            )
            elapsed = time.time() - t0
            print(f"  [Warmup] {name} ({model}): {r.status_code} ({elapsed:.0f}s)")
        except Exception as e:
            elapsed = time.time() - t0
            print(f"  [Warmup] {name} ({model}): FAILED ({elapsed:.0f}s) {e}")


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


def pick_target_function(code: str, max_lines: int = 80) -> dict | None:
    """Pick a random function ≤max_lines from code.

    Returns dict with keys: name, start (0-idx), end (exclusive 0-idx), source.
    Returns None if no suitable function or code is unparseable.
    """
    import random
    code_lines = code.split("\n")
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None

    candidates = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == "call_llm":
                continue
            start = node.lineno - 1
            end = node.end_lineno if hasattr(node, 'end_lineno') and node.end_lineno else start + 1
            fn_lines = end - start
            candidates.append((node.name, start, end, fn_lines))

    if not candidates:
        return None

    # Prefer functions ≤max_lines; if all exceed, pick the smallest
    small = [c for c in candidates if c[3] <= max_lines]
    if small:
        pick = random.choice(small)
    else:
        pick = min(candidates, key=lambda c: c[3])

    name, start, end, fn_lines = pick
    fn_code = "\n".join(code_lines[start:end])
    print(f"  [TargetFn] {name} ({fn_lines} lines, line {start+1}-{end})")
    return {"name": name, "start": start, "end": end, "source": fn_code}


def extract_function_source(source: str, func_name: str):
    """Extract one function by name from source.

    Returns (func_source, start_line_0idx, end_line_0idx_exclusive) or None.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
            lines = source.split('\n')
            start = node.lineno - 1
            end = node.end_lineno if hasattr(node, 'end_lineno') and node.end_lineno else start + 1
            return '\n'.join(lines[start:end]), start, end
    return None


def splice_function_back(source: str, new_func: str, start_line: int, end_line: int) -> str:
    """Replace lines [start_line:end_line] in source with new_func source.

    Preserves the original leading indentation of the function's ``def`` line.
    If the executor outputs a function at column 0 but the target was a nested
    (indented) function, the new function is re-indented to match — otherwise
    the splice would break the containing function's scope.
    """
    lines = source.split('\n')
    # Detect original indentation from the first non-blank line of the target
    orig_indent = ""
    for i in range(start_line, min(end_line, len(lines))):
        if lines[i].strip():
            orig_indent = lines[i][:len(lines[i]) - len(lines[i].lstrip())]
            break

    new_lines = new_func.rstrip('\n').split('\n')
    # Detect executor's base indentation from its first non-blank line
    exec_indent = ""
    for nl in new_lines:
        if nl.strip():
            exec_indent = nl[:len(nl) - len(nl.lstrip())]
            break

    # Re-indent: strip executor's base indent, then prepend the original indent
    if orig_indent != exec_indent:
        rebased = []
        for nl in new_lines:
            if nl.startswith(exec_indent):
                rebased.append(orig_indent + nl[len(exec_indent):])
            elif nl.strip() == "":
                rebased.append(nl)
            else:
                # Line has less indent than the base — leave as-is with orig prefix
                rebased.append(orig_indent + nl.lstrip())
        new_lines = rebased

    return '\n'.join(lines[:start_line] + new_lines + lines[end_line:])


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

# Proposal dedup (persists across cycles within a run)
_seen_proposal_hashes: set[str] = set()


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

def propose(objective: str, target_path: str, target_code: str,
            target_fn: dict | None = None) -> str | None:
    print(f"\n  [Proposer] {PROPOSER_MODEL} @ Bifrost ...")

    # Build signature summary for proposer context
    sig_summary = extract_signatures(target_code)

    # Use pre-selected target function (picked once in run_cycle so proposer
    # and executor see the same function). When set, the proposer MUST only
    # suggest changes to this function — no wandering.
    focus_section = ""
    if target_fn:
        focus_section = (
            f"\n\nTARGET FUNCTION — you MUST only suggest changes to this function "
            f"(name: `{target_fn['name']}`). Do NOT suggest changes to any other function. "
            f"Do NOT rename or replace it with a different function.\n"
            f"```python\n{target_fn['source']}\n```"
        )

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

    pick_different_line = (
        "" if target_fn else
        "Pick a DIFFERENT function each time — do not repeat the same suggestion pattern. "
    )
    system_prompt = (
        "You are a senior Python developer. Suggest ONE small, safe improvement. "
        "Keep your response under 800 chars. Just state WHAT to change and WHY in 2-3 sentences, "
        "then show the improved code snippet. No markdown headers, no numbered lists, no verbose analysis. "
        "Do not suggest changes to call_llm or imports. No new dependencies. No signature changes. "
        "No new type imports (e.g. Coroutine, Any) — only use names already imported in the file. "
        f"{pick_different_line}\n\n"
        f"{rules_block}\n\n"
        f"FUNCTION SIGNATURES:\n{sig_summary}"
        f"{focus_section}"
        f"{rejection_section}"
    )

    # Fix 2: when target_fn is set, send ONLY a short user prompt. The
    # function source is already in the system prompt (focus_section). Sending
    # the full file in the user message causes the proposer to fixate on
    # whatever function appears first in the file (e.g. gate_node) regardless
    # of target.
    if target_fn:
        proposer_user = (
            f"Objective: {objective}\n\n"
            f"Suggest ONE small improvement to the target function "
            f"`{target_fn['name']}` shown above. Do not reference any other function."
        )
    else:
        proposer_user = (
            f"Objective: {objective}\n\n"
            f"Code:\n```python\n{target_code[:10000]}"
            f"{'...[TRUNCATED]' if len(target_code) > 10000 else ''}\n```\n\n"
            f"Suggest one improvement:"
        )

    t0 = time.time()
    try:
        proposal = call_llm(
            PROPOSER_URL, PROPOSER_MODEL,
            system_prompt,
            proposer_user,
            num_ctx=ACTIVE_NUM_CTX,
            max_tokens=1500,
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
    if target_fn:
        retry_user = (
            f"Suggest one small improvement to function `{target_fn['name']}`:\n"
            f"```python\n{target_fn['source']}\n```\n"
            f"NEVER use raw 'await' in sync def -- use _run_async() instead."
        )
    else:
        retry_user = (
            f"Suggest one small improvement to this Python code (not call_llm). "
            f"NEVER use raw 'await' in sync def -- use _run_async() instead.\n"
            f"```python\n{target_code[:5000]}"
            f"{chr(10) + '...[TRUNCATED]' if len(target_code) > 5000 else ''}\n```"
        )
    try:
        proposal = call_llm(
            PROPOSER_URL, PROPOSER_MODEL,
            "You are a code expert. Be brief.",
            retry_user,
            num_ctx=ACTIVE_NUM_CTX,
            max_tokens=1500,
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
def execute_parallel(
    proposal: str,
    target_fn: dict | None,
    executor_fleet: list[dict],
    num_ctx: int,
) -> list[tuple[str, str, float, str]]:
    """Fan out proposal to all configured executors concurrently.

    Returns list of (label, new_code, elapsed_s, trust_level) in completion
    order (fastest first). Empty results are filtered. Two-pass executors run
    Pass 1 (generator) then Pass 2 (refiner) sequentially on their own thread;
    primary executors run directly. All threads run in parallel.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _build_exec_messages(ep: dict) -> tuple[str, str]:
        if target_fn:
            sys_msg = (
                "You are a Python implementer. You will be shown ONE function. "
                "Output the COMPLETE modified function. Do NOT output code "
                "outside this function. Start with def or async def. No "
                "markdown fences, no explanations, no imports, no surrounding "
                "code."
            )
            usr_msg = (
                f"Proposed change:\n{proposal[:1500]}\n\n"
                f"Current function (modify ONLY this):\n"
                f"```python\n{target_fn['source']}\n```\n\n"
                f"Output the COMPLETE modified function with name "
                f"`{target_fn['name']}` preserved exactly:"
            )
        else:
            sys_msg = (
                "You are a Python implementer. Output ONLY the modified "
                "function. Start with def or async def. No markdown fences, "
                "no explanations."
            )
            usr_msg = f"Implement this change:\n{proposal[:2000]}"
        return sys_msg, usr_msg

    def _run_primary(ep: dict) -> tuple[str, str, float, str]:
        t0 = time.time()
        label = ep["label"]
        sys_msg, usr_msg = _build_exec_messages(ep)
        try:
            result = call_llm(
                ep["url"], ep["model"], sys_msg, usr_msg,
                num_ctx=num_ctx, max_tokens=1500,
            )
            elapsed = time.time() - t0
            code = strip_fences(result).strip() if result else ""
            print(f"  [Executor/{label}] {len(code)} chars in {elapsed:.1f}s")
            return (label, code, elapsed, "primary")
        except Exception as e:
            elapsed = time.time() - t0
            print(f"  [Executor/{label}] FAIL {elapsed:.1f}s: {e}")
            return (label, "", elapsed, "primary")

    def _run_two_pass(ep: dict) -> tuple[str, str, float, str]:
        t0 = time.time()
        label = ep["label"]
        cfg = ep.get("two_pass", {}) or {}
        refiner_url = cfg.get("refiner_url", "")
        refiner_model = cfg.get("refiner_model", "")

        sys_msg, usr_msg = _build_exec_messages(ep)
        try:
            raw = call_llm(
                ep["url"], ep["model"], sys_msg, usr_msg,
                num_ctx=min(num_ctx, 4096), max_tokens=800,
            )
            raw = strip_fences(raw).strip() if raw else ""
            print(f"  [Executor/{label}/P1] {len(raw)} chars in {time.time()-t0:.1f}s")
        except Exception as e:
            print(f"  [Executor/{label}/P1] FAIL: {e}")
            return (label, "", time.time() - t0, "two_pass")

        if len(raw) < 30:
            print(f"  [Executor/{label}/P1] too short, skipping P2")
            return (label, "", time.time() - t0, "two_pass")

        if not refiner_url or not refiner_model:
            return (label, raw, time.time() - t0, "two_pass")

        fn_name = target_fn["name"] if target_fn else "the function"
        refine_sys = (
            "You are a Python code reviewer. Fix issues in the provided "
            "function implementation."
        )
        refine_usr = (
            f"Fix this Python function implementation. Requirements:\n"
            f"1. The function name MUST be exactly `{fn_name}` - rename if different\n"
            f"2. Fix any syntax errors\n"
            f"3. Preserve the core logic\n"
            f"4. Output ONLY the corrected function, no explanations\n\n"
            f"```python\n{raw}\n```"
        )
        try:
            refined = call_llm(
                refiner_url, refiner_model, refine_sys, refine_usr,
                num_ctx=num_ctx, max_tokens=1500,
            )
            elapsed = time.time() - t0
            code = strip_fences(refined).strip() if refined else raw
            print(f"  [Executor/{label}/P2] {len(code)} chars total in {elapsed:.1f}s")
            return (label, code, elapsed, "two_pass")
        except Exception as e:
            elapsed = time.time() - t0
            print(f"  [Executor/{label}/P2] refiner FAIL, using P1: {e}")
            return (label, raw, elapsed, "two_pass")

    results: list[tuple[str, str, float, str]] = []
    if not executor_fleet:
        return results

    print(f"  [Executor] parallel fan-out across {len(executor_fleet)} nodes")
    with ThreadPoolExecutor(max_workers=len(executor_fleet)) as pool:
        futures = {}
        for ep in executor_fleet:
            fn = _run_two_pass if ep.get("trust") == "two_pass" else _run_primary
            futures[pool.submit(fn, ep)] = ep["label"]
        for future in as_completed(futures):
            label, code, elapsed, trust = future.result()
            if code:
                results.append((label, code, elapsed, trust))
    return results


def run_cycle(cycle_num: int, objective: str, target_path: str,
              decisions_path: str) -> bool:
    target_code = read_file(target_path)
    if not target_code:
        print(f"  WARNING: target file empty: {target_path}")

    print(f"\n{'='*60}")
    print(f"  CYCLE {cycle_num}")
    print(f"{'='*60}")

    # Pick target function ONCE so proposer and executor see the same one.
    # When set, the executor is sandboxed to this function only (see below).
    target_fn = pick_target_function(target_code)

    proposal = propose(objective, target_path, target_code, target_fn=target_fn)
    if not proposal:
        log_decision(decisions_path, cycle_num, "PROPOSER_FAIL", "", False)
        return False

    # Proposal deduplication
    proposal_hash = hashlib.sha256(proposal[:500].encode()).hexdigest()[:16]
    if proposal_hash in _seen_proposal_hashes:
        print(f"  [Dedup] DUPLICATE proposal (hash={proposal_hash}), skipping cycle")
        log_decision(decisions_path, cycle_num, proposal[:800], "DUPLICATE: same proposal seen earlier in run", False)
        return False
    _seen_proposal_hashes.add(proposal_hash)

    # ========================================================
    # Parallel fan-out path - used when executor_fleet is
    # configured in the profile. Falls through to legacy single-
    # executor loop below when ACTIVE_EXECUTOR_FLEET is empty.
    # ========================================================
    if ACTIVE_EXECUTOR_FLEET:
        candidates = execute_parallel(
            proposal, target_fn, ACTIVE_EXECUTOR_FLEET, ACTIVE_NUM_CTX,
        )
        if not candidates:
            print("  [Executor] all nodes returned empty output")
            log_decision(
                decisions_path, cycle_num, proposal[:800],
                "EXECUTOR_FAIL (all nodes empty)", False,
            )
            return False

        rejection_reasons: list[str] = []
        for label, new_code, elapsed, trust in candidates:
            if target_fn:
                if not re.match(r"^(async\s+)?def\s+", new_code):
                    rejection_reasons.append(f"{label}:no-def")
                    continue
                fn_match = re.search(r"(async\s+)?def\s+(\w+)", new_code)
                if fn_match and fn_match.group(2) != target_fn["name"]:
                    rejection_reasons.append(
                        f"{label}:renamed({fn_match.group(2)})"
                    )
                    continue
                code_to_check = splice_function_back(
                    target_code, new_code,
                    target_fn["start"], target_fn["end"],
                )
                is_isolation_mode = True
                is_diff_mode = False
                diff_result = None
            else:
                diff_result = apply_diff(target_code, new_code)
                is_diff_mode = diff_result is not None
                is_isolation_mode = False
                code_to_check = diff_result if is_diff_mode else new_code

            syn_ok, syn_err = syntax_check(code_to_check)
            if not syn_ok:
                rejection_reasons.append(f"{label}:syntax")
                print(f"  [Executor/{label}] Syntax FAIL: {syn_err[:80]}")
                continue

            try:
                before_tree = ast.parse(target_code)
                after_tree = ast.parse(code_to_check)
                before_syms = [
                    n.name for n in ast.walk(before_tree)
                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                ]
                after_syms = [
                    n.name for n in ast.walk(after_tree)
                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                ]
                from collections import Counter as _PCounter
                after_counts = _PCounter(after_syms)
                missing = set(before_syms) - set(after_syms)
                dupes = {n: c for n, c in after_counts.items() if c > 1}
                if missing:
                    rejection_reasons.append(f"{label}:missing({sorted(missing)[:3]})")
                    continue
                if dupes:
                    rejection_reasons.append(f"{label}:dupes({list(dupes)[:3]})")
                    continue
            except SyntaxError:
                rejection_reasons.append(f"{label}:ast-parse")
                continue

            eval_system = (
                "You are a code quality evaluator. Reply PASS or FAIL on the "
                "FIRST LINE followed by reasoning. PASS only if the change is "
                "correct and improves the code. FAIL if it introduces "
                "breaking changes, wrong imports, or missing call-site updates."
            )
            if ACTIVE_EVALUATOR_PROMPT_EXTRA:
                eval_system += "\n\n" + ACTIVE_EVALUATOR_PROMPT_EXTRA
            if target_fn:
                eval_user = (
                    f"Objective: {objective}\n\n"
                    f"Context: only the function `{target_fn['name']}` is "
                    f"being modified. The rest of the file is unchanged.\n\n"
                    f"Original function:\n```python\n{target_fn['source']}\n```\n\n"
                    f"Proposed change description:\n{proposal[:1000]}\n\n"
                    f"New function:\n```python\n{new_code[:4000]}\n```\n\n"
                    f"PASS or FAIL? Evaluate the function change in isolation - "
                    f"assume all other symbols exist in the full file."
                )
            else:
                eval_user = (
                    f"Objective: {objective}\n\n"
                    f"Original:\n```python\n{target_code[:12000]}\n```\n\n"
                    f"Proposed:\n{proposal[:1000]}\n\n"
                    f"New code:\n```python\n{new_code[:4000]}\n```\n\n"
                    f"PASS or FAIL?"
                )
            t_eval = time.time()
            try:
                evaluation = call_llm(
                    EVALUATOR_URL, EVALUATOR_MODEL,
                    eval_system, eval_user,
                    num_ctx=ACTIVE_NUM_CTX, max_tokens=1500,
                )
            except Exception as e:
                rejection_reasons.append(f"{label}:eval-err")
                print(f"  [Evaluator/{label}] FAIL: {e}")
                continue
            if not evaluation or len(evaluation.strip()) == 0:
                rejection_reasons.append(f"{label}:eval-empty")
                continue
            first_line = evaluation.strip().split("\n")[0].strip().upper()
            accepted = first_line.startswith("PASS")
            print(
                f"  [Evaluator/{label}] {len(evaluation)} chars "
                f"({time.time()-t_eval:.1f}s) -> "
                f"{'ACCEPT' if accepted else 'REJECT'}"
            )
            log_decision(
                decisions_path, cycle_num, proposal[:800],
                f"[{label}/{trust}] {evaluation[:700]}",
                accepted, len(new_code),
            )
            if not accepted:
                eval_lines = evaluation.strip().split("\n")
                reason = " ".join(line.strip() for line in eval_lines[:3])[:200]
                _recent_rejections.append(f"{label}: {reason}")
                if len(_recent_rejections) > MAX_REJECTION_HISTORY:
                    _recent_rejections.pop(0)
                rejection_reasons.append(f"{label}:reject")
                continue

            try:
                if is_isolation_mode:
                    with open(target_path, "w", encoding="utf-8") as f:
                        f.write(code_to_check)
                    apply_result = (
                        f"Spliced isolated function {target_fn['name']} "
                        f"into {Path(target_path).name}"
                    )
                elif is_diff_mode:
                    with open(target_path, "w", encoding="utf-8") as f:
                        f.write(diff_result)
                    apply_result = "Applied via SEARCH/REPLACE diff"
                else:
                    apply_result = smart_apply(target_path, target_code, new_code)
                print(f"  [Apply/{label}] {apply_result}")

                written_code = read_file(target_path)
                post_ok, post_err = syntax_check(written_code)
                if post_ok:
                    try:
                        pre_tree = ast.parse(target_code)
                        post_tree = ast.parse(written_code)
                        pre_names = [
                            n.name for n in ast.walk(pre_tree)
                            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                        ]
                        post_names = [
                            n.name for n in ast.walk(post_tree)
                            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                        ]
                        from collections import Counter as _PC
                        post_counts = _PC(post_names)
                        missing2 = set(pre_names) - set(post_names)
                        dupes2 = {k: v for k, v in post_counts.items() if v > 1}
                        if missing2:
                            post_ok = False
                            post_err = f"missing symbols after write: {sorted(missing2)}"
                        elif dupes2:
                            post_ok = False
                            post_err = f"duplicate symbols after write: {dupes2}"
                    except SyntaxError as e:
                        post_ok = False
                        post_err = f"post-write AST parse failed: {e}"
                if not post_ok:
                    bak_path = Path(target_path).with_suffix(".py.bak")
                    if bak_path.exists():
                        bak_path.replace(target_path)
                    else:
                        with open(target_path, "w", encoding="utf-8") as f:
                            f.write(target_code)
                    print(f"  [ROLLBACK/{label}] {post_err[:200]}")
                    rejection_reasons.append(f"{label}:rollback")
                    continue

                gates_ok, gate_results = run_all_gates(Path(target_path))
                if not gates_ok:
                    bak_path = Path(target_path).with_suffix(".py.bak")
                    if bak_path.exists():
                        bak_path.replace(target_path)
                    else:
                        with open(target_path, "w", encoding="utf-8") as f:
                            f.write(target_code)
                    gate_summary = "; ".join(
                        f"{n}:{d}" for n, ok, d in gate_results if not ok
                    )
                    print(f"  [GATES/{label}] FAIL - restored {Path(target_path).name}")
                    rejection_reasons.append(f"{label}:gates({gate_summary[:60]})")
                    continue
                print(f"  [Cycle {cycle_num}] ACCEPTED via {label} "
                      f"({trust}, {elapsed:.1f}s)")
                return True
            except Exception as e:
                print(f"  [Apply/{label}] FAIL: {e}")
                rejection_reasons.append(f"{label}:apply-err")
                continue

        reason_str = ", ".join(rejection_reasons) or "all-rejected"
        print(f"  [Cycle {cycle_num}] all {len(candidates)} candidates rejected")
        log_decision(
            decisions_path, cycle_num, proposal[:800],
            f"All parallel candidates rejected: {reason_str[:600]}",
            False,
        )
        return False

    MAX_RETRIES = 3
    retries = 0

    while retries < MAX_RETRIES:
        print(f"\n  [Executor] {EXECUTOR_MODEL} @ Bifrost ... (Retry {retries + 1})")
        t0 = time.time()

        # Function-isolated executor sandbox: when a target function is
        # selected, the executor sees ONLY that function — never the full
        # 1000+ line file. This prevents the executor from corrupting
        # unrelated code.
        if target_fn:
            executor_system = (
                "You are a Python implementer. You will be shown ONE function. "
                "Output the COMPLETE modified function. Do NOT output code outside this function. "
                "Start with def or async def. No markdown fences, no explanations, "
                "no imports, no surrounding code. Just the full function body."
            )
            executor_user = (
                f"Proposed change:\n{proposal[:1500]}\n\n"
                f"Current function (this is ALL you can modify — do not output anything outside it):\n"
                f"```python\n{target_fn['source']}\n```\n\n"
                f"Output the COMPLETE modified function. Do NOT output code outside this function:"
            )
        else:
            executor_system = (
                "You are a Python implementer. Output ONLY the modified function. "
                "Start with def or async def. No markdown fences, no explanations, no full file rewrites. "
                "No prose before or after the code. Just the function."
            )
            executor_user = (
                f"Proposed change:\n{proposal[:1500]}\n\n"
                f"Current code:\n```python\n{target_code[:10000]}{'...[TRUNCATED]' if len(target_code) > 10000 else ''}\n```\n\n"
                f"Output ONLY the modified function:"
            )

        try:
            new_code = call_llm(
                EXECUTOR_URL, EXECUTOR_MODEL,
                executor_system,
                executor_user,
                num_ctx=ACTIVE_NUM_CTX,
                max_tokens=2000,
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

        if len(new_code) < 50:
            print("  [Executor] Output too short")
            retries += 1
            continue

        # Isolation mode: splice the executor's function back into the full
        # file and syntax-check the combined result. Otherwise, fall back to
        # the legacy diff/smart_apply path.
        diff_result = None
        is_diff_mode = False
        is_isolation_mode = False

        if target_fn:
            # Executor must have returned a function (def/async def)
            if not re.match(r"^(async\s+)?def\s+", new_code):
                print("  [Isolation] Executor did not output a function (no def/async def)")
                retries += 1
                continue
            # STRICT: executor must NOT rename the function — renaming creates
            # duplicate-definition corruption (see cycle 5 _extract_code disaster)
            fn_match = re.search(r"(async\s+)?def\s+(\w+)", new_code)
            if fn_match and fn_match.group(2) != target_fn["name"]:
                print(f"  [Isolation] REJECTED: executor renamed {target_fn['name']} -> {fn_match.group(2)}")
                log_decision(decisions_path, cycle_num, proposal[:800],
                             f"REJECTED: executor renamed function {target_fn['name']} -> {fn_match.group(2)}",
                             False, len(new_code))
                retries += 1
                continue
            # Splice the new function into the full file at the known line range
            code_to_check = splice_function_back(
                target_code, new_code, target_fn["start"], target_fn["end"]
            )
            is_isolation_mode = True
            print(f"  [Isolation] Spliced {target_fn['name']} back into full file "
                  f"(lines {target_fn['start']+1}-{target_fn['end']})")
        else:
            diff_result = apply_diff(target_code, new_code)
            is_diff_mode = diff_result is not None
            if is_diff_mode:
                print("  [DiffMode] SEARCH/REPLACE blocks detected")
                code_to_check = diff_result
            else:
                code_to_check = new_code

        # Syntax pre-filter: catch basic errors before burning an evaluator call
        syn_ok, syn_err = syntax_check(code_to_check)
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

        # Semantic structure check: compare top-level symbols before/after.
        # Catches renamed/deleted target functions and duplicate definitions
        # that py_compile doesn't notice (e.g., two functions with the same
        # name — the disaster from cycle 5 _extract_code).
        try:
            before_tree = ast.parse(target_code)
            after_tree = ast.parse(code_to_check)
            before_syms = [n.name for n in ast.walk(before_tree)
                           if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
            after_syms = [n.name for n in ast.walk(after_tree)
                          if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
            before_set = set(before_syms)
            after_set = set(after_syms)
            missing = before_set - after_set
            # Duplicate detection: same name appearing more than once
            from collections import Counter as _Counter
            after_counts = _Counter(after_syms)
            dupes = {name: cnt for name, cnt in after_counts.items() if cnt > 1}
            structure_err = None
            if missing:
                structure_err = f"missing symbols: {sorted(missing)}"
            elif dupes:
                structure_err = f"duplicate symbols: {dupes}"
            elif len(after_syms) != len(before_syms):
                structure_err = f"symbol count changed: {len(before_syms)} -> {len(after_syms)}"
            if structure_err:
                print(f"  [StructureCheck] FAIL: {structure_err}")
                log_decision(decisions_path, cycle_num, proposal[:800],
                             f"STRUCTURE_ERROR: {structure_err}", False, len(new_code))
                reason = f"STRUCTURE_ERROR: {structure_err[:120]}"
                _recent_rejections.append(reason)
                if len(_recent_rejections) > MAX_REJECTION_HISTORY:
                    _recent_rejections.pop(0)
                retries += 1
                continue
            print("  [StructureCheck] OK")
        except SyntaxError:
            # If AST parse of code_to_check fails (shouldn't — syntax_check
            # passed) treat as a syntax failure
            print("  [StructureCheck] AST parse failed after syntax_check passed")
            retries += 1
            continue

        print(f"\n  [Evaluator] {EVALUATOR_MODEL} @ Forge ... (Retry {retries + 1})")
        t0 = time.time()
        eval_system = (
            "You are a code quality evaluator. Reply PASS or FAIL on the FIRST LINE "
            "followed by reasoning. PASS only if the change is correct and improves the code. "
            "FAIL if it introduces breaking changes, wrong imports, or missing call-site updates."
        )
        if ACTIVE_EVALUATOR_PROMPT_EXTRA:
            eval_system += "\n\n" + ACTIVE_EVALUATOR_PROMPT_EXTRA

        # Build evaluator payload: when a function is isolated, send only that
        # function. Previously we sent target_code[:12000] which blinded the
        # evaluator to any function past line ~300.
        if target_fn:
            eval_user = (
                f"Objective: {objective}\n\n"
                f"Context: only the function `{target_fn['name']}` is being modified. "
                f"The rest of the file is unchanged.\n\n"
                f"Original function:\n```python\n{target_fn['source']}\n```\n\n"
                f"Proposed change description:\n{proposal[:1000]}\n\n"
                f"New function:\n```python\n{new_code[:4000]}\n```\n\n"
                f"PASS or FAIL? Evaluate the function change in isolation — "
                f"assume all other symbols exist in the full file."
            )
        else:
            eval_user = (
                f"Objective: {objective}\n\n"
                f"Original:\n```python\n{target_code[:12000]}\n```\n\n"
                f"Proposed:\n{proposal[:1000]}\n\n"
                f"New code:\n```python\n{new_code[:4000]}\n```\n\n"
                f"PASS or FAIL?"
            )

        try:
            evaluation = call_llm(
                EVALUATOR_URL, EVALUATOR_MODEL,
                eval_system,
                eval_user,
                num_ctx=ACTIVE_NUM_CTX,
                max_tokens=1500,
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

        # Empty evaluator retry: if 0 chars, sleep 5s and retry same payload once
        if not evaluation or len(evaluation.strip()) == 0:
            print(f"  [Evaluator] 0 chars ({time.time()-t0:.0f}s), retrying after 5s...")
            time.sleep(5)
            try:
                evaluation = call_llm(
                    EVALUATOR_URL, EVALUATOR_MODEL,
                    eval_system,
                    eval_user,
                    num_ctx=ACTIVE_NUM_CTX,
                    max_tokens=1500,
                )
            except Exception:
                evaluation = ""
            if not evaluation or len(evaluation.strip()) == 0:
                print("  [Evaluator] Still empty after retry")
                log_decision(decisions_path, cycle_num, proposal[:800],
                             "FAIL: Evaluator returned empty response twice", False, len(new_code))
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
                if is_isolation_mode:
                    with open(target_path, 'w', encoding='utf-8') as f:
                        f.write(code_to_check)
                    apply_result = (
                        f"Spliced isolated function {target_fn['name']} "
                        f"into {Path(target_path).name}"
                    )
                elif is_diff_mode:
                    with open(target_path, 'w', encoding='utf-8') as f:
                        f.write(diff_result)
                    apply_result = "Applied via SEARCH/REPLACE diff"
                else:
                    apply_result = smart_apply(target_path, target_code, new_code)
                print(f"  [Apply] {apply_result}")

                # Rollback guard: verify target file still compiles AND
                # preserves the symbol set (no missing target_fn, no dupes)
                written_code = read_file(target_path)
                post_ok, post_err = syntax_check(written_code)
                if post_ok:
                    try:
                        pre_tree = ast.parse(target_code)
                        post_tree = ast.parse(written_code)
                        pre_names = [n.name for n in ast.walk(pre_tree)
                                     if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
                        post_names = [n.name for n in ast.walk(post_tree)
                                      if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
                        from collections import Counter as _C
                        post_counts = _C(post_names)
                        missing = set(pre_names) - set(post_names)
                        dupes = {k: v for k, v in post_counts.items() if v > 1}
                        if missing:
                            post_ok = False
                            post_err = f"missing symbols after write: {sorted(missing)}"
                        elif dupes:
                            post_ok = False
                            post_err = f"duplicate symbols after write: {dupes}"
                    except SyntaxError as e:
                        post_ok = False
                        post_err = f"post-write AST parse failed: {e}"
                if not post_ok:
                    bak_path = Path(target_path).with_suffix(".py.bak")
                    if bak_path.exists():
                        bak_path.replace(target_path)
                    else:
                        with open(target_path, 'w', encoding='utf-8') as f:
                            f.write(target_code)
                    print(f"  [ROLLBACK] accepted change broke syntax: {post_err[:200]}")
                    log_decision(decisions_path, cycle_num, proposal[:800],
                                 f"ROLLBACK: {post_err[:400]}", False, len(new_code))
                    retries += 1
                    continue

                # Session O-SAFETY: canary gates before accepting change
                gates_ok, gate_results = run_all_gates(Path(target_path))
                if not gates_ok:
                    bak_path = Path(target_path).with_suffix(".py.bak")
                    if bak_path.exists():
                        bak_path.replace(target_path)
                    else:
                        with open(target_path, "w", encoding="utf-8") as _f:
                            _f.write(target_code)
                    gate_summary = "; ".join(f"{n}:{d}" for n, ok, d in gate_results if not ok)
                    print(f"  [GATES] FAIL -- restored {Path(target_path).name}")
                    for gn, ok, detail in gate_results:
                        print(f"    {'OK' if ok else 'FAIL'} {gn}: {detail}")
                    log_decision(decisions_path, cycle_num, proposal[:800],
                                 f"GATE_FAILURE: {gate_summary[:400]}", False, len(new_code))
                    retries += 1
                    continue
                print("  [GATES] all passed")

                return True
            except Exception as e:
                print(f"  [Apply] FAIL: {str(e)}")
                retries += 1
                continue

        # Track rejection reason for feedback loop (include reasoning, not just FAIL)
        eval_lines = evaluation.strip().split("\n")
        reason = " ".join(line.strip() for line in eval_lines[:3])[:200]
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
    global ACTIVE_EVALUATOR_PROMPT_EXTRA, ACTIVE_TEMPERATURE, ACTIVE_EXECUTOR_FLEET

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
    ACTIVE_TEMPERATURE = profile.get("temperature", 0.7)
    ACTIVE_EXECUTOR_FLEET = profile.get("executor_fleet", [])

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
        # Use --decisions if explicitly passed, otherwise default to profile dir
        if args.decisions != "decisions.md":
            decisions_path = args.decisions
        else:
            decisions_path = str(Path(__file__).parent / "profiles" / profile_upper / f"decisions_{profile_name}.md")
        objective = profile.get("objective", "Improve the code quality and robustness.")
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
    warmup_models()

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
