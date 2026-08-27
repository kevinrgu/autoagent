# Setup Workflow

The setup folder now supports **generic repository setup** via optional configuration.

## Workflow

### Option 1: Quick Start (Default)
Use the original hardcoded repo, model, and settings:

```bash
# Linux / macOS
bash setup/install.sh

# Windows PowerShell
.\setup\install.ps1
```

Pick your harness, install, done. Defaults:
- Repo: `https://github.com/Oncorporation/open-autoagent`
- Model: `qwen3.8:27b-mtp-q8_0`
- Ollama: `http://127.0.0.1:11434`

### Option 2: Custom Configuration
Use your own repo, model, hardware, LLM provider:

```bash
# 1. Configure
bash setup/configure.sh          # Linux/macOS
.\setup\configure.ps1            # Windows

# 2. Install
bash setup/install.sh            # Linux/macOS
.\setup\install.ps1              # Windows
```

The installer checks for `.skill-config.json` and uses it to customize the
SKILL.md before copying. If no config exists, defaults are used.

## What Gets Configured

| Setting | Purpose | Default |
|---|---|---|
| Main repo | GitHub/HF/local path to clone | `https://github.com/Oncorporation/open-autoagent` |
| Domain repo | Optional catalog/context repo | `https://github.com/Oncorporation/secure-torrent-mcp-agent` |
| Domain branch | Branch name in main repo | `domain/secure-torrent` |
| LLM provider | `ollama`, `openai`, `anthropic`, `azure` | `ollama` |
| Model | Model name/tag | `qwen3.8:27b-mtp-q8_0` |
| Ollama endpoint | Only if provider=ollama | `http://127.0.0.1:11434` |
| Hardware | Info string (notes only) | `AMD Ryzen AI Max+ 395 64GB-64GB` |

## How It Works

1. **`configure.sh/.ps1`** → prompts → saves to `.skill-config.json`
2. **`install.sh/.ps1`** → reads config (or defaults) → processes `SKILL.md.template` → installs customized SKILL.md
3. **Harness triggers** → runs the skill with custom repo/model/hardware baked in

`.skill-config.json` is gitignored so it never gets committed.

## Adding a New Harness

1. Create `setup/harness/new-harness/` directory
2. **Either:**
   - Pre-build: copy `SKILL.md.template` → `new-harness/SKILL.md` (installers will use it as-is)
   - Or: install will auto-generate from template + config
3. Update installer menu (both `.sh` and `.ps1`) with harness option and trigger instructions
4. Done — installers pick up the new folder automatically

## Files

| File | Purpose |
|---|---|
| `configure.sh` / `configure.ps1` | Prompt user for custom config → save to `.skill-config.json` |
| `install.sh` / `install.ps1` | Read config (or defaults) → process template → install to harness |
| `SKILL.md.template` | Generic skill template with `{{PLACEHOLDERS}}` |
| `.skill-config.json` | User config (created by configure, gitignored) |
| `harness/*/SKILL.md` | Pre-built harness-specific skills (optional) |
| `open-autoagent-ollama-setup.md` | Legacy canonical skill (fallback only) |
