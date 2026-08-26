#!/usr/bin/env bash
# install.sh — Install open-autoagent-ollama-setup skill for your AI harness.
#
# Usage:  bash setup/install.sh
#         (or chmod +x setup/install.sh && ./setup/install.sh from repo root)
#
# Additional harnesses: add a subfolder under setup/harness/ and update the
# case block below.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
HARNESS_DIR="$SCRIPT_DIR/harness"
SKILL_NAME="open-autoagent-ollama-setup"
CONFIG_FILE="$SCRIPT_DIR/.skill-config.json"

# Load config if it exists
if [[ -f "$CONFIG_FILE" ]]; then
    MAIN_REPO=$(jq -r '.mainRepo // empty' "$CONFIG_FILE")
    MODEL=$(jq -r '.model // empty' "$CONFIG_FILE")
    OLLAMA_ENDPOINT=$(jq -r '.ollamaEndpoint // empty' "$CONFIG_FILE")
    HARDWARE=$(jq -r '.hardware // empty' "$CONFIG_FILE")
    DOMAIN_REPO=$(jq -r '.domainRepo // empty' "$CONFIG_FILE")
    DOMAIN_BRANCH=$(jq -r '.domainBranch // empty' "$CONFIG_FILE")
else
    # Defaults
    MAIN_REPO="https://github.com/Oncorporation/open-autoagent"
    MODEL="qwen3.8:27b-mtp-q8_0"
    OLLAMA_ENDPOINT="http://127.0.0.1:11434"
    HARDWARE="AMD Ryzen AI Max+ 395 64GB-64GB"
    DOMAIN_REPO="https://github.com/Oncorporation/secure-torrent-mcp-agent"
    DOMAIN_BRANCH="domain/secure-torrent"
fi

# ── menu ──────────────────────────────────────────────────────────────────────
echo ""
echo "  open-autoagent-ollama-setup  --  Skill Installer"
echo "  --------------------------------------------------"
echo ""
echo "  Select your AI harness:"
echo "    1) Hermes"
echo "    2) Claude Code         (project-level: <repo>/.claude/)"
echo "    3) Claude Desktop      (user-level: ~/.claude/)"
echo "    4) Cursor              (project-level: <repo>/.cursor/)"
echo "    5) Grok                (user-level: ~/.grok/)"
echo "    6) VS Code + Copilot   (project-level: <repo>/.vscode/)"
echo "    7) Visual Studio       (project-level: <repo>/.github/)"
echo ""
echo "  (Additional harnesses: add a folder under setup/harness/ and re-run)"
echo ""

choice=""
while [[ ! "$choice" =~ ^[1-7]$ ]]; do
    read -rp "  Enter number [1-7]: " choice
done

# ── resolve harness name and install path ─────────────────────────────────────
case "$choice" in
    1) HARNESS="hermes";         TARGET_DIR="$HOME/.hermes/skills/mcp-install/$SKILL_NAME" ;;
    2) HARNESS="claude-code";    TARGET_DIR="$REPO_ROOT/.claude/skills/$SKILL_NAME" ;;
    3) HARNESS="claude-desktop"; TARGET_DIR="$HOME/.claude/skills/$SKILL_NAME" ;;
    4) HARNESS="cursor";         TARGET_DIR="$REPO_ROOT/.cursor/skills/$SKILL_NAME" ;;
    5) HARNESS="grok";           TARGET_DIR="$HOME/.grok/skills/$SKILL_NAME" ;;
    6) HARNESS="vscode";         TARGET_DIR="$REPO_ROOT/.vscode/skills/$SKILL_NAME" ;;
    7) HARNESS="visual-studio";  TARGET_DIR="$REPO_ROOT/.github/skills/$SKILL_NAME" ;;
esac

# ── locate and process source ────────────────────────────────────────────────
TEMPLATE_FILE="$HARNESS_DIR/$HARNESS/SKILL.md"
HARNESS_SOURCE_FILE="$TEMPLATE_FILE"

# Fall back to template-based generation if no harness-specific file exists
if [[ ! -f "$HARNESS_SOURCE_FILE" ]]; then
    TEMPLATE="$SCRIPT_DIR/SKILL.md.template"
    if [[ ! -f "$TEMPLATE" ]]; then
        # Further fallback: use canonical if template missing
        HARNESS_SOURCE_FILE="$SCRIPT_DIR/$SKILL_NAME.md"
    else
        # Generate from template on-the-fly
        HARNESS_SOURCE_FILE="/tmp/$SKILL_NAME-$HARNESS-$RANDOM.md"
        sed \
            -e "s|{{MAIN_REPO}}|$MAIN_REPO|g" \
            -e "s|{{MODEL}}|$MODEL|g" \
            -e "s|{{OLLAMA_ENDPOINT}}|$OLLAMA_ENDPOINT|g" \
            -e "s|{{HARDWARE}}|$HARDWARE|g" \
            -e "s|{{DOMAIN_REPO}}|$DOMAIN_REPO|g" \
            -e "s|{{DOMAIN_BRANCH}}|$DOMAIN_BRANCH|g" \
            "$TEMPLATE" > "$HARNESS_SOURCE_FILE"
    fi
fi

if [[ ! -f "$HARNESS_SOURCE_FILE" ]]; then
    echo "ERROR: No source SKILL.md found and no template to process." >&2
    exit 1
fi

# ── confirm ───────────────────────────────────────────────────────────────────
echo ""
echo "  Harness : $HARNESS"
echo "  Source  : $HARNESS_SOURCE_FILE"
echo "  Target  : $TARGET_DIR/SKILL.md"
echo "  Model   : $MODEL"
echo "  Repo    : $MAIN_REPO"
echo ""
read -rp "  Proceed? [Y/n]: " confirm
if [[ "$confirm" =~ ^[Nn] ]]; then
    echo "  Aborted."
    exit 0
fi

# ── install ───────────────────────────────────────────────────────────────────
mkdir -p "$TARGET_DIR"
cp -f "$HARNESS_SOURCE_FILE" "$TARGET_DIR/SKILL.md"

# Clean up temp file if created
if [[ "$HARNESS_SOURCE_FILE" =~ ^/tmp/ ]]; then
    rm -f "$HARNESS_SOURCE_FILE"
fi

echo ""
echo "  ✓ Installed: $TARGET_DIR/SKILL.md"
echo ""

# ── trigger instructions ──────────────────────────────────────────────────────
echo "  Next: open $HARNESS and run the skill:"
case "$HARNESS" in
    hermes)
        echo "    Start a new session (or /reset), then paste:"
        echo "      run open-autoagent-ollama-setup"
        ;;
    claude-code)
        echo "    /skill open-autoagent-ollama-setup"
        ;;
    claude-desktop)
        echo "    run the skill named open-autoagent-ollama-setup"
        ;;
    cursor)
        echo "    @open-autoagent-ollama-setup  in the Cursor chat"
        ;;
    grok)
        echo "    run open-autoagent-ollama-setup"
        ;;
    vscode)
        echo "    In Copilot Chat (Ctrl+Shift+I), attach the file then ask:"
        echo "      #file:.vscode/skills/$SKILL_NAME/SKILL.md"
        echo "      run open-autoagent-ollama-setup"
        ;;
    visual-studio)
        echo "    In Copilot Chat (View > GitHub Copilot Chat), attach the file then ask:"
        echo "      #file:.github/skills/$SKILL_NAME/SKILL.md"
        echo "      run open-autoagent-ollama-setup"
        ;;
esac
echo ""
