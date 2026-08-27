#Requires -Version 5.1
<#
.SYNOPSIS
    Installs the open-autoagent-ollama-setup skill for your AI harness.
.DESCRIPTION
    Prompts for harness selection and copies the pre-built SKILL.md to the
    correct location for that harness. Additional harnesses can be added by
    creating a subfolder under setup\harness\ and updating the switch below.
.EXAMPLE
    .\setup\install.ps1
#>

$ErrorActionPreference = "Stop"

$ScriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot   = Split-Path -Parent $ScriptDir
$HarnessDir = Join-Path $ScriptDir "harness"
$SkillName  = "open-autoagent-ollama-setup"
$UserHome   = $env:USERPROFILE
$ConfigFile = Join-Path $ScriptDir ".skill-config.json"

# Load config if it exists
if (Test-Path $ConfigFile) {
    $config = Get-Content -Raw $ConfigFile | ConvertFrom-Json
    $mainRepo = $config.mainRepo
    $model = $config.model
    $ollamaEndpoint = $config.ollamaEndpoint
    $hardware = $config.hardware
    $domainRepo = $config.domainRepo
    $domainBranch = $config.domainBranch
} else {
    # Defaults
    $mainRepo = "https://github.com/Oncorporation/open-autoagent"
    $model = "qwen3.8:27b-mtp-q8_0"
    $ollamaEndpoint = "http://127.0.0.1:11434"
    $hardware = "AMD Ryzen AI Max+ 395 64GB-64GB"
    $domainRepo = "https://github.com/Oncorporation/secure-torrent-mcp-agent"
    $domainBranch = "domain/secure-torrent"
}

# -- menu ----------------------------------------------------------------------
Write-Host ""
Write-Host "  open-autoagent-ollama-setup  --  Skill Installer" -ForegroundColor Cyan
Write-Host "  --------------------------------------------------" -ForegroundColor DarkGray
Write-Host ""
Write-Host "  Select your AI harness:" -ForegroundColor White
Write-Host "    1) Hermes"
Write-Host "    2) Claude Code         (project-level: <repo>\.claude\)"
Write-Host "    3) Claude Desktop      (user-level: ~\.claude\)"
Write-Host "    4) Cursor              (project-level: <repo>\.cursor\)"
Write-Host "    5) Grok                (user-level: ~\.grok\)"
Write-Host "    6) VS Code + Copilot   (project-level: <repo>\.vscode\)"
Write-Host "    7) Visual Studio       (project-level: <repo>\.github\)"
Write-Host ""
Write-Host "  (Additional harnesses: add a folder under setup\harness\ and re-run)" -ForegroundColor DarkGray
Write-Host ""

$choice = ""
while ($choice -notmatch '^[1-7]$') {
    $choice = (Read-Host "  Enter number [1-7]").Trim()
}

# -- resolve harness name and install path -------------------------------------
switch ($choice) {
    "1" {
        $Harness   = "hermes"
        $TargetDir = Join-Path $UserHome ".hermes\skills\mcp-install\$SkillName"
    }
    "2" {
        $Harness   = "claude-code"
        $TargetDir = Join-Path $RepoRoot ".claude\skills\$SkillName"
    }
    "3" {
        $Harness   = "claude-desktop"
        $TargetDir = Join-Path $UserHome ".claude\skills\$SkillName"
    }
    "4" {
        $Harness   = "cursor"
        $TargetDir = Join-Path $RepoRoot ".cursor\skills\$SkillName"
    }
    "5" {
        $Harness   = "grok"
        $TargetDir = Join-Path $UserHome ".grok\skills\$SkillName"
    }
    "6" {
        $Harness   = "vscode"
        $TargetDir = Join-Path $RepoRoot ".vscode\skills\$SkillName"
    }
    "7" {
        $Harness   = "visual-studio"
        $TargetDir = Join-Path $RepoRoot ".github\skills\$SkillName"
    }
}

# -- locate and process source ------------------------------------------------
$SourceFile = Join-Path $HarnessDir "$Harness\SKILL.md"

# Fall back to template-based generation if no harness-specific file exists
if (-not (Test-Path $SourceFile)) {
    $Template = Join-Path $ScriptDir "SKILL.md.template"
    if (-not (Test-Path $Template)) {
        # Further fallback: use canonical if template missing
        $SourceFile = Join-Path $ScriptDir "$SkillName.md"
    } else {
        # Generate from template on-the-fly
        $TempFile = Join-Path $env:TEMP "skill-$Harness-$(Get-Random).md"
        $TemplateContent = Get-Content -Raw $Template
        $TemplateContent = $TemplateContent -replace '\{\{MAIN_REPO\}\}', $mainRepo
        $TemplateContent = $TemplateContent -replace '\{\{MODEL\}\}', $model
        $TemplateContent = $TemplateContent -replace '\{\{OLLAMA_ENDPOINT\}\}', $ollamaEndpoint
        $TemplateContent = $TemplateContent -replace '\{\{HARDWARE\}\}', $hardware
        $TemplateContent = $TemplateContent -replace '\{\{DOMAIN_REPO\}\}', $domainRepo
        $TemplateContent = $TemplateContent -replace '\{\{DOMAIN_BRANCH\}\}', $domainBranch
        Set-Content -Path $TempFile -Value $TemplateContent
        $SourceFile = $TempFile
    }
}

if (-not (Test-Path $SourceFile)) {
    Write-Error "Source SKILL.md not found at '$SourceFile'."
    exit 1
}

# -- confirm -------------------------------------------------------------------
Write-Host ""
Write-Host "  Harness : $Harness" -ForegroundColor Yellow
Write-Host "  Source  : $SourceFile" -ForegroundColor DarkGray
Write-Host "  Target  : $TargetDir\SKILL.md" -ForegroundColor Yellow
Write-Host "  Model   : $model" -ForegroundColor DarkGray
Write-Host "  Repo    : $mainRepo" -ForegroundColor DarkGray
Write-Host ""

$confirm = (Read-Host "  Proceed? [Y/n]").Trim()
if ($confirm -match '^[Nn]') {
    Write-Host "  Aborted." -ForegroundColor Red
    exit 0
}

# -- install -------------------------------------------------------------------
New-Item -ItemType Directory -Force -Path $TargetDir | Out-Null
Copy-Item -Path $SourceFile -Destination (Join-Path $TargetDir "SKILL.md") -Force

# Clean up temp file if created
if ($SourceFile -match '\\Temp\\') {
    Remove-Item -Path $SourceFile -Force
}

Write-Host ""
Write-Host "  OK Installed: $TargetDir\SKILL.md" -ForegroundColor Green
Write-Host ""

# -- trigger instructions ------------------------------------------------------
Write-Host "  Next: open $Harness and run the skill:" -ForegroundColor Cyan
switch ($Harness) {
    "hermes"         { Write-Host "    Start a new session (or /reset), then paste:" ; Write-Host "      run open-autoagent-ollama-setup" }
    "claude-code"    { Write-Host "    /skill open-autoagent-ollama-setup" }
    "claude-desktop" { Write-Host "    run the skill named open-autoagent-ollama-setup" }
    "cursor"         { Write-Host "    @open-autoagent-ollama-setup  in the Cursor chat" }
    "grok"           { Write-Host "    run open-autoagent-ollama-setup" }
    "vscode"         {
        Write-Host "    In Copilot Chat (Ctrl+Shift+I), attach the file then ask:"
        Write-Host "      #file:.vscode\skills\$SkillName\SKILL.md"
        Write-Host "      run open-autoagent-ollama-setup"
    }
    "visual-studio"  {
        Write-Host "    In Copilot Chat (View > GitHub Copilot Chat), attach the file then ask:"
        Write-Host "      #file:.github\skills\$SkillName\SKILL.md"
        Write-Host "      run open-autoagent-ollama-setup"
    }
    default {
        throw "Unknown harness '$Harness'. Add a folder under setup\harness\ and a matching arm here."
    }
}
Write-Host ""
