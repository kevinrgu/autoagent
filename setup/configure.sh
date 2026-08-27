#!/usr/bin/env bash
# configure.sh — Configure setup options for the open-autoagent-ollama-setup skill.
#
# Interactively collects repository, model, and hardware information,
# then generates a customized SKILL.md template. Run this BEFORE install.sh
# if you want to use a custom repo, model, or hardware profile.
#
# Output: setup/.skill-config.json (gitignored, used by install.sh)
#
# Usage:  bash setup/configure.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="$SCRIPT_DIR/.skill-config.json"

# Defaults (sensible for the original use case)
MAIN_REPO_DEFAULT="https://github.com/Oncorporation/open-autoagent"
DOMAIN_REPO_DEFAULT="https://github.com/Oncorporation/secure-torrent-mcp-agent"
DOMAIN_BRANCH_DEFAULT="domain/secure-torrent"
LLM_PROVIDER_DEFAULT="ollama"
MODEL_DEFAULT="qwen3.8:27b-mtp-q8_0"
OLLAMA_ENDPOINT_DEFAULT="http://127.0.0.1:11434"
HARDWARE_DEFAULT="AMD Ryzen AI Max+ 395 64GB-64GB"

echo ""
echo "  open-autoagent-ollama-setup  --  Configuration"
echo "  -----------------------------------------------"
echo ""
echo "  Press Enter to accept defaults (shown in brackets)"
echo ""

# Helper: read with default
read_with_default() {
    local prompt="$1"
    local default="$2"
    read -rp "  $prompt [$default]: " input
    echo "${input:-$default}"
}

# Gather config
MAIN_REPO=$(read_with_default "Main repository URL" "$MAIN_REPO_DEFAULT")
DOMAIN_REPO=$(read_with_default "Domain/catalog repository URL" "$DOMAIN_REPO_DEFAULT")
DOMAIN_BRANCH=$(read_with_default "Domain branch name" "$DOMAIN_BRANCH_DEFAULT")
LLM_PROVIDER=$(read_with_default "LLM provider (ollama, openai, anthropic, azure)" "$LLM_PROVIDER_DEFAULT")
MODEL=$(read_with_default "Model name" "$MODEL_DEFAULT")

if [[ "$LLM_PROVIDER" == "ollama" ]]; then
    OLLAMA_ENDPOINT=$(read_with_default "Ollama endpoint" "$OLLAMA_ENDPOINT_DEFAULT")
fi

HARDWARE=$(read_with_default "Hardware profile (optional, for notes only)" "$HARDWARE_DEFAULT")

# Build JSON
cat > "$CONFIG_FILE" << EOF
{
  "mainRepo": "$MAIN_REPO",
  "domainRepo": "$DOMAIN_REPO",
  "domainBranch": "$DOMAIN_BRANCH",
  "llmProvider": "$LLM_PROVIDER",
  "model": "$MODEL",
  "ollamaEndpoint": "${OLLAMA_ENDPOINT:-}",
  "hardware": "$HARDWARE"
}
EOF

echo ""
echo "  ✓ Saved to: $CONFIG_FILE"
echo ""
echo "  Next: bash setup/install.sh"
echo ""
