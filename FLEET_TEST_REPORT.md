# BIFROST Fleet-Wide Completions Test Report

**Date:** 2026-04-14
**Router:** v69 @ http://192.168.2.4:8089 (Hearth k3d, NodePort)
**Mode:** WORKSHOP / Profile B-Dual / Strategy INTERACTIVE
**Test prompt (direct):** `What is 2+2? Answer in one word.`
**Sources:** `fleet_config.json` (read from Router pod + local), `config.py` CASCADE_TABLES, `/fleet/ps`, `/status`, `/classify`

---

## TL;DR

**Update 2026-04-14 19:15 ET — both daemon outages fixed; one phantom remains.**

- **All 11 local Ollama/llama-server endpoints now answer HTTP from the network.**
- **`T2_5` Forge Ollama** — fixed. Root cause: systemd unit had `OLLAMA_KEEP_ALIVE=5m` but no `OLLAMA_HOST`, so the daemon defaulted to `127.0.0.1:11434`. Added `Environment=OLLAMA_HOST=0.0.0.0:11434` to the drop-in, `daemon-reload`, restart. Now binds `*:11434` and answers `bifrost-t2p5-scout` + `bifrost-t2-gemma4` from the LAN. llama-server :11437 unaffected.
- **`T1A_HEARTH` Hearth dGPU Ollama** — fixed at the daemon and firewall level, **but the model `bifrost-1a-hearth` is a phantom in `fleet_config.json` that has never existed** in the dGPU instance's model store (`L:\models\ollama` only contains `qwen2.5-coder:7b`, `qwen3:4b`, `qwen3:8b`, `sam860/qwen3:8b-Q5_K_XL`). Two root causes had to be peeled off:
  1. **No daemon was running on :11434.** Only the Vega8 :11436 instance was up. The launcher script `L:\repos\bifrost-platform\hearth\hearth-ollama-5700xt.ps1` was never persistent. Fix: registered scheduled task `Ollama-dGPU-5700XT` (AtLogon, RunLevel=Highest, RestartCount=3) and triggered it. PID 13144 now holds `*:11434`.
  2. **Windows Firewall had two `ollama.exe` Block rules** (Profile=Public) and only an Allow rule for the Vega8 port (:11436, Profile=Private). No Allow rule for :11434. Fix: added `New-NetFirewallRule -DisplayName 'Ollama-dGPU-11434' -Direction Inbound -Protocol TCP -LocalPort 11434 -Action Allow -Profile Any`. `qwen2.5-coder:7b` now answers `"4"` from the network.
- **Pending follow-up (not in this session — needs user approval):** create a `bifrost-1a-hearth` Modelfile that wraps `qwen2.5-coder:7b`, OR update `fleet_config.json` to use `qwen2.5-coder:7b` directly.
- **3 thinking-model tiers (`1a-overflow`, `2`, `search`, plus `forge-gemma4`) still emit verbose `reasoning` content but the Router strips it**, leaving end-users with empty `content` strings unless `max_tokens` is huge. **Unchanged** — separate from the daemon outages.
- **Classifier under-buckets prompts**: a 10-token "Explain TCP vs UDP" prompt classifies as `TRIVIAL` (score 3.0/0/0/0) because none of its words match the `moderate_keywords` list. **Unchanged.**
- **Cascade fallback works**: TRIVIAL still goes `T1A_HEARTH → T1A_OVERFLOW` because the router pod has cached the phantom-model 404 path. The fallthrough is fast (1.4s) instead of the previous 21s timeout.

---

## Step 1 — Tiers defined in `fleet_config.json`

| Tier | Label | Machine | Type | Endpoint | active_model |
|------|-------|---------|------|----------|--------------|
| `1a-coder` | Bifrost T1A Coder | bifrost (RX 9070 XT) | ollama | http://192.168.2.33:11434 | bifrost-1a-coder |
| `1a-instruct` | Bifrost T1A Instruct | bifrost (RX 9070 XT) | ollama | http://192.168.2.33:11434 | bifrost-1a-instruct |
| `1b` | Bifrost T1B Interactive | bifrost (RX 9070 XT) | ollama | http://192.168.2.33:11434 | bifrost-t1b |
| `1a-hearth` | Hearth T1A | hearth (RX 5700 XT) | ollama | http://192.168.2.4:11434 | bifrost-1a-hearth |
| `1a-overflow` | Hearth Vega8 Overflow | hearth (Vega 8 iGPU) | ollama | http://192.168.2.4:11436 | bifrost-1a-overflow |
| `2` | Forge T2 (llama-server) | forge (Radeon 8060S) | llama_server | http://192.168.2.50:11437/v1 | unsloth/Qwen3-1.7B-GGUF:Q4_0 |
| `2.5` | Forge T2.5 | forge (Radeon 8060S) | ollama | http://192.168.2.50:11434 | bifrost-t2p5-scout |
| `npu` | Forge T_NPU FLM | forge (XDNA2 NPU) | ollama | http://192.168.2.50:8003 | gemma3:4b |
| `search` | Bifrost T_SEARCH (Context-1) | bifrost (RX 9070 XT) | ollama | http://192.168.2.33:11434 | bifrost-t-search |
| `3-claude` | Claude Sonnet 4.6 | cloud | anthropic | (cloud) | claude-sonnet-4-6 |
| `3-gemini` | Gemini 2.5 Flash | cloud | openai_compat | (cloud) | gemini-2.5-flash |
| `3-fast` | Groq Llama 3.3-70B | cloud | openai_compat | (cloud) | llama-3.3-70b-versatile |

Cloud tiers (`3-*`) skipped per request — focus is on local fleet.

---

## Step 2 — Direct endpoint tests

Prompt: `What is 2+2? Answer in one word.`  `max_tokens=10` initially, retried with `200–300` for thinking models.

| Tier | Endpoint | Model | HTTP | Latency | Reported tok/s | Output | Coherent |
|------|----------|-------|------|---------|----------------|--------|----------|
| `1a-hearth` | http://192.168.2.4:11434 | bifrost-1a-hearth | **000** (timeout 21s) | — | — | `Failed to connect` | **DOWN** |
| `1a-overflow` | http://192.168.2.4:11436 | bifrost-1a-overflow | 200 | 67.3s @ 200 tok | ~3 tok/s | content="" + 200 tok of `reasoning` ending in "4" | yes (in reasoning) |
| `1a-coder` | http://192.168.2.33:11434 | bifrost-1a-coder | 200 | 36.6s (cold) | n/a (Ollama) | `"4"` | yes |
| `1a-instruct` | http://192.168.2.33:11434 | bifrost-1a-instruct | 200 | 9.3s | n/a | `"Four"` | yes |
| `1b` | http://192.168.2.33:11434 | bifrost-t1b | 200 | 10.5s | n/a | `"Four."` | yes |
| `2` (Forge llama-server) | http://192.168.2.50:11437/v1 | unsloth/Qwen3-1.7B-GGUF:Q4_0 | 200 | 1.16s | **149 tok/s decode**, 142 tok/s prefill | `"4"` (with 167 tok of `reasoning_content`) | yes |
| `2.5` (Forge Ollama) | http://192.168.2.50:11434 | bifrost-t2p5-scout | **000** (refused 2s) | — | — | `Failed to connect` | **DOWN** |
| `npu` (Forge FLM) | http://192.168.2.50:8003 | gemma3:4b | 200 | 1.37s | **12.3 tok/s decode**, 19.1 tok/s prefill | `"Four.\n"` | yes |
| `search` (Bifrost Context-1) | http://192.168.2.33:11434 | bifrost-t-search | 200 | 9.0s @ 300 tok | n/a | `"Four. Confidence: 1.0"` (+ 152 tok reasoning) | yes |

### Why the two are down

**Hearth dGPU Ollama (`192.168.2.4:11434`)** — confirmed via `netstat -an | findstr 11434` on Hearth: **no listener on either localhost or 0.0.0.0**. Only `:11436` (overflow Vega8) and `:8089` (Router NodePort) are up. `tasklist | findstr ollama` shows two `ollama.exe` processes, but they're serving the Vega8 instance, not the dGPU. The "Hearth dGPU" service (RX 5700 XT) appears to never have been started, or its bind config doesn't expose port 11434. The Router's own `/fleet/ps` agrees: `"status":"offline","error":"All connection attempts failed"`.

**Forge Ollama (`192.168.2.50:11434`)** — `ss -ltn` on Forge shows:
```
LISTEN 0 4096 127.0.0.1:11434  0.0.0.0:*
```
Process **is running**, but bound to **localhost only** — unreachable from Hearth/Bifrost. This is a recent regression (probably from the K2 session edits when llama-server was put on `:11437` to coexist with Ollama). `OLLAMA_HOST` likely needs to be `0.0.0.0:11434` instead of unset/default `127.0.0.1`. Router `/fleet/ps` confirms offline.

### Thinking-model `content`-vs-`reasoning` issue

Three tiers route to thinking-style models that emit large `reasoning` / `reasoning_content` blocks before producing visible `content`:

- `1a-overflow` (gemma3 reasoning template): 200 reasoning tokens to answer "2+2"; with `max_tokens=10` the visible `content` is empty.
- `2` (Qwen3-1.7B): 167 reasoning tokens, then `"4"`.
- `search` (Context-1 20B): 152 reasoning tokens, then `"Four. Confidence: 1.0"`.

When called **directly**, the OpenAI-format response carries the chain-of-thought in a side channel (`reasoning` for Ollama, `reasoning_content` for llama-server). When called **through the Router**, those side-channel fields are not propagated — the body returned to the client only has `choices[0].message.content`. So a request like `max_tokens=30` to `1a-overflow` via the Router returns a perfectly valid HTTP 200 with `content: ""`, which from the user's POV looks like a broken backend.

This is the highest-impact finding in this report — **`1a-overflow` is the de-facto TRIVIAL handler now that `1a-hearth` is dead**, and it silently returns blank responses unless `max_tokens` is set very high.

---

## Step 3 — Routing through the Router

POST `http://192.168.2.4:8089/v1/chat/completions`, no model field (auto-route).

| Band tested | Prompt | `X-Bifrost-Band` | `X-Bifrost-Tier` | Confidence | Latency | Outcome |
|---|---|---|---|---|---|---|
| TRIVIAL | `Hi` | TRIVIAL | `1a-overflow` | 0.80 | 53.2s | HTTP 200, `content=""`, finish=stop, 30 tokens used (all in stripped reasoning) |
| MODERATE (intent) | `Explain the difference between TCP and UDP in 3 sentences` | **TRIVIAL** (misclassified) | `1a-overflow` | 0.80 | 69.7s | HTTP 200, `content=""`, 200 tokens used (reasoning) |
| COMPLEX | `Implement a thread-safe LRU cache in Python with TTL support` | COMPLEX | `2` | 0.75 | 4.07s | HTTP 200, `content=""`, finish=length, 600 tokens reasoning, **answer never reached** |

### Cascade fallback verification

For the TRIVIAL test, `WORKSHOP.TRIVIAL = [T1A_HEARTH, T1A_OVERFLOW, T1A_CODER, T_NPU]`. The response came back with `tier=1a-overflow`, meaning the Router tried `1a-hearth` first, got the connection refused, and **transparently fell through to `1a-overflow`** — cascade is working as designed. The Router `/status` `escalation_count: 11` over `total_requests: 14` corroborates this — most requests are escalating past the dead first hop.

### Classifier issue (MODERATE → TRIVIAL)

`/classify` on the TCP/UDP prompt:
```
band: TRIVIAL, confidence: 0.8
reasoning: "short prompt (10 tokens); no question, very short, no keywords -> likely autocomplete"
scores: {TRIVIAL: 3.0, MODERATE: 0, COMPLEX: 0, FRONTIER: 0}
```
The keyword-based classifier never matches because `moderate_keywords` (in `config.py:240-248`) doesn't include `explain`, `difference`, `TCP`, `UDP`, or any common Q&A verbs. Anything <200 tokens with no programming keywords classifies TRIVIAL. This isn't broken routing, but it means **MODERATE-band cascades are never exercised by typical prose questions** and end users get the dumbest tier by default.

---

## Step 4 — Router self-view (`/fleet/ps`, `/status`)

`/fleet/ps` (Ollama-only — does not include the llama-server T2 endpoint):

| base_url | machine | status | loaded models | VRAM |
|---|---|---|---|---|
| http://192.168.2.33:11434 | bifrost | **online** | bifrost-t-search:latest | 12.8 GB |
| http://192.168.2.4:11434 | hearth | **offline** ("All connection attempts failed") | — | 0 |
| http://192.168.2.4:11436 | hearth | **online** | bifrost-1a-overflow:latest | 0 |
| http://192.168.2.50:11434 | forge | **offline** ("All connection attempts failed") | — | 0 |
| http://192.168.2.50:8003 | forge | **online** | gemma3:4b | 0 |

`/status` band/tier distribution from the last 14 requests since pod start (~29 min uptime):

```
band_distribution:  TRIVIAL=9, MODERATE=2, COMPLEX=3
tier_distribution:  1a-hearth=6, 1a-overflow=5, 2=3
escalation_count:   11
local_percentage:   100.0
cloud_requests:     0
```

The `1a-hearth: 6` count is **routing intent**, not successful service — every one of those 6 falls through to `1a-overflow` via cascade. The 11 escalations confirm it.

`/v1/models` — only `bifrost-auto`, `bifrost-coder`, `bifrost-cloud` are exposed as model names. There is no per-tier direct-routing model alias (e.g., `bifrost-instruct`, `bifrost-npu`), so callers cannot bypass the classifier without using internal headers.

---

## Step 5 — Issue inventory

### CRITICAL

1. **`T1A_HEARTH` is dead and is the first hop in every TRIVIAL/MODERATE cascade in WORKSHOP / JARVIS / RELAY / JARVIS_OFFLINE / WORKSHOP_OFFLINE modes.**
   Every interactive request pays a TCP connect-fail penalty before falling through. Either start the dGPU Ollama on `192.168.2.4:11434` (RX 5700 XT) or remove `T1A_HEARTH` from the cascades and re-order so `T1A_OVERFLOW` is first.

2. **`T2_5` (Forge Ollama Scout) is bound to localhost.** Process is up, network is not. Fix `OLLAMA_HOST=0.0.0.0:11434` in the Forge Ollama unit. Until then, COMPLEX cascade `[T2, T2_5, T3_CLAUDE]` has only T2 (Qwen3-1.7B Q4_0, a 1.7B model) before going to cloud — no real local "complex" handler.

3. **Router strips `reasoning` / `reasoning_content` from upstream responses, producing empty `content` to the client.** `1a-overflow`, `2`, and `search` all emit reasoning side-channel content. With the default `max_tokens` end users get blank answers. Either:
   - propagate the `reasoning` field through the Router OpenAI surface, or
   - merge `reasoning` + `content` into `content` at the Router layer when upstream finish_reason is `stop`, or
   - set a much higher default `max_tokens` for these tiers (workaround only).

### HIGH

4. **Classifier underrates non-code prose.** "Explain TCP vs UDP" -> TRIVIAL (3.0 score). The keyword lists in `config.py:236-248` need `explain`, `compare`, `difference`, `summarize`, `describe`, `define`, `why`, `how does`, plus a "question word" boost.

5. **`fleet_config.json` and reality have drifted on Hearth.** The config still claims a `1a-hearth` Ollama on `:11434`. Either the model needs to be loaded there or the entry should be deleted/marked as `disabled: true`.

### MEDIUM

6. **`/fleet/ps` doesn't enumerate llama-server endpoints**, so T2 (the only working Forge tier right now) doesn't appear in operator dashboards. Consider adding a `type=llama_server` probe path.

7. **No direct model alias for individual tiers in `/v1/models`** — operators can't pin a request to a specific tier from a vanilla OpenAI client without setting `X-Bifrost-*` headers.

### LOW

8. **`1a-coder` cold-load is 36s** (first call after pod restart). This is just Ollama load time, but it's worth pre-warming if `1a-coder` is the cascade-3 fallback.

---

## Per-tier completion verdict

| Tier | Reachable | Completes | Coherent (high max_tok) | Production usable today |
|---|---|---|---|---|
| `1a-hearth` (daemon) | **yes** (post-fix) | yes (with `qwen2.5-coder:7b`) | yes | **partial** — daemon up, but `bifrost-1a-hearth` model is a phantom; needs Modelfile or fleet_config update |
| `1a-overflow` | yes | yes | yes (in `reasoning`) | partial — only if Router stops stripping reasoning OR raises default max_tokens |
| `1a-coder` | yes | yes | yes | yes |
| `1a-instruct` | yes | yes | yes | yes |
| `1b` | yes | yes | yes | yes |
| `2` (llama-server) | yes | yes | yes (in `reasoning_content`) | partial — same reasoning-strip caveat |
| `2.5` (Forge Ollama Scout) | **yes** (post-fix) | yes | yes | **yes** |
| `forge-gemma4` (Forge Ollama gemma4:26b) | **yes** (post-fix) | yes | yes (in `reasoning`) | partial — same reasoning-strip caveat |
| `npu` (FLM) | yes | yes | yes | yes |
| `search` (Context-1) | yes | yes | yes (in `reasoning`) | partial — same reasoning-strip caveat (but explicit-call only, not in cascades) |
| `3-claude` / `3-gemini` / `3-fast` | not tested | — | — | — |

**Bottom line (post-fix):** All 11 local endpoints answer HTTP. 4 tiers are clean (`1a-coder`, `1a-instruct`, `1b`, `npu`, plus the recovered `2.5-scout`). 4 tiers technically answer but produce empty Router responses due to the reasoning-strip issue (`1a-overflow`, `2`, `search`, `forge-gemma4`). The phantom `bifrost-1a-hearth` model is the last functional gap — daemon is up but the model name in fleet_config has never existed in this instance's store.

### Post-fix audit (parallel run, `fleet_audit_after.json`)

| Tier | OK? | Latency | Notes |
|---|---|---|---|
| `1a-hearth` | 404 | 1.41s | daemon up, model phantom (was: 21s timeout, no listener) |
| `1a-coder` | OK | 1.6s | `"4"` |
| `2-llamaserver` | OK | 1.61s | reasoning-only (`148.4 tok/s`) |
| `npu` | OK | 2.76s | `"Four."` |
| `1a-instruct` | OK | 12.16s | `"Four"` |
| `search` | OK | 23.41s | reasoning-only |
| `2.5-scout` | **OK** | 28.44s | `"Four."` (was: connection refused) |
| `forge-gemma4` | **OK** | 35.7s | reasoning-only (was: connection refused) |
| `bifrost-mistral` | OK | 61.15s | `"4"` |
| `1b` | OK | 83.43s | `"Four."` |
| `bifrost-gemma4` | OK | 111.8s | `"Four"` |
| `1a-overflow` | timeout | 181s | parallel-load contention — solo retry returned in 5s |
