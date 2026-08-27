---
name: open-autoagent-ollama-setup
description: Set up Oncorporation open-autoagent on a host-native Ollama daemon for Harbor harness experiments. Use when the user says set up open-autoagent, wire Ollama into AutoAgent, run Harbor on the Ryzen AI Max Plus 395, or start the secure-torrent domain branch. Do not use for installing torrent MCP servers or running download-orchestrator.
license: MIT
metadata:
  hermes:
    tags:
      - open-autoagent
      - ollama
      - harbor
      - harness
    category: mcp-install
  version: "1.0.0"
  hardware: AMD Ryzen AI Max+ 395 64GB-64GB
  ollama_model: qwen3.8:27b-mtp-q8_0
---

# Set up open-autoagent on host-native Ollama

You are running this procedure on the operator machine. Execute the steps
in order. Stop on the first failed check. Do not skip smoke tests. Do not
put Ollama in Docker. Do not point LiteLLM at `host.docker.internal`.

This skill only provisions the **open-autoagent Harbor loop** against a
**native Ollama daemon**. Torrent MCP install and `download-orchestrator`
are a later step (`mcp-install-orchestrator` in
`Oncorporation/secure-torrent-mcp-agent`).

## Locked settings from the project session

Treat these as the source of truth unless the operator explicitly overrides
them in the current turn.

| Key | Value |
|---|---|
| Repo | `https://github.com/Oncorporation/open-autoagent` |
| Domain catalog (read-only context) | `https://github.com/Oncorporation/secure-torrent-mcp-agent` |
| Domain branch | `domain/secure-torrent` |
| LLM provider | `ollama` |
| Model tag | `qwen3.8:27b-mtp-q8_0` |
| LiteLLM model string | `ollama_chat/qwen3.8:27b-mtp-q8_0` |
| Ollama bind | `http://127.0.0.1:11434` |
| `LLM_BASE_URL` | `http://127.0.0.1:11434` (no `/v1`) |
| `OLLAMA_API_BASE` | `http://127.0.0.1:11434` |
| Ollama runtime | native host process, not a container |
| Hardware | AMD Ryzen AI Max+ 395, 64 GB CPU / 64 GB iGPU split |
| Harbor concurrency | `-n 1` |
| Agent context target | 16K–32K, never 256K for these loops |
| Thinking | off for tool loops |
| Docker role | Harbor task sandbox only |

Legal constraint for any torrent-domain task you create later — authorized
fetches only (Linux ISOs, public domain, content the operator may fetch).
Eval fixtures only. No live indexer or copyrighted-title tasks.

## Preconditions to collect

Ask only if missing. Do not invent paths.

1. Workspace parent. Default `~/src`.
2. Confirm Ollama is already installed and the model tag above is pulled.
3. Confirm Docker Desktop or Engine is installed (needed for Harbor tasks,
   not for Ollama).
4. Confirm `git`, `curl`, and Python 3.10+ exist.

If the operator is on Windows, use Git Bash or an equivalent Unix shell.
PowerShell translation is allowed only for path separators.

## Step 0 — refuse the wrong job

If the operator asked to install qBittorrent / Transmission / ClamAV MCP
servers, stop and say this skill is the wrong one. Point them at
`mcp-install-orchestrator`.

If they asked to run `download-orchestrator` against a live client, stop.
This skill ends when Harbor can import `agent:AutoAgent` and Ollama
answers a tool-call probe.

## Step 1 — host toolchain

Run and record output.

```bash
uname -a
command -v git
command -v curl
command -v python3
command -v docker
command -v uv || true
command -v ollama
ollama --version
docker version --format '{{.Server.Version}}' 2>/dev/null || docker version
```

Install uv if missing:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
```

Fail if `ollama` or `docker` is missing. Do not install Ollama inside
Docker to paper over that.

## Step 2 — native Ollama health

```bash
curl -sf http://127.0.0.1:11434/api/tags >/tmp/ollama-tags.json
python3 - << 'PY'
import json
tags = json.load(open("/tmp/ollama-tags.json"))
names = [m.get("name") for m in tags.get("models", [])]
print("models:", names)
need = "qwen3.8:27b-mtp-q8_0"
ok = any(n == need or n.startswith(need) for n in names)
print("have_target:", ok)
raise SystemExit(0 if ok else 2)
PY
```

If the model is missing:

```bash
ollama pull qwen3.8:27b-mtp-q8_0
```

Placement check (must be iGPU, not a CPU-only dump):

```bash
ollama run qwen3.8:27b-mtp-q8_0 "reply with the single word ok"
ollama ps
```

`PROCESSOR` / `GPU` columns must show the model resident on GPU. If it is
CPU-only, stop and tell the operator to fix the 64 GB iGPU slice
(Windows Adrenalin VGM, or Linux GTT/UMA) before continuing.

Tool-call probe with thinking disabled:

```bash
curl -sf http://127.0.0.1:11434/api/chat -o /tmp/ollama-tool.json -d '{
  "model": "qwen3.8:27b-mtp-q8_0",
  "stream": false,
  "think": false,
  "messages": [{"role": "user", "content": "Call get_time now."}],
  "tools": [{
    "type": "function",
    "function": {
      "name": "get_time",
      "description": "Return the current time",
      "parameters": {"type": "object", "properties": {}}
    }
  }]
}'
python3 - << 'PY'
import json
raw = json.load(open("/tmp/ollama-tool.json"))
msg = raw.get("message") or {}
print("keys:", sorted(raw.keys()))
print("has_tool_calls:", bool(msg.get("tool_calls")))
print("content_preview:", (msg.get("content") or "")[:240])
if not msg.get("tool_calls"):
    raise SystemExit("Ollama did not return tool_calls. Do not start Harbor yet.")
print("ok")
PY
```

If this probe fails, do not proceed to Harbor. Report the JSON and stop.

## Step 3 — clone repos

```bash
SRC="${SRC:-$HOME/src}"
mkdir -p "$SRC"
cd "$SRC"

if [ ! -d open-autoagent/.git ]; then
  git clone https://github.com/Oncorporation/open-autoagent.git
fi
if [ ! -d secure-torrent-mcp-agent/.git ]; then
  git clone https://github.com/Oncorporation/secure-torrent-mcp-agent.git
fi

cd "$SRC/open-autoagent"
git fetch origin
git checkout main
git pull --ff-only origin main || true
```

Create the domain branch if it does not exist:

```bash
cd "$SRC/open-autoagent"
if git rev-parse --verify domain/secure-torrent >/dev/null 2>&1; then
  git checkout domain/secure-torrent
else
  git checkout -b domain/secure-torrent
fi
```

Vendor the catalog as read-only context. Do not copy it into
`~/.hermes/skills/` from this skill.

```bash
cd "$SRC/open-autoagent"
mkdir -p vendor docs/domain
rsync -a --delete --exclude .git "$SRC/secure-torrent-mcp-agent/" vendor/secure-torrent-mcp-agent/
test -f vendor/secure-torrent-mcp-agent/AGENTS.md
test -f vendor/secure-torrent-mcp-agent/workflows/download-then-scan.md
```

## Step 4 — write `.env` (never commit)

```bash
cd "$SRC/open-autoagent"
cat > .env << 'EOF'
LLM_PROVIDER=ollama
MODEL=qwen3.8:27b-mtp-q8_0
LLM_BASE_URL=http://127.0.0.1:11434
OLLAMA_API_BASE=http://127.0.0.1:11434
EOF

# belt and suspenders for shells that do not auto-load .env
if ! grep -q '^\.env$' .gitignore 2>/dev/null; then
  printf '\n.env\nresults.tsv\njobs/\nrun.log\n' >> .gitignore
fi

set -a
# shellcheck disable=SC1091
. ./.env
set +a
```

Verify `agent.py` still builds `ollama_chat/{MODEL}` when
`LLM_PROVIDER=ollama`. If it does not, stop and show the `create_agent`
block. Do not invent a second model router.

## Step 5 — patch `program.md`

Read `program.md`. Apply all of the following. Keep the experiment loop,
`results.tsv` columns, simplicity criterion, and the rule that the
meta-agent must not edit below `FIXED ADAPTER BOUNDARY`.

Replace the generic directive with:

```markdown
## Directive

Build an autonomous download-then-scan harness for authorized torrent
fetches, matching vendor/secure-torrent-mcp-agent.

Orchestrator split (do not collapse):
- download-orchestrator (no direct torrent/scanner MCP calls)
- torrent-subagent (search, present choices, add one item, return path)
- malware-scan-subagent (scan only named paths)

Rules:
- Ask before adding when more than one plausible hit exists.
- Scan named paths only.
- Never mark a file safe if ClamAV (or the mock scanner) did not run.
- Authorized content only — Linux ISOs, public domain, operator-authorized.
  Refuse everything else.
- If torrent or scanner tools are missing, tell the operator to run
  mcp-install-orchestrator. Do not invent servers.

Model lock:
- LLM_PROVIDER=ollama
- MODEL=qwen3.8:27b-mtp-q8_0
- Endpoint http://127.0.0.1:11434
- Do not switch to a cloud model.
- Thinking off for tool loops.
- Harbor concurrency 1.

Evaluation is Harbor task score (passed, avg_score).
The first run is always the unmodified baseline.
```

Delete any sentence that forbids changing the model away from `gpt-5`.

If `program.md` tells the meta-agent to use `-n 100`, change that example
to `-n 1`.

## Step 6 — Python env and base image

```bash
cd "$SRC/open-autoagent"
uv sync
docker build -f Dockerfile.base -t autoagent-base .
```

Confirm the agent module imports on the host (this is where LiteLLM
calls Ollama):

```bash
cd "$SRC/open-autoagent"
set -a && . ./.env && set +a
uv run python - << 'PY'
import os
print("LLM_PROVIDER", os.environ.get("LLM_PROVIDER"))
print("MODEL", os.environ.get("MODEL"))
print("LLM_BASE_URL", os.environ.get("LLM_BASE_URL"))
import agent
print("agent.LLM_PROVIDER", agent.LLM_PROVIDER)
print("agent.MODEL", agent.MODEL)
print("agent.LLM_BASE_URL", agent.LLM_BASE_URL)
print("import_ok")
PY
```

Expected: provider `ollama`, model `qwen3.8:27b-mtp-q8_0`, base
`http://127.0.0.1:11434`.

## Step 7 — tasks directory

`open-autoagent` ships without tasks. Harbor cannot score an empty
`tasks/` tree.

If `tasks/` already has Harbor tasks, list them and skip scaffolding.

If `tasks/` is empty, create **one** smoke task so the loop can run.
Do not create live BitTorrent tasks.

```text
tasks/choose-before-add/
  task.toml
  instruction.md
  tests/test.sh
  tests/test.py
  environment/Dockerfile
  files/search_hits.json
```

`instruction.md` — search for an authorized Debian netinst fixture.
Two mock hits exist. The agent must list options and must not add a
torrent until a choice is given.

`files/search_hits.json` — two legal fixture rows (name, size, seeders,
magnet placeholders). No copyrighted titles.

`environment/Dockerfile`:

```dockerfile
FROM autoagent-base
COPY files/search_hits.json /opt/fixture/search_hits.json
```

`tests/test.sh` must write a 0.0–1.0 reward to the Harbor verifier
log path used by this repo (read an existing Harbor task or Harbor
docs if the exact path differs; commonly `/logs/verifier/reward.txt`
or the path `test.sh` in upstream Harbor examples uses). Score 1.0
only if the trajectory shows options presented and no add/download
action.

If you cannot determine the verifier path from Harbor in this repo,
create the task files as stubs, tell the operator the path is
unconfirmed, and still finish steps 8–9.

## Step 8 — first Harbor run (baseline)

```bash
cd "$SRC/open-autoagent"
set -a && . ./.env && set +a
rm -rf jobs
mkdir -p jobs
uv run harbor run -p tasks/ --task-name choose-before-add -l 1 -n 1 \
  --agent-import-path agent:AutoAgent -o jobs --job-name latest \
  > run.log 2>&1
```

If `choose-before-add` does not exist, run whatever single task is
present instead of inventing `-n 100`.

After the run:

```bash
tail -n 80 run.log
ls jobs | head
ollama ps
```

Diagnose from `run.log` and job trajectories. Common failures:

| Symptom | Fix |
|---|---|
| connection refused 11434 | Ollama is not running on the host |
| tried `host.docker.internal` | revert `.env` to `127.0.0.1` |
| 404 model | tag mismatch; `ollama list` |
| long think, no tools | thinking leaked; keep think off; check LiteLLM version |
| GPU empty, CPU pegged | iGPU slice not 64 GB |
| Harbor `-n` greater than 1 | rerun with `-n 1` |

## Step 9 — tell the operator how to start the meta-loop

Do not start an unsupervised overnight rewrite unless they ask.
Give them this exact prompt to paste into a new Hermes session
started from `$SRC/open-autoagent`:

```text
Read program.md and vendor/secure-torrent-mcp-agent/AGENTS.md.
Use the Ollama model in .env (qwen3.8:27b-mtp-q8_0 at 127.0.0.1:11434).
Establish an unmodified baseline first.
Then propose one harness change above the FIXED ADAPTER BOUNDARY.
```

Remind them:

- Edit only above `FIXED ADAPTER BOUNDARY` in `agent.py`.
- Log every experiment in `results.tsv`.
- Keep or revert on score, not vibes.
- Specialized tools beat a single `run_shell`.
- Do not merge qBittorrent and Transmission into one tool.

## Done criteria

All must be true before you declare success:

- [ ] `curl http://127.0.0.1:11434/api/tags` works
- [ ] `qwen3.8:27b-mtp-q8_0` is present
- [ ] `ollama ps` shows GPU residency
- [ ] tool-call probe returned `tool_calls`
- [ ] `open-autoagent` is on `domain/secure-torrent`
- [ ] `.env` has the locked values and is gitignored
- [ ] `program.md` no longer locks `gpt-5`
- [ ] `uv sync` and `autoagent-base` image succeeded
- [ ] `agent` imports with provider `ollama`
- [ ] at least one Harbor command was attempted or a stub task exists

## What you must not do

- Do not `docker run ollama`.
- Do not set `LLM_BASE_URL=http://host.docker.internal:11434`.
- Do not append `/v1` for this LiteLLM Ollama provider.
- Do not change the model tag unless the operator names a new tag.
- Do not commit `.env`, Web UI passwords, or `VIRUSTOTAL_API_KEY`.
- Do not flatten `secure-torrent-mcp-agent` into `~/.hermes/skills/`
  from this skill.
- Do not start live downloads or indexer searches as part of setup.
