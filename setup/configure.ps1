#Requires -Version 5.1
<#
.SYNOPSIS
    Configure setup options for the open-autoagent-ollama-setup skill.
.DESCRIPTION
    Interactively collects repository, model, and hardware information,
    then generates a customized SKILL.md template. Run this BEFORE install.ps1
    if you want to use a custom repo, model, or hardware profile.

    Output: setup/.skill-config.json (gitignored, used by install.ps1)
.EXAMPLE
    .\setup\configure.ps1
#>

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ConfigFile = Join-Path $ScriptDir ".skill-config.json"

# Defaults (sensible for the original use case)
$defaults = @{
    mainRepo = "https://github.com/Oncorporation/open-autoagent"
    domainRepo = "https://github.com/Oncorporation/secure-torrent-mcp-agent"
    domainBranch = "domain/secure-torrent"
    llmProvider = "ollama"
    model = "qwen3.8:27b-mtp-q8_0"
    ollamaEndpoint = "http://127.0.0.1:11434"
    hardware = "AMD Ryzen AI Max+ 395 64GB-64GB"
}

Write-Host ""
Write-Host "  open-autoagent-ollama-setup  --  Configuration" -ForegroundColor Cyan
Write-Host "  -----------------------------------------------" -ForegroundColor DarkGray
Write-Host ""
Write-Host "  Press Enter to accept defaults (shown in brackets)" -ForegroundColor DarkGray
Write-Host ""

# Main repo
$prompt = "  Main repository URL"
$default = $defaults.mainRepo
$input = (Read-Host "$prompt [$default]").Trim()
$config = @{ mainRepo = if ($input) { $input } else { $default } }

# Domain repo (optional)
$prompt = "  Domain/catalog repository URL (leave blank to skip)"
$default = $defaults.domainRepo
$input = (Read-Host "$prompt [$default]").Trim()
$config.domainRepo = if ($input) { $input } else { $default }

$prompt = "  Domain branch name"
$default = $defaults.domainBranch
$input = (Read-Host "$prompt [$default]").Trim()
$config.domainBranch = if ($input) { $input } else { $default }

# LLM provider
$prompt = "  LLM provider (ollama, openai, anthropic, azure)"
$default = $defaults.llmProvider
$input = (Read-Host "$prompt [$default]").Trim()
$config.llmProvider = if ($input) { $input } else { $default }

# Model
$prompt = "  Model name"
$default = $defaults.model
$input = (Read-Host "$prompt [$default]").Trim()
$config.model = if ($input) { $input } else { $default }

# Ollama endpoint (only if ollama provider)
if ($config.llmProvider -eq "ollama") {
    $prompt = "  Ollama endpoint"
    $default = $defaults.ollamaEndpoint
    $input = (Read-Host "$prompt [$default]").Trim()
    $config.ollamaEndpoint = if ($input) { $input } else { $default }
}

# Hardware
$prompt = "  Hardware profile (optional, for notes only)"
$default = $defaults.hardware
$input = (Read-Host "$prompt [$default]").Trim()
$config.hardware = if ($input) { $input } else { $default }

# Save config
$config | ConvertTo-Json | Out-File -Encoding UTF8 $ConfigFile
Write-Host ""
Write-Host "  OK Saved to: $ConfigFile" -ForegroundColor Green
Write-Host ""
Write-Host "  Next: run .\setup\install.ps1" -ForegroundColor Cyan
Write-Host ""
