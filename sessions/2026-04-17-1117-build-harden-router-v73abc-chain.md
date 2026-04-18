# Session: Build-Harden — Router v73 regression chain + Portal AUTOPILOT fix
**Date:** 2026-04-17 (Friday demo day → build-harden)
**Duration:** ~10 hours
**Outcome:** Router v73c live, AUTOPILOT end-to-end working, Portal fix queued, Session O-SAFETY designed

## Summary
Planned demo-day recording session became unplanned build-harden after Portal
AUTOPILOT failed to render. Root cause was a chain of 4 regressions from
overnight Session O commit 24d338e that all passed py_compile but crashed at
runtime, compounded by a Router scoped-key signature mismatch and a Portal
SSE parser race.

## What happened
1. Morning PK audit ran clean (after Task Scheduler double-fire fix)
2. Git origins repaired on autoagent + bifrost-router + bifrost-kb
3. Qwen3.6-35B-A3B bench → REJECTED (-12.4% /coding delta)
4. Attempted to rebuild Router for v73 scoped-key fix → CrashLoopBackOff
5. Discovered 4 regressions in 24d338e:
   - Dict[str, Any] without importing Dict/Any (v73a fix)
   - SubtaskID type alias referenced but not defined (v73a fix)
   - logger.* calls without module-level logger (v73b fix)
   - Entire StateGraph construction blocks DELETED (v73c fix)
6. Router v73c deployed clean, AUTOPILOT end-to-end works via API
7. Portal still broken → SSE one-chunk race condition, non-streaming fix queued
8. Session O-SAFETY brief drafted to prevent future 24d338e-class issues
9. ARCHITECT-0 brief drafted (script-first, pod-queued for ARCHITECT-1)
10. Session capture brief (this one) drafted

## Commits
- bifrost-router@4360049 — v73 (scoped-key signature)
- bifrost-router@7a29de2 — v73b (module logger)
- bifrost-router@49b019a — v73c (restored StateGraph wiring)
- autoagent@e51fda6 — Qwen3.6 bench data

## What's still open
- Portal AUTOPILOT non-streaming fix (brief ready)
- L: drive remap (brief ready)
- Session O-SAFETY install (brief ready — user running now)
- ARCHITECT-0 build (brief ready)
- Demo recording (deferred to next week per broken-system rule)

## Lessons
- py_compile is not a safety gate; overnight commits need full import + build + smoke
- Signature mismatches on API endpoints cascade silently when gated by auth headers that are disabled in open mode
- SSE one-chunk responses race with React state initialization
- Four regressions from one overnight commit = evaluator needs runtime gates, not just diff judgment

## Next session
Probably: Portal fix + L: remap + start Session O-SAFETY if not done
