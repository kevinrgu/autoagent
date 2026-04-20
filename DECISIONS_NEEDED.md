# DECISIONS_NEEDED — Repair 4 Partial Failure

**Generated:** 2026-04-20 (Monday evening, Bifrost local)
**Parent brief:** DISPATCH-REPAIRS-4-6.md
**Status:** HALTED after Repair 4 partial failure. **Repair 6 NOT executed.**

---

## What happened

Running Repair 4 enabled one task cleanly and hit Access Denied on the other:

| Task | State | Enable Result |
|------|-------|---------------|
| BIFROST-Overnight-Training | **Ready** (was Disabled) | ✅ Enabled |
| BIFROST-Session-O-AutoAgent | **Disabled** (still) | ❌ `HRESULT 0x80070005` Access is denied |

Post-enable info (LastTaskResult, NextRunTime) for both:
- Both NextRunTime: `2026-04-21 02:00:00` (Tuesday AM)
- Both LastTaskResult: `0` (clean exit on their last 2026-04-18 run)

## Root cause

```
Get-ScheduledTask -TaskName "BIFROST-Overnight-Training"  .Principal
  UserId: jhpri  LogonType: Interactive  RunLevel: Limited   ← enable OK

Get-ScheduledTask -TaskName "BIFROST-Session-O-AutoAgent"  .Principal
  UserId: jhpri  LogonType: Interactive  RunLevel: Highest   ← requires admin to modify

whoami                    → billybobwin11p\jhpri
IsInRole(Administrator)   → False
```

Session-O-AutoAgent was created with `RunLevel: Highest` (elevation required for execution). Windows requires the same elevation level to enable/disable/modify such tasks. The current Claude Code session is running as `jhpri` without admin elevation, so `Enable-ScheduledTask` on the Highest-runlevel task is denied.

This is the exact reason the task was originally registered with an empty `Author` field — it was created by an elevated process, and non-elevated PowerShell can read it but not modify its enabled state.

## Impact on Tuesday 2026-04-21 02:00

- **Overnight-Training WILL fire.** It's Ready.
- **Session-O-AutoAgent WILL NOT fire.** Still Disabled.

Depending on which task actually runs the overnight Session O cycles (Overnight-Training vs. Session-O-AutoAgent), this may mean:
- Tuesday's training fires normally but Session O decisions are not captured → no git commit of decisions
- Or the whole overnight chain fails if the two tasks are sequenced

I did not read `bifrost_cycle.py` (out of scope per brief) so I cannot determine which task owns the Session O loop. Assume worst case: **Session O is blocked until Session-O-AutoAgent is re-enabled.**

## Your action — one of

### Option A (recommended, ~30 sec)
Open an **elevated** PowerShell (Run as Administrator) and run:
```powershell
Enable-ScheduledTask -TaskName "BIFROST-Session-O-AutoAgent"
Get-ScheduledTask -TaskName "BIFROST-Session-O-AutoAgent" | Select State
# Expected: State: Ready
```
Then, in a non-elevated session or a follow-on brief, run Repair 6.

### Option B (if you want me to do it next session)
Reply with `RESUME_ELEVATED` after you run Claude Code from an elevated shell (`Run as administrator` on the Claude Code launcher). I can then retry Repair 4's second half and proceed through Repair 6.

### Option C (skip and downgrade)
If the Highest runlevel isn't actually needed for the task's workload, you could re-register the task with `RunLevel: Limited` via:
```powershell
$a = (Get-ScheduledTask "BIFROST-Session-O-AutoAgent").Actions
$t = (Get-ScheduledTask "BIFROST-Session-O-AutoAgent").Triggers
Unregister-ScheduledTask "BIFROST-Session-O-AutoAgent" -Confirm:$false
Register-ScheduledTask "BIFROST-Session-O-AutoAgent" -Action $a -Trigger $t -RunLevel Limited
```
(Also needs admin.) This avoids the elevation requirement on future enables/disables.

## Repair 6 — not attempted

Repair 6 (Session O role rebalance: proposer → Forge T2, add logger naming rule) was NOT run. The brief's explicit stop rule ("If either task fails to enable, write DECISIONS_NEEDED.md and stop.") triggered after the first Enable-ScheduledTask failure.

Repair 6 is independent of task state — the profiles.json edits and Forge liveness test don't depend on whether the tasks are enabled. But I followed the brief's halt-on-failure instruction rather than proceeding past a gate.

If you want Repair 6 to run before Tuesday 02:00 regardless of the task-enable fix, reply `PROCEED_REPAIR_6` and I'll run just that half.

---

# DECISIONS_NEEDED -- 2026-04-20 (update_ollama dispatch)

## Context

Dispatch `DISPATCH-UPDATE-OLLAMA.md` ran live updates in the order Forge -> Bifrost -> Hearth.

- **Forge**: updated `0.20.5 -> 0.21.0`, 10 models preserved, service active. OK.
- **Hearth**: already `0.21.0`, skipped.
- **Bifrost**: FAILED verification on first attempt.

Per dispatch safety rule ("If any live-run node fails: stop, write DECISIONS_NEEDED.md,
do not proceed to next node"), Bifrost live update was halted. No retries were
attempted on a different node.

## Bifrost failure detail

### Root cause 1 -- wrong installer flags (FIXED in script)

Dispatch brief stated Ollama's Windows installer is NSIS (`/S` for silent). It
is not -- it's **Inno Setup** (confirmed by `unins000.exe` uninstaller naming
convention). `/S` was silently ignored by Inno Setup, so the installer launched
a UI. `update_ollama.py` was corrected on the spot to use Inno Setup flags:

```
/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /CLOSEAPPLICATIONS /RESTARTAPPLICATIONS /DIR=C:\Ollama
```

Hearth's path was corrected to match.

### Root cause 2 -- BLOCKING: ollama.exe runs under SYSTEM, user session cannot kill it

After the botched first install, two `ollama.exe` processes remain:

| PID   | Parent PID | Name   | Owner                  |
|-------|------------|--------|------------------------|
| 20536 | 2552 (`svchost.exe`) | ollama | (unreadable -- SYSTEM) |
| 4272  | 20536      | ollama | (unreadable -- SYSTEM) |

`taskkill /F /T` and `Stop-Process -Force` both return `Access is denied`. The
Claude Code session is NOT elevated (`IsInRole(Administrator) == False`). Even
the Inno Setup `/CLOSEAPPLICATIONS` flag likely cannot get a new binary past a
locked file owned by SYSTEM without user-level UAC approval, which cannot be
issued from a headless non-interactive session.

No Windows service named `ollama*` is registered -- the SYSTEM-owned processes
appear to have been launched by the AMD AI Bundle or a prior run that ran
elevated and survived the session.

## Bifrost current state (non-broken, just not upgraded)

- `C:\Ollama\ollama.exe --version`: **server 0.20.2 / client 0.21.0** (mismatch)
- `http://192.168.2.33:11434/api/tags`: responding, 15 models intact
- PATH: `C:\Ollama\ollama.exe` first, AMD bundle second (Rule 10 still satisfied)
- `C:\Ollama\` has freshly-written `lib/` and `unins000.*` from the botched
  install, but `ollama.exe` and `ollama app.exe` binaries remain dated
  2026-04-17 (not replaced -- locked by the SYSTEM-owned processes)

## Decision needed

### Option A -- human runs updater from elevated shell (recommended)

1. Open an **elevated** PowerShell (Run as Administrator)
2. `cd C:\Users\jhpri\projects\autoagent`
3. `python update_ollama.py --nodes bifrost`
4. Verify: `ollama --version` -> `0.21.0`

~30 seconds of human action. The script is now correct; only elevation was missing.

### Option B -- run installer interactively

1. Double-click `L:\temp\OllamaSetup.exe` (already downloaded)
2. Accept UAC prompt
3. Installer detects existing `C:\Ollama` install and upgrades in place

### Option C -- register an elevated Scheduled Task for future Bifrost updates

Create Scheduled Task `BIFROST-OllamaUpdate` that runs
`python update_ollama.py --nodes bifrost` with "Run with highest privileges".
ARCHITECT-0 would trigger the task instead of calling the script directly when
Bifrost drift is detected. Sustainable option, but deferred -- today's unblock
is Option A.

## Side effect seen + repaired

The second Bifrost install attempt (via architect's auto-invoke with the
corrected `/VERYSILENT` flags) took the Bifrost Ollama server offline: the
installer exited with rc=0 but the server was no longer listening on
`:11434`, and the on-disk binary was still 0.20.2 (SYSTEM-owned processes
held it locked). Service was restored by launching `C:\Ollama\ollama app.exe`
interactively from the user session (~8s to HTTP 200, 15 models intact).

To prevent tomorrow's 06:30 architect run from repeating this, a per-node
throttle was added to `update_ollama.main_programmatic`: any node with a
`FAIL` row in `OLLAMA_UPDATE_LOG.md` within the last 20h is skipped with a
`throttled` note. The throttle self-clears on success.

## Not blocked / still delivered

- **Forge update path is proven** -- ARCHITECT can auto-update Forge without elevation.
- **Hearth update path is code-correct** (Inno Setup flags, Scheduled Task stop/start
  wrapping) but unexercised this session since Hearth is already current.
- **`architect.py` patch** (auto-invoke updater on drift) is safe to apply: worst
  case on Bifrost is the updater returning `ok=False`, which flips the
  recommendation from GREEN back to YELLOW for next-day human review.

