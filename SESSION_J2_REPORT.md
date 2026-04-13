# SESSION J2 REPORT — Project-Aware Bridge + rawmark + Overnight Training
> **Date:** 2026-04-13
> **Duration:** ~35 min active
> **Machine:** Bifrost (primary), Forge (benchmarks)

---

## Block 0 — Collect J+ Results
- No J+ report found
- No DECISIONS_NEEDED.md
- Router healthy: `{"status":"ok","service":"bifrost-router"}`
- Git state clean on both repos

## Block 1 — Project-Aware Bridge: Paperless -> bifrost-kb
**Status: WRITTEN LOCALLY — deploy pending Hearth SSH access**

- `hearth_deploy/bridge_to_kb.py`: Paperless post-consume script
  - Reads DOCUMENT_ID, DOCUMENT_SOURCE_PATH, DOCUMENT_ORIGINAL_FILENAME
  - Queries Paperless API for tags -> maps to project (personal, client, rfp, default)
  - Falls back to source path folder detection
  - Uploads to bifrost-kb /upload with project parameter
- `hearth_deploy/docker_compose_patch.md`: Full deployment instructions
  - Volume mount for scripts dir
  - PAPERLESS_POST_CONSUME_SCRIPT env var
  - Tag creation commands
  - Test procedure

**Blocker:** SSH to Hearth (192.168.2.4) failing — ed25519 key not authorized. Need to add Bifrost's public key to Hearth's authorized_keys.

## Block 2 — Cloud_AI Direct Watch Path
**Status: DOCUMENTED — deploy pending Hearth SSH access**

Instructions in `hearth_deploy/docker_compose_patch.md`:
- Mount F:/Documents/Cloud_AI as /data/inbox-cloud-ai in bifrost-kb container
- Patch pipeline.py for INBOX_PATHS env var (comma-separated multi-inbox)
- Each inbox maps to project by folder name

**Blocker:** Same SSH issue. Also need to read pipeline.py from container before patching.

## Block 3 — Router Project-Aware RAG Enrichment
**Status: COMPLETE**

Patched `D:\Projects\bifrost-router\main.py`:
- `/v1/chat/completions`: Reads `X-Bifrost-Project` header or `body.project`, passes to `_query_kb_async()`
- `/search`: Reads `body.project` (POST) or `query_params.project` (GET), passes to `_query_kb_async()`
- `/rag`: Already had project support (no change needed)

Committed: `dd4f999`

## Block 4 — rawmark.io Benchmark Push
**Status: COMPLETE**

| Model | Hardware | tok/s | Pushed |
|-------|----------|-------|--------|
| bifrost-t1b | bifrost (Ryzen 9 3950X + RX 9070 XT) | 9.0 | cfbe37a |
| bifrost-t2-gemma4 | forge (Ryzen AI Max+ 395 / Radeon 8060S) | 47.9 | 99d19f1 |

Both auto-deployed to rawmark.io via Cloudflare Pages.

## Block 5 — Overnight Training Config
**Status: COMPLETE**

`overnight_run.py`:
- Rotates: coding (5 cycles) -> general (5 cycles) -> research (5 cycles)
- 1-hour timeout per profile
- Calls pk_sync.sync() at end
- CLI: `--cycles-per-profile N`, `--timeout SECS`

Committed: `e45e331`

**Task Scheduler update:** Manual — update BIFROST-Session-O-AutoAgent to use `python overnight_run.py`

## Block 6 — MP v3.10
**Status: COMPLETE**

`MASTER-PLAN-STATUS-v3_10.md` written to bifrost-platform with:
- Session J2 changelog (project-aware KB, overnight training, benchmarks)
- Infrastructure status (all nodes)
- Model fleet table
- Phase 2 milestone checklist
- Pending Hearth deployment items

Committed: `39c3e08`

L: drive not accessible — pk-upload copy skipped.

## Block 7 — Commits
| Repo | Commit | Description |
|------|--------|-------------|
| bifrost-router | `dd4f999` | project-aware RAG in main.py |
| autoagent | `e45e331` | overnight_run.py + hearth_deploy/ |
| bifrost-platform | `cfbe37a` | rawmark: bifrost-t1b on bifrost |
| bifrost-platform | `99d19f1` | rawmark: bifrost-t2-gemma4 on forge |
| bifrost-platform | `39c3e08` | MP v3.10 |

---

## TODO for Next Session
1. **Fix SSH to Hearth**: Add Bifrost ed25519 pubkey to Hearth authorized_keys
2. **Deploy Block 1**: Copy bridge_to_kb.py, patch docker-compose, create tags
3. **Deploy Block 2**: Read pipeline.py from container, patch for multi-inbox, rebuild
4. **Task Scheduler**: Update BIFROST-Session-O-AutoAgent to `python overnight_run.py`
5. **Start overnight run**: Verify all 3 profiles complete with 90%+ acceptance
6. **L: drive**: Check why not mounted — needed for pk-upload sync
