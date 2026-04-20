"""
update_ollama.py — BIFROST Ollama fleet updater
Called by architect.py when version drift is detected.
Covers all three nodes: Bifrost (local Windows), Hearth (SSH Windows), Forge (SSH Ubuntu).

Safety rules:
- Rule 8: stop loaded models on Forge before update.
- Rule 10: Bifrost — update C:\\Ollama\\ ONLY, never the AMD bundle at
  C:\\Users\\jhpri\\AppData\\Local\\AMD\\AI_Bundle\\Ollama\\.
- Verify model inventory before and after each node update.
- Log all actions to L:\\temp\\pk-upload\\OLLAMA_UPDATE_LOG.md.
- Dry-run mode: pass --dry-run to preview without executing.
"""

import argparse
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

# -- Constants -----------------------------------------------------------------
LOG_PATH        = Path(r"L:\temp\pk-upload\OLLAMA_UPDATE_LOG.md")
INSTALLER_PATH  = Path(r"L:\temp\OllamaSetup.exe")
BIFROST_OLLAMA  = r"C:\Ollama\ollama.exe"   # Rule 10 — standard install, NOT AMD bundle
BIFROST_AMD     = r"C:\Users\jhpri\AppData\Local\AMD\AI_Bundle\Ollama\ollama.exe"
BIFROST_INSTALL_DIR = r"C:\Ollama"
HEARTH_SSH      = "jhpri@192.168.2.4"
FORGE_SSH       = "jhpritch@192.168.2.50"
HEARTH_TEMP_INSTALLER = r"C:\Users\jhpri\AppData\Local\Temp\OllamaSetup.exe"
VERSION_CACHE   = Path(r"L:\temp\ollama_version_cache.json")
INSTALLER_URL   = "https://ollama.com/download/OllamaSetup.exe"
LINUX_INSTALL   = "https://ollama.com/install.sh"

HEARTH_OLLAMA_TASK = "Ollama-dGPU-5700XT"


# -- Small helpers -------------------------------------------------------------

def _run(cmd: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout,
        encoding="utf-8", errors="replace",
    )


def _parse_ollama_version(output: str) -> str:
    """Parse `ollama --version` output. Prefer the 'client version' line when
    present -- that's the post-install state where the server is still warming
    up and the old server version would otherwise be reported. Falls back to
    the 'ollama version is X' line.
    """
    for line in (output or "").splitlines():
        s = line.strip().lower()
        if "client version" in s:
            parts = s.split("is")
            if len(parts) >= 2:
                return parts[-1].strip()
    for line in (output or "").splitlines():
        s = line.strip().lower()
        if s.startswith("ollama version"):
            parts = s.split("is")
            if len(parts) >= 2:
                return parts[-1].strip()
    return "unknown"


# Backwards-compat alias: callers used _parse_version before.
_parse_version = _parse_ollama_version


def _get_bifrost_version() -> str:
    """Bifrost version check with API fallback. If `ollama --version` returns
    nothing useful (tray app running without stdout, or binary locked), query
    the server API at http://localhost:11434/api/version.
    """
    try:
        r = subprocess.run(
            [BIFROST_OLLAMA, "--version"],
            capture_output=True, text=True, timeout=10,
            encoding="utf-8", errors="replace",
        )
        v = _parse_ollama_version(r.stdout + r.stderr)
        if v and v != "unknown":
            return v
    except Exception:
        pass
    try:
        resp = requests.get("http://localhost:11434/api/version", timeout=5)
        if resp.status_code == 200:
            return resp.json().get("version", "unknown")
    except Exception:
        pass
    return "unknown"


def _refresh_version_cache(node: str, new_version: str) -> None:
    """Update the cached installed version for a node after a successful update.
    Prevents the next ARCHITECT run from reading stale data and retrying."""
    try:
        cache: dict = {}
        if VERSION_CACHE.exists():
            try:
                cache = json.loads(VERSION_CACHE.read_text(encoding="utf-8"))
            except Exception:
                cache = {}
        cache[f"installed_{node}"] = new_version
        cache["ts_installed"] = datetime.now(timezone.utc).isoformat()
        VERSION_CACHE.parent.mkdir(parents=True, exist_ok=True)
        VERSION_CACHE.write_text(json.dumps(cache, indent=2), encoding="utf-8")
    except Exception as e:
        _log_line(f"  cache refresh failed (non-fatal): {e}")


def _log_line(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


# -- Public API ----------------------------------------------------------------

def get_latest_version() -> str:
    """Read from cache; fall back to GitHub releases API."""
    now = datetime.now(timezone.utc)
    if VERSION_CACHE.exists():
        try:
            cache = json.loads(VERSION_CACHE.read_text(encoding="utf-8"))
            ts = datetime.fromisoformat(cache.get("ts", ""))
            if (now - ts).total_seconds() < 24 * 3600 and cache.get("latest"):
                return cache["latest"]
        except Exception:
            pass

    try:
        r = requests.get(
            "https://api.github.com/repos/ollama/ollama/releases/latest",
            timeout=10,
            headers={"Accept": "application/vnd.github+json"},
        )
        r.raise_for_status()
        tag = (r.json().get("tag_name") or "").lstrip("v")
        if tag:
            VERSION_CACHE.parent.mkdir(parents=True, exist_ok=True)
            existing: dict = {}
            if VERSION_CACHE.exists():
                try:
                    existing = json.loads(VERSION_CACHE.read_text(encoding="utf-8"))
                except Exception:
                    existing = {}
            existing["ts"] = now.isoformat()
            existing["latest"] = tag
            VERSION_CACHE.write_text(
                json.dumps(existing, indent=2),
                encoding="utf-8",
            )
            return tag
    except Exception as e:
        _log_line(f"get_latest_version lookup failed: {e}")

    return "unknown"


def get_installed_version(node: str) -> str:
    if node == "bifrost":
        return _get_bifrost_version()
    try:
        if node == "hearth":
            r = _run(["ssh", HEARTH_SSH, "ollama --version"], timeout=15)
        elif node == "forge":
            r = _run(["ssh", FORGE_SSH, "ollama --version"], timeout=15)
        else:
            return "unknown"
        return _parse_ollama_version(r.stdout + r.stderr)
    except Exception as e:
        _log_line(f"get_installed_version({node}) failed: {e}")
        return "unknown"


def get_model_count(node: str) -> int:
    try:
        if node == "bifrost":
            r = _run([BIFROST_OLLAMA, "list"], timeout=30)
        elif node == "hearth":
            r = _run(["ssh", HEARTH_SSH, "ollama list"], timeout=30)
        elif node == "forge":
            r = _run(["ssh", FORGE_SSH, "ollama list"], timeout=30)
        else:
            return -1
        lines = [ln for ln in r.stdout.splitlines() if ln.strip()]
        # Drop header row (starts with "NAME")
        if lines and lines[0].upper().startswith("NAME"):
            lines = lines[1:]
        return len(lines)
    except Exception as e:
        _log_line(f"get_model_count({node}) failed: {e}")
        return -1


# -- Forge-specific pre-update -------------------------------------------------

def stop_forge_models() -> list[str]:
    """Rule 8: unload any running Forge models before update. Returns model names."""
    stopped: list[str] = []
    try:
        r = _run(["ssh", FORGE_SSH, "ollama ps"], timeout=30)
        for line in r.stdout.splitlines()[1:]:
            parts = line.split()
            if parts and not line.upper().startswith("NAME"):
                name = parts[0]
                _log_line(f"  forge: stopping model '{name}'")
                _run(["ssh", FORGE_SSH, f"ollama stop {name}"], timeout=30)
                stopped.append(name)
    except Exception as e:
        _log_line(f"stop_forge_models (ollama stop step) failed: {e}")

    # Clean stop of daemon for safe binary replacement
    try:
        _run(["ssh", FORGE_SSH, "sudo systemctl stop ollama"], timeout=30)
    except Exception as e:
        _log_line(f"stop_forge_models (systemctl stop) failed: {e}")
    return stopped


# -- Bifrost -------------------------------------------------------------------

def _download_installer() -> bool:
    try:
        _log_line(f"  downloading installer -> {INSTALLER_PATH}")
        INSTALLER_PATH.parent.mkdir(parents=True, exist_ok=True)
        with requests.get(INSTALLER_URL, stream=True, timeout=120) as r:
            r.raise_for_status()
            with open(INSTALLER_PATH, "wb") as f:
                for chunk in r.iter_content(chunk_size=65536):
                    if chunk:
                        f.write(chunk)
        return INSTALLER_PATH.exists() and INSTALLER_PATH.stat().st_size > 1_000_000
    except Exception as e:
        _log_line(f"  installer download failed: {e}")
        return False


def update_bifrost(latest: str, dry_run: bool) -> dict:
    _log_line(f"[bifrost] target version {latest}")
    old = get_installed_version("bifrost")
    before_count = get_model_count("bifrost")

    result: dict = {
        "node": "bifrost",
        "old_version": old,
        "new_version": old,
        "model_count_before": before_count,
        "model_count_after": before_count,
        "ok": False,
        "skipped": False,
        "notes": [],
    }

    # Rule 10 — verify C:\Ollama\ollama.exe exists and is first in PATH
    if not Path(BIFROST_OLLAMA).exists():
        result["notes"].append(f"ABORT: {BIFROST_OLLAMA} not found — cannot verify standard install")
        return result

    where = _run(["where.exe", "ollama"], timeout=10)
    first = (where.stdout.splitlines() or [""])[0].strip().lower()
    if Path(BIFROST_OLLAMA).resolve().as_posix().lower() not in first.replace("\\", "/"):
        result["notes"].append(
            f"ABORT (Rule 10): PATH resolves to '{first}' before "
            f"{BIFROST_OLLAMA}; refusing to run installer — would update wrong binary"
        )
        return result
    if "amd" in first:
        result["notes"].append(f"ABORT (Rule 10): PATH resolves to AMD bundle first: {first}")
        return result

    if dry_run:
        result["notes"].append(
            f"DRY RUN: would download {INSTALLER_URL}, trigger "
            f"BIFROST-Ollama-Restart after install /VERYSILENT /DIR={BIFROST_INSTALL_DIR}"
        )
        result["ok"] = True
        result["skipped"] = True
        return result

    if not _download_installer():
        result["notes"].append("ABORT: installer download failed")
        return result

    _log_line(f"  running installer: {INSTALLER_PATH} /VERYSILENT /DIR={BIFROST_INSTALL_DIR}")
    # Ollama installer is Inno Setup (unins000.exe pattern confirms it).
    # /VERYSILENT + /SUPPRESSMSGBOXES for no-UI install. /CLOSEAPPLICATIONS +
    # /RESTARTAPPLICATIONS request Restart Manager to close/relaunch the running
    # ollama processes. /DIR=... sets the install directory.
    try:
        inst = subprocess.run(
            [str(INSTALLER_PATH),
             "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
             "/CLOSEAPPLICATIONS", "/RESTARTAPPLICATIONS",
             f'/DIR={BIFROST_INSTALL_DIR}'],
            timeout=300, capture_output=True, text=True,
        )
        result["notes"].append(f"installer rc={inst.returncode}")
    except Exception as e:
        result["notes"].append(f"installer failed: {e}")
        return result

    # After install, trigger the BIFROST-Ollama-Restart Scheduled Task. That task
    # runs as SYSTEM / RunLevel:Highest, which is required to stop the SYSTEM-owned
    # ollama.exe processes that a user-session `taskkill` cannot touch. Without
    # this restart, the newly-installed binary is not loaded.
    _log_line("  triggering BIFROST-Ollama-Restart Scheduled Task")
    tr = subprocess.run(
        ["schtasks", "/Run", "/TN", "BIFROST-Ollama-Restart"],
        capture_output=True, text=True,
    )
    if tr.returncode != 0:
        result["notes"].append(
            "restart task failed (is BIFROST-Ollama-Restart registered as "
            f"SYSTEM/Highest?): {tr.stderr.strip() or tr.stdout.strip()}"
        )
        return result
    time.sleep(8)

    new = get_installed_version("bifrost")
    after_count = get_model_count("bifrost")
    result["new_version"] = new
    result["model_count_after"] = after_count
    result["ok"] = (new == latest) and (after_count == before_count or before_count == -1)
    if result["ok"]:
        _refresh_version_cache("bifrost", new)
    else:
        result["notes"].append(
            f"verify failed: version {old}->{new} (want {latest}), "
            f"models {before_count}->{after_count}"
        )
    return result


# -- Hearth --------------------------------------------------------------------

def update_hearth(latest: str, dry_run: bool) -> dict:
    _log_line(f"[hearth] target version {latest}")
    old = get_installed_version("hearth")
    before_count = get_model_count("hearth")

    result: dict = {
        "node": "hearth",
        "old_version": old,
        "new_version": old,
        "model_count_before": before_count,
        "model_count_after": before_count,
        "ok": False,
        "skipped": False,
        "notes": [],
    }

    if dry_run:
        result["notes"].append(
            f"DRY RUN: would stop task {HEARTH_OLLAMA_TASK}, download installer to "
            f"{HEARTH_TEMP_INSTALLER}, run /S, restart task"
        )
        result["ok"] = True
        result["skipped"] = True
        return result

    _log_line(f"  stopping Scheduled Task {HEARTH_OLLAMA_TASK}")
    _run(["ssh", HEARTH_SSH,
          f"powershell -NoProfile Stop-ScheduledTask -TaskName {HEARTH_OLLAMA_TASK} -ErrorAction SilentlyContinue"],
         timeout=30)

    _log_line("  stopping ollama.exe processes on Hearth")
    _run(["ssh", HEARTH_SSH,
          "powershell -NoProfile \"Stop-Process -Name ollama -Force -ErrorAction SilentlyContinue; "
          "Stop-Process -Name 'ollama app' -Force -ErrorAction SilentlyContinue\""],
         timeout=30)
    time.sleep(2)

    _log_line(f"  downloading installer to Hearth: {HEARTH_TEMP_INSTALLER}")
    dl = _run(["ssh", HEARTH_SSH,
               f"powershell -NoProfile \"Invoke-WebRequest -Uri {INSTALLER_URL} "
               f"-OutFile {HEARTH_TEMP_INSTALLER} -UseBasicParsing\""],
              timeout=300)
    if dl.returncode != 0:
        result["notes"].append(f"download failed: rc={dl.returncode} stderr={dl.stderr[:200]}")
        return result

    _log_line("  running installer on Hearth (Inno Setup /VERYSILENT)")
    inst = _run(
        ["ssh", HEARTH_SSH,
         f"{HEARTH_TEMP_INSTALLER} /VERYSILENT /SUPPRESSMSGBOXES /NORESTART "
         "/CLOSEAPPLICATIONS /RESTARTAPPLICATIONS"],
        timeout=300,
    )
    result["notes"].append(f"installer rc={inst.returncode}")
    time.sleep(10)

    _log_line(f"  restarting Scheduled Task {HEARTH_OLLAMA_TASK}")
    _run(["ssh", HEARTH_SSH,
          f"powershell -NoProfile Start-ScheduledTask -TaskName {HEARTH_OLLAMA_TASK}"],
         timeout=30)
    time.sleep(6)

    new = get_installed_version("hearth")
    after_count = get_model_count("hearth")
    result["new_version"] = new
    result["model_count_after"] = after_count
    result["ok"] = (new == latest) and (after_count == before_count or before_count == -1)
    if result["ok"]:
        _refresh_version_cache("hearth", new)
    else:
        result["notes"].append(
            f"verify failed: version {old}->{new} (want {latest}), "
            f"models {before_count}->{after_count}"
        )
    return result


# -- Forge ---------------------------------------------------------------------

def update_forge(latest: str, dry_run: bool) -> dict:
    _log_line(f"[forge] target version {latest}")
    old = get_installed_version("forge")
    before_count = get_model_count("forge")

    result: dict = {
        "node": "forge",
        "old_version": old,
        "new_version": old,
        "model_count_before": before_count,
        "model_count_after": before_count,
        "ok": False,
        "skipped": False,
        "notes": [],
    }

    if dry_run:
        result["notes"].append(
            "DRY RUN: would stop_forge_models(), curl install.sh | sh, restart ollama service"
        )
        result["ok"] = True
        result["skipped"] = True
        return result

    stopped = stop_forge_models()
    if stopped:
        result["notes"].append(f"stopped models: {', '.join(stopped)}")

    _log_line("  running install.sh on Forge")
    inst = _run(["ssh", FORGE_SSH, f"curl -fsSL {LINUX_INSTALL} | sh"], timeout=600)
    result["notes"].append(f"install.sh rc={inst.returncode}")
    if inst.returncode != 0:
        result["notes"].append(f"install.sh stderr tail: {inst.stderr[-300:]}")

    # install.sh usually restarts the service; ensure it
    _log_line("  ensuring ollama service is active on Forge")
    _run(["ssh", FORGE_SSH, "sudo systemctl start ollama"], timeout=30)
    time.sleep(5)

    new = get_installed_version("forge")
    after_count = get_model_count("forge")
    result["new_version"] = new
    result["model_count_after"] = after_count
    result["ok"] = (new == latest) and (after_count == before_count or before_count == -1)
    if result["ok"]:
        _refresh_version_cache("forge", new)
    else:
        result["notes"].append(
            f"verify failed: version {old}->{new} (want {latest}), "
            f"models {before_count}->{after_count}"
        )
    return result


# -- Log writer ----------------------------------------------------------------

def write_log(results: list[dict], dry_run: bool) -> Path:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    tag = " (DRY RUN)" if dry_run else ""

    lines = [f"## Update run -- {ts}{tag}", "",
             "| Node | Before | After | Models (before/after) | Skipped | OK |",
             "|------|--------|-------|-----------------------|---------|----|"]
    for r in results:
        node = r.get("node", "?")
        before = r.get("old_version", "?")
        after = r.get("new_version", "?")
        mb = r.get("model_count_before", "?")
        ma = r.get("model_count_after", "?")
        sk = "yes" if r.get("skipped") else "no"
        ok = "OK" if r.get("ok") else "FAIL"
        lines.append(f"| {node} | {before} | {after} | {mb}/{ma} | {sk} | {ok} |")

    # Notes block
    for r in results:
        notes = r.get("notes") or []
        if notes:
            lines.append("")
            lines.append(f"**{r.get('node')}:**")
            for n in notes:
                lines.append(f"- {n}")

    lines.append("")
    content = "\n".join(lines) + "\n"

    mode = "a" if LOG_PATH.exists() else "w"
    with open(LOG_PATH, mode, encoding="utf-8") as f:
        if mode == "w":
            f.write("# Ollama Update Log\n\n")
        f.write(content)
    return LOG_PATH


# -- Programmatic entry for architect.py --------------------------------------

def _recently_failed(node: str, hours: int = 20) -> bool:
    """Read OLLAMA_UPDATE_LOG.md: has this node had a FAIL entry in the last N hours?"""
    if not LOG_PATH.exists():
        return False
    try:
        text = LOG_PATH.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return False
    # Each entry starts with "## Update run -- YYYY-MM-DD HH:MM:SS UTC"
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    sections = re.split(r"^## Update run -- ", text, flags=re.MULTILINE)
    for sec in sections[1:]:
        first, _, rest = sec.partition("\n")
        m = re.match(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) UTC", first)
        if not m:
            continue
        try:
            ts = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        except Exception:
            continue
        if ts < cutoff:
            continue
        # Look for a row like "| node | ... | FAIL |"
        row_re = re.compile(rf"\|\s*{re.escape(node)}\s*\|[^\n]*\|\s*FAIL\s*\|", re.IGNORECASE)
        if row_re.search(rest):
            return True
    return False


def main_programmatic(nodes: list[str] | None = None, dry_run: bool = False) -> list[dict]:
    """Callable from architect.py. Runs updates for the given nodes only.

    Skips any node that had a FAIL in the last 20h to avoid daily re-attempts of a
    known-broken path (e.g. elevation-blocked Bifrost install). Manually running
    this script from an elevated shell clears the throttle on success.
    """
    if not nodes:
        return []
    latest = get_latest_version()
    if latest == "unknown":
        _log_line("main_programmatic: cannot resolve latest version; aborting")
        return [{"node": n, "ok": False, "notes": ["latest version lookup failed"],
                 "old_version": "unknown", "new_version": "unknown",
                 "model_count_before": -1, "model_count_after": -1,
                 "skipped": False} for n in nodes]
    results: list[dict] = []
    for node in nodes:
        if not dry_run and _recently_failed(node):
            current = get_installed_version(node)
            results.append({"node": node, "old_version": current, "new_version": current,
                            "model_count_before": -1, "model_count_after": -1,
                            "ok": False, "skipped": True,
                            "notes": ["throttled: prior FAIL <20h ago -- see OLLAMA_UPDATE_LOG.md"]})
            continue
        current = get_installed_version(node)
        if current == latest:
            results.append({"node": node, "old_version": current, "new_version": current,
                            "model_count_before": -1, "model_count_after": -1,
                            "ok": True, "skipped": True, "notes": ["already current"]})
            continue
        if node == "bifrost":
            results.append(update_bifrost(latest, dry_run))
        elif node == "hearth":
            results.append(update_hearth(latest, dry_run))
        elif node == "forge":
            results.append(update_forge(latest, dry_run))
    write_log(results, dry_run)
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="BIFROST Ollama fleet updater")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview without executing installs")
    parser.add_argument("--nodes", nargs="+",
                        choices=["bifrost", "hearth", "forge"],
                        default=["bifrost", "hearth", "forge"],
                        help="Which nodes to update")
    args = parser.parse_args()

    latest = get_latest_version()
    _log_line(f"latest version resolved: {latest}")
    if latest == "unknown":
        _log_line("ABORT: could not resolve latest version")
        return 2

    results: list[dict] = []
    for node in args.nodes:
        current = get_installed_version(node)
        if current == latest:
            _log_line(f"{node}: {current} -- already current, skipping")
            results.append({"node": node, "old_version": current, "new_version": current,
                            "model_count_before": -1, "model_count_after": -1,
                            "ok": True, "skipped": True, "notes": ["already current"]})
            continue
        tag = " (dry run)" if args.dry_run else ""
        _log_line(f"{node}: {current} -> {latest}{tag}")
        if node == "bifrost":
            results.append(update_bifrost(latest, args.dry_run))
        elif node == "hearth":
            results.append(update_hearth(latest, args.dry_run))
        elif node == "forge":
            results.append(update_forge(latest, args.dry_run))

    log_path = write_log(results, args.dry_run)
    _log_line(f"log written: {log_path}")

    failed = [r for r in results if not r.get("ok", True)]
    for r in results:
        _log_line(f"  {r['node']}: {r.get('old_version')} -> {r.get('new_version')} "
                  f"ok={r.get('ok')} skipped={r.get('skipped')}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
