"""
architect.py — BIFROST ARCHITECT-0
Daily fleet health + drift detection + recommendations.
Writes ARCHITECT_REPORT_{date}.md to pk-upload staging dir.
Run after morning_report.py (06:30 Scheduled Task).

SAFETY: Read-only with ONE scoped maintenance action -- when Ollama version drift
is detected on any node, architect calls update_ollama.py to upgrade in place.
Model files, config, and Scheduled Tasks are never modified. All actions are
logged to L:\\temp\\pk-upload\\OLLAMA_UPDATE_LOG.md.
"""

import json
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

try:
    import update_ollama  # co-located in autoagent dir
except Exception as _e:  # noqa: BLE001 - optional dep
    update_ollama = None

# -- Paths ---------------------------------------------------------------------
AUTOAGENT_DIR  = Path(r"C:\Users\jhpri\projects\autoagent")
ROUTER_DIR     = Path(r"D:\Projects\bifrost-router")
PK_UPLOAD_DIR  = Path(r"L:\temp\pk-upload")
PROFILES_PATH  = AUTOAGENT_DIR / "profiles.json"
OVERNIGHT_LOG  = AUTOAGENT_DIR / "overnight_log.txt"
MORNING_REPORT = AUTOAGENT_DIR / "MORNING_REPORT.md"
FLEET_CONFIG   = ROUTER_DIR / "fleet_config.json"
OLLAMA_VER_CACHE = Path(r"L:\temp\ollama_version_cache.json")

# -- Fleet endpoints (read-only checks) ---------------------------------------
ROUTER_HEALTH  = "http://192.168.2.4:8089/health"
FORGE_OLLAMA   = "http://192.168.2.50:11434"
BIFROST_OLLAMA = "http://192.168.2.33:11434"
HEARTH_OLLAMA  = "http://192.168.2.4:11434"
KB_HEALTH_URL  = "http://192.168.2.4:8091/health"
KB_STATS_URL   = "http://192.168.2.4:8091/stats?project=default"

HEARTH_SSH = "jhpri@192.168.2.4"
FORGE_SSH  = "jhpritch@192.168.2.50"

TASK_NAMES = ["BIFROST-Overnight-Training", "BIFROST-Session-O-AutoAgent"]


# -- Helpers -------------------------------------------------------------------

def _ssh(host: str, cmd: str, timeout: int = 10) -> tuple[int, str, str]:
    try:
        r = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=5", host, cmd],
            capture_output=True, text=True, timeout=timeout,
        )
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except Exception as e:
        return -1, "", str(e)


def _get(url: str, timeout: int = 5) -> tuple[bool, object]:
    try:
        r = requests.get(url, timeout=timeout)
        if r.status_code == 200:
            try:
                return True, r.json()
            except Exception:
                return True, r.text
        return False, f"HTTP {r.status_code}"
    except Exception as e:
        return False, str(e)


def _safe(fn, *args, **kwargs):
    """Run a collection function, return (result, error_str)."""
    try:
        return fn(*args, **kwargs), None
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


# -- Collection functions ------------------------------------------------------

def collect_overnight() -> dict:
    """Parse overnight_log.txt: runs in last 48h, flag if none in 26h."""
    if not OVERNIGHT_LOG.exists():
        return {"runs": [], "missed_window": True, "note": "overnight_log.txt not found"}

    text = OVERNIGHT_LOG.read_text(encoding="utf-8", errors="replace")
    now = datetime.now(timezone.utc)
    cutoff_48h = now - timedelta(hours=48)
    cutoff_26h = now - timedelta(hours=26)

    # Run header: "# Run 20260418_0200 — 2026-04-18T03:09:47.634373"
    run_re = re.compile(
        r"#\s*Run\s+(\d{8}_\d{4})\s+[—-]+\s+(\d{4}-\d{2}-\d{2}T[\d:.]+)"
    )
    # Per-profile line: "  coding     OK        1368.9s" or FAIL
    prof_re = re.compile(r"^\s+(coding|general|research)\s+(\w+)\s+([\d.]+)s")
    accept_re = re.compile(r"accepted target changes", re.I)

    runs: list[dict] = []
    current: dict | None = None
    for line in text.splitlines():
        m = run_re.match(line)
        if m:
            if current:
                runs.append(current)
            try:
                ts = datetime.fromisoformat(m.group(2)).replace(tzinfo=timezone.utc)
            except Exception:
                ts = None
            current = {
                "run_id": m.group(1),
                "timestamp": ts.isoformat() if ts else m.group(2),
                "ts_obj": ts,
                "coding": None,
                "general": None,
                "research": None,
                "accepted_changes": False,
            }
            continue
        if current is None:
            continue
        pm = prof_re.match(line)
        if pm:
            current[pm.group(1)] = {"status": pm.group(2), "duration_s": float(pm.group(3))}
            continue
        if accept_re.search(line):
            current["accepted_changes"] = True

    if current:
        runs.append(current)

    recent_48h = [r for r in runs if r["ts_obj"] and r["ts_obj"] >= cutoff_48h]
    recent_26h = [r for r in runs if r["ts_obj"] and r["ts_obj"] >= cutoff_26h]

    # Strip ts_obj for serialization
    for r in recent_48h:
        r.pop("ts_obj", None)

    return {
        "runs_48h": recent_48h,
        "missed_window": len(recent_26h) == 0,
        "last_run_ts": runs[-1]["timestamp"] if runs else None,
        "total_runs_parsed": len(runs),
    }


def collect_accept_rates() -> dict:
    """Per-profile 7-day rolling accept rate vs prior 7-day."""
    results: dict[str, dict] = {}
    accepted_re = re.compile(r"^\*\*Accepted:\*\*\s*YES", re.MULTILINE)
    total_re = re.compile(r"^\*\*Accepted:\*\*", re.MULTILINE)

    # Run header in decisions file: "## Cycle N -- 2026-04-18 06:04:08 UTC"
    cycle_re = re.compile(
        r"##\s*Cycle\s+\d+\s*--\s*(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})"
    )

    now = datetime.now(timezone.utc)
    cutoff_7d = now - timedelta(days=7)
    cutoff_14d = now - timedelta(days=14)

    for profile in ("coding", "general", "research"):
        pdir = AUTOAGENT_DIR / "profiles" / profile.upper()
        if not pdir.exists():
            results[profile] = {"error": "profile dir not found"}
            continue

        files = sorted(pdir.glob(f"decisions_{profile}_2*.md"))
        cur_yes = cur_total = prior_yes = prior_total = 0

        for f in files:
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue

            # Split into per-cycle chunks keyed by cycle timestamp
            positions = [(m.start(), m.group(1)) for m in cycle_re.finditer(text)]
            if not positions:
                continue
            positions.append((len(text), None))

            for i in range(len(positions) - 1):
                start, ts_str = positions[i]
                end, _ = positions[i + 1]
                chunk = text[start:end]
                try:
                    ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                except Exception:
                    continue
                if total_re.search(chunk):
                    is_yes = bool(accepted_re.search(chunk))
                    if ts >= cutoff_7d:
                        cur_total += 1
                        if is_yes:
                            cur_yes += 1
                    elif ts >= cutoff_14d:
                        prior_total += 1
                        if is_yes:
                            prior_yes += 1

        cur_rate = (cur_yes / cur_total * 100) if cur_total else None
        prior_rate = (prior_yes / prior_total * 100) if prior_total else None
        if cur_rate is not None and prior_rate is not None:
            delta = cur_rate - prior_rate
            if abs(delta) < 2:
                trend = "flat"
            else:
                trend = "up" if delta > 0 else "down"
        else:
            delta = None
            trend = "insufficient_data"

        results[profile] = {
            "current_7d": cur_rate,
            "prior_7d": prior_rate,
            "current_samples": cur_total,
            "prior_samples": prior_total,
            "delta": delta,
            "trend": trend,
        }
    return results


def collect_git_delta() -> dict:
    """Recent commits in autoagent + bifrost-router. Flag missing auto-commit."""
    out: dict[str, dict] = {}
    for label, repo in (("autoagent", AUTOAGENT_DIR), ("bifrost-router", ROUTER_DIR)):
        try:
            r = subprocess.run(
                ["git", "-C", str(repo), "log", "--oneline",
                 "--since=48 hours ago"],
                capture_output=True, text=True, timeout=10,
                encoding="utf-8", errors="replace",
            )
            commits = [ln for ln in r.stdout.splitlines() if ln.strip()]
            out[label] = {"count": len(commits), "commits": commits[:20]}
        except Exception as e:
            out[label] = {"error": str(e), "count": 0, "commits": []}

    # Flag missing autoagent auto-commit despite active scheduled task
    out["missing_recent_autocommit"] = out.get("autoagent", {}).get("count", 0) == 0
    return out


def collect_service_health() -> dict:
    """Router + bifrost-kb + Ollama (3 nodes) + Forge systemd services."""
    health: dict[str, dict] = {}

    ok, data = _get(ROUTER_HEALTH)
    health["router"] = {"status": "green" if ok else "red", "detail": data}

    # bifrost-kb -- RAG enrichment service on Hearth k3d :8091
    kb_ok, kb_data = _get(KB_HEALTH_URL)
    if kb_ok:
        stats_ok, stats_data = _get(KB_STATS_URL)
        if stats_ok and isinstance(stats_data, dict):
            active = stats_data.get("active_project") or {}
            chunks = active.get("chunks", "?")
            docs = len(active.get("documents") or [])
            proj_count = len(stats_data.get("projects") or [])
            note = f"{chunks} chunks, {docs} docs, {proj_count} projects"
        else:
            note = "stats unavailable"
        health["bifrost_kb"] = {"status": "green", "ok": True, "note": note}
    else:
        health["bifrost_kb"] = {"status": "red", "ok": False,
                                 "note": str(kb_data)[:120]}

    for label, url in (
        ("ollama_bifrost", BIFROST_OLLAMA),
        ("ollama_hearth", HEARTH_OLLAMA),
        ("ollama_forge", FORGE_OLLAMA),
    ):
        ok, data = _get(f"{url}/api/tags")
        if ok and isinstance(data, dict):
            n = len(data.get("models", []))
            health[label] = {"status": "green", "model_count": n}
        else:
            health[label] = {"status": "red", "detail": str(data)[:120]}

    # Forge systemd — pipe-to-cat prevents pagers and the non-zero "inactive"
    # exit code from killing the whole line.
    rc, out, err = _ssh(
        FORGE_SSH,
        "for s in ollama lemonade flm; do "
        "printf '%s ' $s; systemctl is-active $s || true; done"
    )
    svc: dict[str, str] = {}
    if rc == 0:
        for line in out.splitlines():
            parts = line.strip().split()
            if len(parts) >= 2:
                svc[parts[0]] = parts[1]
    else:
        svc["error"] = err or "ssh failed"
    health["forge_services"] = svc
    return health


def collect_task_states() -> dict:
    """Scheduled Task states for overnight-training + session-o."""
    ps = (
        "Get-ScheduledTask -TaskName "
        + ",".join(f"'{t}'" for t in TASK_NAMES)
        + " -ErrorAction SilentlyContinue | "
        + "Select-Object TaskName,State | ConvertTo-Json -Compress"
    )
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True, text=True, timeout=20,
        )
        raw = r.stdout.strip()
        if not raw:
            return {"error": "no output", "stderr": r.stderr.strip()}
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            parsed = [parsed]
        states: dict[str, str] = {}
        for item in parsed:
            name = item.get("TaskName")
            state_v = item.get("State")
            if isinstance(state_v, int):
                state_v = {3: "Ready", 1: "Disabled", 4: "Running"}.get(state_v, str(state_v))
            states[name] = state_v
        for t in TASK_NAMES:
            states.setdefault(t, "NOT_FOUND")
        return states
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def collect_model_inventory() -> dict:
    """Ensure models referenced in profiles.json exist on the target node."""
    if not PROFILES_PATH.exists():
        return {"error": "profiles.json not found"}

    try:
        profiles = json.loads(PROFILES_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        return {"error": f"parse failed: {e}"}

    url_to_node = {
        BIFROST_OLLAMA: "bifrost",
        HEARTH_OLLAMA: "hearth",
        FORGE_OLLAMA: "forge",
    }

    # Cache Ollama catalog per base URL
    catalog_cache: dict[str, list[str]] = {}

    def catalog(url: str) -> list[str] | None:
        if url in catalog_cache:
            return catalog_cache[url]
        ok, data = _get(f"{url}/api/tags")
        if not ok or not isinstance(data, dict):
            catalog_cache[url] = None
            return None
        names = [m.get("name", "") for m in data.get("models", [])]
        catalog_cache[url] = names
        return names

    missing: list[dict] = []
    checked: list[dict] = []

    for pname, pcfg in profiles.get("profiles", {}).items():
        for role in ("proposer", "executor", "evaluator"):
            rc = pcfg.get(role)
            if not rc:
                continue
            model = rc.get("model")
            url = rc.get("url", "")
            base = url.split("/v1/")[0] if "/v1/" in url else url.rsplit("/api", 1)[0]
            base = base.rstrip("/")
            node = url_to_node.get(base, base)
            names = catalog(base)
            entry = {"profile": pname, "role": role, "model": model, "node": node}
            if names is None:
                entry["result"] = "node_unreachable"
                missing.append(entry)
            else:
                # Match exact name or name:tag with either side optional
                model_base = model.split(":")[0]
                found = any(
                    n == model or n.startswith(model + ":") or n.split(":")[0] == model_base
                    for n in names
                )
                if not found:
                    entry["result"] = "missing"
                    missing.append(entry)
            checked.append(entry)

    return {"checked": checked, "missing": missing, "catalogs": catalog_cache}


def collect_ollama_versions() -> dict:
    """Installed vs GitHub-latest; cached daily."""
    now = datetime.now(timezone.utc)
    latest: str | None = None
    cache_age_h: float | None = None

    # Read cache
    if OLLAMA_VER_CACHE.exists():
        try:
            cache = json.loads(OLLAMA_VER_CACHE.read_text(encoding="utf-8"))
            ts = datetime.fromisoformat(cache.get("ts", ""))
            cache_age_h = (now - ts).total_seconds() / 3600
            if cache_age_h < 24:
                latest = cache.get("latest")
        except Exception:
            pass

    if latest is None:
        try:
            r = requests.get(
                "https://api.github.com/repos/ollama/ollama/releases/latest",
                timeout=10,
                headers={"Accept": "application/vnd.github+json"},
            )
            if r.status_code == 200:
                latest = (r.json().get("tag_name") or "").lstrip("v")
                try:
                    OLLAMA_VER_CACHE.parent.mkdir(parents=True, exist_ok=True)
                    OLLAMA_VER_CACHE.write_text(
                        json.dumps({"ts": now.isoformat(), "latest": latest}),
                        encoding="utf-8",
                    )
                except Exception:
                    pass
        except Exception as e:
            latest = f"lookup_failed:{e}"

    def parse_ver(text: str) -> str | None:
        m = re.search(r"ollama version is\s+(\S+)", text)
        return m.group(1) if m else None

    # Bifrost local
    try:
        r = subprocess.run(["ollama", "--version"], capture_output=True, text=True, timeout=5)
        bifrost_v = parse_ver(r.stdout + r.stderr)
    except Exception as e:
        bifrost_v = f"error:{e}"

    _, hearth_out, _ = _ssh(HEARTH_SSH, "ollama --version")
    hearth_v = parse_ver(hearth_out)

    _, forge_out, _ = _ssh(FORGE_SSH, "ollama --version")
    forge_v = parse_ver(forge_out)

    def compare(installed: str | None) -> dict:
        if not installed or not latest or ":" in str(latest):
            return {"installed": installed, "latest": latest, "behind": None}
        try:
            ip = tuple(int(x) for x in installed.split("."))
            lp = tuple(int(x) for x in str(latest).split("."))
            behind = ip < lp
        except Exception:
            behind = None
        return {"installed": installed, "latest": latest, "behind": behind}

    return {
        "latest": latest,
        "cache_age_hours": cache_age_h,
        "bifrost": compare(bifrost_v),
        "hearth": compare(hearth_v),
        "forge": compare(forge_v),
    }


def collect_pk_sync_staleness() -> dict:
    """Hours since most recent pk_sync_manifest.txt."""
    manifest = PK_UPLOAD_DIR / "pk_sync_manifest.txt"
    if not manifest.exists():
        return {"found": False, "age_hours": None}
    try:
        mtime = datetime.fromtimestamp(manifest.stat().st_mtime, tz=timezone.utc)
        age_h = (datetime.now(timezone.utc) - mtime).total_seconds() / 3600
        return {"found": True, "age_hours": age_h, "mtime": mtime.isoformat()}
    except Exception as e:
        return {"found": False, "error": str(e)}


# -- Recommendations -----------------------------------------------------------

def _rec(level: str, text: str, category: str = "general") -> dict:
    return {"level": level, "category": category, "text": text}


def generate_recommendations(results: dict) -> list[dict]:
    """Prioritized rec list. RED = blocking, YELLOW = advisory, GREEN = info."""
    recs: list[dict] = []

    # Scheduled Task states
    tasks = results.get("tasks") or {}
    if isinstance(tasks, dict) and "error" not in tasks:
        for name in TASK_NAMES:
            st = tasks.get(name)
            if st == "Disabled":
                recs.append(_rec("RED", f"Scheduled Task '{name}' is Disabled — re-enable before next run", "tasks"))
            elif st == "NOT_FOUND":
                recs.append(_rec("YELLOW", f"Scheduled Task '{name}' not registered on this machine", "tasks"))

    # Overnight window. If both Scheduled Tasks are Ready, the missed window is
    # likely a just-re-enabled grace period rather than a real outage -- downgrade
    # to YELLOW so we don't wake people up for a 02:00 run that hasn't fired yet.
    on = results.get("overnight") or {}
    if isinstance(on, dict):
        if on.get("missed_window"):
            both_ready = (
                isinstance(tasks, dict)
                and "error" not in tasks
                and all(tasks.get(t) == "Ready" for t in TASK_NAMES)
            )
            if both_ready:
                recs.append(_rec("YELLOW",
                    "No overnight run in 26+ hours -- tasks are Ready, next fire at 02:00",
                    "overnight"))
            else:
                recs.append(_rec("RED",
                    "No overnight run in 26+ hours -- check task states and overnight_log.txt",
                    "overnight"))
        for run in on.get("runs_48h", []) or []:
            fails = [p for p in ("coding", "general", "research")
                     if isinstance(run.get(p), dict) and run[p].get("status") not in (None, "OK")]
            if fails:
                recs.append(_rec("YELLOW",
                    f"Run {run.get('run_id')} at {run.get('timestamp')} had non-OK: {', '.join(fails)}",
                    "overnight"))

    # Accept-rate drift
    ar = results.get("accept_rates") or {}
    if isinstance(ar, dict):
        for profile, data in ar.items():
            if not isinstance(data, dict) or "error" in data:
                continue
            d = data.get("delta")
            if d is None:
                continue
            if abs(d) > 15:
                level = "YELLOW"
                direction = "dropped" if d < 0 else "climbed"
                recs.append(_rec(level,
                    f"{profile.upper()} accept rate {direction} >15% "
                    f"(7d: {data['prior_7d']:.0f}% -> {data['current_7d']:.0f}%) — "
                    f"review recent decisions files",
                    "accept_rate"))

    # Service health
    health = results.get("health") or {}
    for svc, info in health.items():
        if not (isinstance(info, dict) and info.get("status") == "red"):
            continue
        if svc == "bifrost_kb":
            recs.append(_rec("RED",
                "bifrost-kb container is down -- RAG enrichment unavailable. "
                "Check: ssh jhpri@192.168.2.4 'docker ps | grep bifrost-kb'",
                "health"))
        else:
            detail = str(info.get("detail", info.get("note", "")))[:100]
            recs.append(_rec("RED",
                f"Service '{svc}' unreachable: {detail}",
                "health"))
    # Forge systemd
    fsvc = health.get("forge_services") or {}
    if isinstance(fsvc, dict):
        for s, state in fsvc.items():
            if s == "error":
                recs.append(_rec("YELLOW", f"Forge systemd check failed: {state}", "health"))
            elif state not in ("active", "activating"):
                recs.append(_rec("YELLOW", f"Forge service '{s}' is '{state}' (expected 'active')", "health"))

    # Git activity
    git = results.get("git_delta") or {}
    if isinstance(git, dict) and git.get("missing_recent_autocommit"):
        recs.append(_rec("YELLOW",
            "No autoagent auto-commit in 48h despite Ready tasks — check overnight_run.py commit step",
            "git"))

    # Model inventory
    models = results.get("models") or {}
    for m in models.get("missing", []) or []:
        if m.get("result") == "missing":
            recs.append(_rec("YELLOW",
                f"Model '{m['model']}' referenced in profiles.json ({m['profile']}/{m['role']}) "
                f"not found on node '{m['node']}'",
                "models"))
        elif m.get("result") == "node_unreachable":
            recs.append(_rec("YELLOW",
                f"Cannot verify model inventory on node '{m['node']}' (unreachable)",
                "models"))

    # Ollama version drift. If an auto-update ran, results["ollama_update_attempt"]
    # holds the updater's per-node result list; use it to choose GREEN vs YELLOW.
    oll = results.get("ollama") or {}
    attempt = results.get("ollama_update_attempt") or []
    attempt_by_node = {r.get("node"): r for r in attempt if isinstance(r, dict)}

    for node in ("bifrost", "hearth", "forge"):
        info = oll.get(node) or {}
        upd = attempt_by_node.get(node)
        if upd and upd.get("ok") and not upd.get("skipped"):
            recs.append(_rec("GREEN",
                f"Auto-updated {node.title()} Ollama: "
                f"{upd.get('old_version')} -> {upd.get('new_version')}",
                "ollama"))
        elif upd and not upd.get("ok"):
            recs.append(_rec("YELLOW",
                f"{node.title()} Ollama auto-update failed "
                f"({upd.get('old_version')} -> {upd.get('new_version')}) -- "
                f"check OLLAMA_UPDATE_LOG.md",
                "ollama"))
        elif info.get("behind"):
            recs.append(_rec("YELLOW",
                f"{node.title()} Ollama is behind "
                f"({info.get('installed')} -> {info.get('latest')}) -- "
                f"update_ollama unavailable; schedule update window",
                "ollama"))

    # pk_sync staleness
    pk = results.get("pk_sync") or {}
    age = pk.get("age_hours")
    if age is not None and age > 26:
        recs.append(_rec("YELLOW",
            f"pk_sync_manifest.txt is {age:.1f}h old — pk_sync may have stopped running",
            "pk_sync"))

    if not recs:
        recs.append(_rec("GREEN", "All services healthy. Accept rates stable. No action required.", "summary"))

    # Sort: RED -> YELLOW -> GREEN
    order = {"RED": 0, "YELLOW": 1, "GREEN": 2}
    recs.sort(key=lambda r: order.get(r["level"], 9))
    return recs


# -- Report writer -------------------------------------------------------------

def _status_emoji(level_or_status: str) -> str:
    return {
        "RED": "[RED]", "YELLOW": "[YEL]", "GREEN": "[OK]",
        "red": "[RED]", "yellow": "[YEL]", "green": "[OK]",
    }.get(level_or_status, "[--]")


def _render_health_table(health: dict) -> str:
    lines = ["| Component | Status | Notes |", "|---|---|---|"]
    order = [
        ("router", "Router /health"),
        ("bifrost_kb", "bifrost-kb :8091"),
        ("ollama_bifrost", "Ollama Bifrost"),
        ("ollama_hearth", "Ollama Hearth"),
        ("ollama_forge", "Ollama Forge"),
    ]
    for key, label in order:
        info = health.get(key) or {}
        status = info.get("status", "unknown")
        note = ""
        if "model_count" in info:
            note = f"{info['model_count']} models"
        elif "note" in info:
            note = str(info["note"])[:120]
        elif info.get("detail"):
            note = str(info["detail"])[:80]
        lines.append(f"| {label} | {_status_emoji(status)} {status} | {note} |")

    fsvc = health.get("forge_services") or {}
    if isinstance(fsvc, dict):
        for s in ("ollama", "lemonade", "flm"):
            st = fsvc.get(s, "unknown")
            emoji = "[OK]" if st == "active" else "[YEL]"
            lines.append(f"| Forge systemd: {s} | {emoji} {st} | via ssh |")
    return "\n".join(lines)


def _render_accept_rate_table(ar: dict) -> str:
    lines = ["| Profile | Current 7d | Prior 7d | Delta | Trend | Samples |",
             "|---|---|---|---|---|---|"]
    for profile in ("coding", "general", "research"):
        data = ar.get(profile) or {}
        if "error" in data:
            lines.append(f"| {profile} | n/a | n/a | n/a | {data['error']} | 0 |")
            continue
        cur = data.get("current_7d")
        prior = data.get("prior_7d")
        delta = data.get("delta")
        trend = data.get("trend", "n/a")
        cs = data.get("current_samples", 0)
        ps = data.get("prior_samples", 0)
        cur_s = f"{cur:.0f}%" if cur is not None else "n/a"
        prior_s = f"{prior:.0f}%" if prior is not None else "n/a"
        delta_s = f"{delta:+.0f}%" if delta is not None else "n/a"
        lines.append(f"| {profile} | {cur_s} | {prior_s} | {delta_s} | {trend} | {cs}/{ps} |")
    return "\n".join(lines)


def _render_recent_activity(on: dict, git: dict) -> str:
    parts: list[str] = []
    parts.append("**Overnight runs (48h):**")
    runs = on.get("runs_48h") if isinstance(on, dict) else []
    if not runs:
        parts.append("- (none)")
    else:
        for r in runs[-6:]:
            c = r.get("coding") or {}
            g = r.get("general") or {}
            rs = r.get("research") or {}
            parts.append(
                f"- {r.get('timestamp')} "
                f"coding={c.get('status', '?')}/{c.get('duration_s', 0):.0f}s "
                f"general={g.get('status', '?')}/{g.get('duration_s', 0):.0f}s "
                f"research={rs.get('status', '?')}/{rs.get('duration_s', 0):.0f}s"
            )
    parts.append("")
    parts.append("**Git commits (48h):**")
    for label in ("autoagent", "bifrost-router"):
        info = (git or {}).get(label) or {}
        commits = info.get("commits") or []
        parts.append(f"- {label}: {len(commits)} commits")
        for c in commits[:5]:
            parts.append(f"  - `{c}`")
    return "\n".join(parts)


def _render_recommendations(recs: list[dict]) -> str:
    lines: list[str] = []
    for i, r in enumerate(recs, 1):
        tag = {"RED": "[BLOCKING]", "YELLOW": "[ADVISORY]", "GREEN": "[INFO]"}.get(r["level"], "")
        lines.append(f"{i}. {_status_emoji(r['level'])} {tag} {r['text']}")
    return "\n".join(lines)


def write_report(results: dict, recommendations: list[dict], errors: dict) -> Path:
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y%m%d")
    ts_str = now.strftime("%Y-%m-%d %H:%M:%S UTC")

    PK_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    path = PK_UPLOAD_DIR / f"ARCHITECT_REPORT_{date_str}.md"

    next_run = "tomorrow 06:30 local"
    counts = {"RED": 0, "YELLOW": 0, "GREEN": 0}
    for r in recommendations:
        counts[r["level"]] = counts.get(r["level"], 0) + 1

    sections = [
        f"# ARCHITECT-0 Report -- {date_str}",
        f"Generated: {ts_str} | Next run: {next_run}",
        "",
        f"**Summary:** {counts['RED']} blocking, {counts['YELLOW']} advisory, {counts['GREEN']} info",
        "",
        "## Fleet Health",
        _render_health_table(results.get("health") or {}),
        "",
        "## Accept Rate Trend (7-day rolling vs prior 7-day)",
        _render_accept_rate_table(results.get("accept_rates") or {}),
        "",
        "## Recent Activity (48h)",
        _render_recent_activity(results.get("overnight") or {}, results.get("git_delta") or {}),
        "",
        "## Scheduled Tasks",
    ]
    tasks = results.get("tasks") or {}
    if isinstance(tasks, dict) and "error" not in tasks:
        for t in TASK_NAMES:
            sections.append(f"- {t}: {tasks.get(t, 'unknown')}")
    else:
        sections.append(f"- error: {tasks.get('error') if isinstance(tasks, dict) else tasks}")

    sections += ["", "## Model Inventory"]
    models = results.get("models") or {}
    missing = models.get("missing") or []
    if not missing:
        sections.append("- All profile-referenced models present on their target nodes.")
    else:
        for m in missing:
            sections.append(f"- {_status_emoji('YELLOW')} {m.get('profile')}/{m.get('role')}: "
                            f"`{m.get('model')}` on {m.get('node')} ({m.get('result')})")

    sections += ["", "## Ollama Version Drift"]
    oll = results.get("ollama") or {}
    age = oll.get("cache_age_hours")
    age_s = f"{age:.1f}h" if isinstance(age, (int, float)) else "fresh"
    sections.append(f"- GitHub latest: `{oll.get('latest', 'unknown')}` (cache age: {age_s})")
    for node in ("bifrost", "hearth", "forge"):
        info = oll.get(node) or {}
        sections.append(f"- {node}: installed=`{info.get('installed')}` "
                        f"behind={info.get('behind')}")

    sections += ["", "## pk_sync Freshness"]
    pk = results.get("pk_sync") or {}
    if pk.get("found"):
        sections.append(f"- last manifest mtime: {pk.get('mtime')}"
                        f" (age: {pk.get('age_hours', 0):.1f}h)")
    else:
        sections.append(f"- no manifest found: {pk.get('error', 'missing')}")

    sections += ["", "## Recommendations",
                 _render_recommendations(recommendations)]

    if errors:
        sections += ["", "## Collection Errors"]
        for k, v in errors.items():
            sections.append(f"- {k}: {v}")

    sections += [
        "",
        "---",
        "*ARCHITECT-0 is read-only except for scoped Ollama auto-updates (see OLLAMA_UPDATE_LOG.md).*",
        "",
    ]

    path.write_text("\n".join(sections), encoding="utf-8")
    return path


# -- Main ----------------------------------------------------------------------

def main() -> int:
    print(f"ARCHITECT-0 starting {datetime.now(timezone.utc).isoformat()}")

    collectors = [
        ("overnight",    collect_overnight),
        ("accept_rates", collect_accept_rates),
        ("git_delta",    collect_git_delta),
        ("health",       collect_service_health),
        ("tasks",        collect_task_states),
        ("models",       collect_model_inventory),
        ("ollama",       collect_ollama_versions),
        ("pk_sync",      collect_pk_sync_staleness),
    ]

    results: dict = {}
    errors: dict = {}
    for name, fn in collectors:
        print(f"  [..] {name}")
        data, err = _safe(fn)
        if err:
            errors[name] = err
            results[name] = {"error": err}
            print(f"  [!!] {name}: {err}")
        else:
            results[name] = data
            print(f"  [OK] {name}")

    # ARCHITECT-0's one maintenance action: auto-update Ollama when behind.
    # Model files and config are untouched; full log at OLLAMA_UPDATE_LOG.md.
    oll = results.get("ollama") or {}
    behind_nodes = [n for n in ("forge", "bifrost", "hearth")
                    if isinstance(oll.get(n), dict) and oll[n].get("behind") is True]
    if behind_nodes and update_ollama is not None:
        print(f"  [..] ollama_update_attempt nodes={behind_nodes}")
        attempt, att_err = _safe(update_ollama.main_programmatic, behind_nodes, False)
        if att_err:
            errors["ollama_update_attempt"] = att_err
            print(f"  [!!] ollama_update_attempt: {att_err}")
        else:
            results["ollama_update_attempt"] = attempt
            # Re-collect post-update versions so the report reflects new state
            post, post_err = _safe(collect_ollama_versions)
            if not post_err:
                results["ollama"] = post
            print(f"  [OK] ollama_update_attempt: {len(attempt)} node(s) processed")
    elif behind_nodes and update_ollama is None:
        errors["ollama_update_attempt"] = "update_ollama module not importable"

    results["generated_at"] = datetime.now(timezone.utc).isoformat()

    recs = generate_recommendations(results)
    report_path = write_report(results, recs, errors)

    red = sum(1 for r in recs if r["level"] == "RED")
    yellow = sum(1 for r in recs if r["level"] == "YELLOW")
    print(f"Report written: {report_path}")
    print(f"Recommendations: {red} blocking, {yellow} advisory")
    return 0


if __name__ == "__main__":
    sys.exit(main())
