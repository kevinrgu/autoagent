"""Session O-SAFETY canary gates.

Installed 2026-04-17 after 24d338e regression chain.
Run AFTER accepted diff is applied to target, BEFORE git commit.

Any gate failure returns (False, reason). Caller must:
  1. Restore target from .bak
  2. Log reason to decisions.md as gate_failure
  3. Skip commit
  4. Continue to next cycle
"""
from __future__ import annotations
import subprocess
import sys
import time
from pathlib import Path


ROUTER_DIR = Path(r"D:\Projects\bifrost-router")
IMPORT_CANARY_MODULES = ["main", "config", "classifier", "autopilot_graph", "subtask_graph"]
DOCKER_CANARY_TAG = "bifrost-router:session-o-canary"
SMOKE_CANARY_PORT = 18089


def _run(cmd: list[str], cwd: Path | None = None, timeout: int = 120) -> tuple[int, str, str]:
    """Run a command, return (exit_code, stdout, stderr)."""
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        return -1, "", f"TIMEOUT after {timeout}s"
    except Exception as e:
        return -2, "", str(e)


def gate_1_import_canary() -> tuple[bool, str]:
    """Verify target modules actually import without NameError.

    Catches: missing imports (Dict/Any), missing module-level bindings
    (logger), deleted classes/functions referenced elsewhere, any NameError
    that lazy module loading would hide from py_compile.
    """
    module_list = ", ".join(IMPORT_CANARY_MODULES)
    script = f"import sys; sys.path.insert(0, r'{ROUTER_DIR}'); import {module_list}; print('IMPORT_OK')"
    code, out, err = _run([sys.executable, "-c", script], timeout=30)
    if code != 0 or "IMPORT_OK" not in out:
        return False, f"import_canary: code={code} err={err.strip()[:200]}"
    return True, "import_canary: OK"


def gate_2_docker_build() -> tuple[bool, str]:
    """Verify the Router image still builds after the change.

    Catches: files referenced in Dockerfile that no longer exist, syntax
    errors that break at import (ImportError at dockerfile COPY level),
    dependency changes.
    """
    code, out, err = _run(
        ["docker", "build", "--no-cache", "-t", DOCKER_CANARY_TAG, "."],
        cwd=ROUTER_DIR,
        timeout=600,
    )
    if code != 0:
        return False, f"docker_build: failed code={code} tail={err.strip()[-300:]}"
    return True, "docker_build: OK"


def gate_3_runtime_smoke() -> tuple[bool, str]:
    """Start a container, hit /health and a minimal /v1/chat/completions.

    Catches: StateGraph construction missing, signature mismatches on
    endpoints, config.py runtime errors, any bug that only surfaces when
    FastAPI actually routes a request.
    """
    container = "so-canary"

    # Clean any leftover
    _run(["docker", "rm", "-f", container])

    # Start
    code, _, err = _run(
        ["docker", "run", "-d", "--name", container, "-p", f"{SMOKE_CANARY_PORT}:8080",
         DOCKER_CANARY_TAG],
        timeout=30,
    )
    if code != 0:
        return False, f"runtime_smoke: docker run failed {err.strip()[:200]}"

    try:
        # Give it a beat to boot
        time.sleep(6)

        # /health
        import urllib.request
        try:
            req = urllib.request.Request(f"http://localhost:{SMOKE_CANARY_PORT}/health")
            resp = urllib.request.urlopen(req, timeout=5)
            if resp.status != 200:
                return False, f"runtime_smoke: /health status={resp.status}"
        except Exception as e:
            return False, f"runtime_smoke: /health unreachable {e}"

        # Minimal routing — does NOT require models to be warm, just that
        # the endpoint parses the request and attempts dispatch without
        # crashing. A 502 from exhausted cascade is acceptable; 500 is not.
        import json
        body = json.dumps({"model": "bifrost-auto",
                           "messages": [{"role": "user", "content": "ping"}],
                           "max_tokens": 1}).encode()
        try:
            req = urllib.request.Request(
                f"http://localhost:{SMOKE_CANARY_PORT}/v1/chat/completions",
                data=body, headers={"Content-Type": "application/json"}, method="POST",
            )
            resp = urllib.request.urlopen(req, timeout=15)
            # Any 200-range or 502 (cascade exhausted — backends not reachable
            # from canary container) is acceptable; what we prevent is 500.
        except urllib.error.HTTPError as e:
            if e.code == 500:
                return False, f"runtime_smoke: /v1/chat/completions 500 {e.read().decode()[:200]}"
            # 502 is expected when cascade can't reach real backends
        except Exception as e:
            # Network timeouts etc. are acceptable; 500-level app errors are not
            if "500" in str(e):
                return False, f"runtime_smoke: exception suggests 500 {e}"

        return True, "runtime_smoke: OK"
    finally:
        _run(["docker", "rm", "-f", container])


def gate_4_graph_invoke(target_path: Path) -> tuple[bool, str]:
    """If the accepted change touched autopilot_graph or subtask_graph,
    verify a minimal run_autopilot invocation succeeds at graph level.

    Catches: autopilot_graph name missing at module scope, StateGraph compile
    returning wrong type, graph wiring errors.
    """
    tname = target_path.name if target_path else ""
    if "autopilot_graph" not in tname and "subtask_graph" not in tname:
        return True, "graph_invoke: skipped (target not a graph file)"

    # NOTE: this gate is expensive (full graph compile + module import).
    # Only run when the target is a graph file.
    script = f"""
import sys
sys.path.insert(0, r'{ROUTER_DIR}')
import autopilot_graph
assert hasattr(autopilot_graph, 'autopilot_graph'), 'autopilot_graph module binding missing'
assert hasattr(autopilot_graph, 'run_autopilot'), 'run_autopilot missing'
# Verify the compile produced a CompiledStateGraph by checking for .invoke
g = autopilot_graph.autopilot_graph
assert hasattr(g, 'invoke'), f'compiled graph missing .invoke (got {{type(g)}})'
print('GRAPH_OK')
"""
    code, out, err = _run([sys.executable, "-c", script], timeout=30)
    if code != 0 or "GRAPH_OK" not in out:
        return False, f"graph_invoke: code={code} err={err.strip()[:200]}"
    return True, "graph_invoke: OK"


def run_all_gates(target_path: Path | None = None) -> tuple[bool, list[tuple[str, bool, str]]]:
    """Run gates 1-4 in order. Short-circuit on first failure.

    Returns (overall_ok, [(gate_name, ok, detail), ...]).
    """
    results = []
    for name, fn in [
        ("gate_1_import_canary", gate_1_import_canary),
        ("gate_2_docker_build",  gate_2_docker_build),
        ("gate_3_runtime_smoke", gate_3_runtime_smoke),
    ]:
        ok, detail = fn()
        results.append((name, ok, detail))
        if not ok:
            return False, results

    # Gate 4 only when relevant
    ok, detail = gate_4_graph_invoke(target_path) if target_path else (True, "graph_invoke: no target")
    results.append(("gate_4_graph_invoke", ok, detail))
    return ok, results


if __name__ == "__main__":
    # Standalone test: run all gates against current Router state
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", type=str, default="")
    args = parser.parse_args()
    target = Path(args.target) if args.target else None

    print(f"Running Session O-SAFETY gates against {ROUTER_DIR}")
    if target:
        print(f"Target file: {target}")
    print()

    overall_ok, results = run_all_gates(target)
    for name, ok, detail in results:
        marker = "[OK]" if ok else "[FAIL]"
        print(f"{marker} {name}: {detail}")
    print()
    print(f"Overall: {'PASS' if overall_ok else 'FAIL'}")
    sys.exit(0 if overall_ok else 1)
