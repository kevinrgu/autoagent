# Session O -- Three-Profile Switcher Report

**Date:** 2026-04-13
**Duration:** ~3 hrs

---

## 1. Fleet Verification

### Bifrost (127.0.0.1:11434) -- RDNA4 Vulkan
| Model | Size |
|-------|------|
| mistral-small3.1:24b | 14.4 GB |
| bifrost-t1b-gemma4 | 8.9 GB |
| gemma4:e4b | 8.9 GB |
| bifrost-t-search | 8.6 GB |
| bifrost-t1b | 8.6 GB |
| bifrost-interactive | 8.6 GB |
| qwen3:14b | 8.6 GB |
| qwen-platform | 8.4 GB |
| qwen2.5-coder:14b | 8.4 GB |
| llama3.1:8b | 4.6 GB |
| qwen2.5-coder:7b | 4.4 GB |
| qwen3.5:0.8b | 1.0 GB |
| nomic-embed-text | 0.3 GB |

### Forge (192.168.2.50:11434)
| Model | Size |
|-------|------|
| llama4:scout | 62.8 GB |
| bifrost-t2p5-scout | 62.8 GB |
| bifrost-t2-gemma4 | 16.8 GB |
| gemma4:26b | 16.8 GB |
| gemma4:e4b | 8.9 GB |
| gemma3:4b | 3.1 GB |
| nomic-embed-text | 0.3 GB |

**Note:** llama4:scout returns 500 errors (unavailable for inference). bifrost-t2-gemma4 (gemma4:26b) is the active T2 model. No qwen3:30b or deepseek-r1:70b present -- fleet has changed since prior memory entries.

### Hearth (192.168.2.4:11434)
| Model | Size |
|-------|------|
| gemma4:e4b | 8.9 GB |
| gemma4:e2b | 6.7 GB |
| qwen3.5:9b | 6.1 GB |
| qwen3:8b | 4.9 GB |
| qwen2.5-coder:7b | 4.4 GB |
| mistral:7b | 4.1 GB |
| gemma3:4b | 3.1 GB |
| nomic-embed-text | 0.3 GB |

---

## 2. Benchmark Results

| Model | Node | Available | Avg tok/s |
|-------|------|-----------|-----------|
| gemma4:e4b | Bifrost | YES | **72.9** |
| gemma4:e4b | Forge | YES | 50.8 |
| bifrost-t1b | Bifrost | YES | 48.2 |
| bifrost-t2-gemma4 (26b) | Forge | YES | 47.2 |
| gemma4:e4b | Hearth | YES | 22.2 |
| qwen2.5-coder:7b | Hearth | YES | 20.5 |
| mistral:7b | Hearth | YES | 14.8 |
| mistral-small3.1:24b | Bifrost | YES | 6.3 |
| qwen2.5-coder:14b | Bifrost | YES | 5.9 |
| qwen3:14b | Bifrost | NO (timeout) | -- |
| qwen3:8b | Hearth | NO (timeout) | -- |
| llama4:scout | Forge | NO (500 error) | -- |

**Key findings:**
- gemma4:e4b on Bifrost is the fastest model (72.9 tok/s) -- 8.9GB quantized
- bifrost-t1b on Bifrost remains the best executor (48.2 tok/s, good code output)
- bifrost-t2-gemma4 on Forge is the best large evaluator (47.2 tok/s, 26B params)
- qwen2.5-coder:14b on Bifrost is slow (5.9 tok/s) but code-specialized
- qwen3:14b and qwen3:8b both timed out (GPU contention or model issues)

---

## 3. Profile Assignments

### /coding
| Role | Model | Node | Rationale |
|------|-------|------|-----------|
| Proposer | mistral-small3.1:24b | Bifrost | Crisp proposals, 6.3 tok/s |
| Executor | bifrost-t1b | Bifrost | Fast coding, 48.2 tok/s |
| Evaluator | qwen2.5-coder:14b | Bifrost | Code-specialized judgment |
| Target | autopilot_graph.py + subtask_graph.py | | |
| num_ctx | 16384 | | |

### /general
| Role | Model | Node | Rationale |
|------|-------|------|-----------|
| Proposer | mistral-small3.1:24b | Bifrost | Good proposal quality |
| Executor | bifrost-t1b | Bifrost | Fast execution |
| Evaluator | bifrost-t2-gemma4 (26B) | Forge | Best prose/quality judgment |
| Target | autopilot_graph.py | | Focus: _is_rfp_task, _build_rfp_dag |
| num_ctx | 16384 | | |

### /research
| Role | Model | Node | Rationale |
|------|-------|------|-----------|
| Proposer | mistral-small3.1:24b | Bifrost | Consistent proposals |
| Executor | bifrost-t1b | Bifrost | Fast execution |
| Evaluator | bifrost-t2-gemma4 (26B) | Forge | Deep reasoning for research eval |
| Target | subtask_graph.py | | Research pipeline |
| num_ctx | 16384 | | |

---

## 4. Per-Profile Test Results (5 cycles each)

### /coding: 1/5 accepted (20%)
- qwen2.5-coder:14b evaluator is very strict
- Rejected proposals for: undefined references, missing context, breaking changes
- Evaluator correctly identified issues like missing `_run_async` definitions in scope
- Strictness is desirable for code safety but acceptance rate needs tuning

### /general: 3/5 accepted (60%)
- bifrost-t2-gemma4 evaluator balances quality and acceptance well
- Accepted: log level improvements, subtask_id traceability additions
- Rejected: breaking signature changes, undefined references
- Best-performing profile in initial testing

### /research: 0/5 accepted (0%)
- Evaluator caught real issues: incomplete mappings, logic contradictions, type inconsistencies
- subtask_graph.py is complex -- proposals struggle with its architecture
- Research pipeline needs more targeted proposer prompts or architecture context

---

## 5. /general 15-Cycle Results

**Result: 8/15 accepted (53%)**

- Early cycles (1-5): strong start, ~5 accepted
- Mid cycles (6-10): rejection feedback loop kicked in, slower acceptance
- Late cycles (11-15): slight recovery as rejection patterns stabilized
- Pattern: evaluator gets stricter as file evolves (accumulated changes increase validation surface)

---

## 6. RFP Regression Test

**Status: PASS**

After 15-cycle /general training, AUTOPILOT RFP test submitted:
- Subtasks generated: 6
- Pipeline status: COMPLETE
- Response length: 23,423 chars
- Output structure: Executive Summary, Technical Approach, Team Qualifications, Past Performance, Pricing Narrative
- No regression detected -- RFP output quality maintained post-training

---

## 7. Architecture: How --profile Works

### File Layout
```
C:\Users\jhpri\projects\autoagent\
  bifrost_cycle.py          # Main harness (patched with profile loading)
  profiles.json             # Profile definitions (model/URL/target/rules)
  active_profile.txt        # Optional: default profile when --profile not specified
  research_benchmark.json   # Benchmark data from all nodes
  research_models.py        # Benchmark runner script
  profiles\
    CODING\
      decisions_coding.md   # Decision log for /coding
      run_coding_test.txt   # 5-cycle test output
    GENERAL\
      decisions_general.md  # Decision log for /general
      run_general_test.txt  # 5-cycle test output
      run_general_15cycle.txt  # 15-cycle run output
    RESEARCH\
      decisions_research.md # Decision log for /research
      run_research_test.txt # 5-cycle test output
```

### CLI Usage
```bash
# Run specific profile
python bifrost_cycle.py --profile coding --max-cycles 5
python bifrost_cycle.py --profile general --max-cycles 15
python bifrost_cycle.py --profile research --max-cycles 5

# Falls back to active_profile.txt if --profile not specified
# Falls back to program.md defaults if no profile active
```

### Profile Loading Flow
1. Parse `--profile` CLI arg (coding/general/research)
2. If not specified, check `active_profile.txt`
3. Load profile from `profiles.json`
4. Override globals: PROPOSER/EXECUTOR/EVALUATOR model+URL, NUM_CTX, SYSTEM_RULES
5. Set profile-specific decision log path (`profiles/{PROFILE}/decisions_{profile}.md`)
6. Profile system_rules are injected into proposer prompt
7. Existing program.md flow still works as fallback (backward compatible)

---

## 8. Recommended Next Steps

1. **Tune /coding evaluator**: qwen2.5-coder:14b is too strict (1/5 = 20%). Options:
   - Add more context to evaluator prompt (show full function signatures)
   - Switch to bifrost-t2-gemma4 for coding eval too (3/5 for general)
   - Increase evaluator prompt context window

2. **Fix /research pipeline**: 0/5 needs attention. Options:
   - Provide architecture summary to proposer for subtask_graph.py
   - Target specific functions instead of whole file
   - Research-specific proposer prompts with domain context

3. **Profile-specific proposer prompts**: General profile could benefit from RFP-domain prompt engineering (terminology, section structure expectations)

4. **Pull deepseek-r1:70b on Forge**: If VRAM permits alongside gemma4:26b, would be a strong research evaluator candidate

5. **Track per-profile metrics**: Add acceptance rate tracking over time to identify which profiles/rules need adjustment

6. **Fleet config update**: Update fleet_config.json -- qwen3:30b gone, llama4:scout broken (500s), actual T2 = bifrost-t2-gemma4 (gemma4:26b)
