# ──────────────────────────────────────────────────────────────────────────────
# JobRadar  --  Windows Smart Setup (PowerShell)
# Checks what exists before downloading anything. Safe to re-run.
# Run: .\setup.ps1   OR   powershell -ExecutionPolicy Bypass -File setup.ps1
# ──────────────────────────────────────────────────────────────────────────────
$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvDir   = Join-Path $ScriptDir ".venv"
$BinDir    = Join-Path $ScriptDir "bin"
$ModelDir  = Join-Path $ScriptDir "models"
$ModelFile = Join-Path $ModelDir "qwen3-1.7b-q4_k_m.gguf"

function Ok   { param($msg) Write-Host "  [OK] $msg" -ForegroundColor Green }
function Skip { param($msg) Write-Host "  [>>] $msg (already exists)" -ForegroundColor Yellow }
function Warn { param($msg) Write-Host "  [!!] $msg" -ForegroundColor Yellow }
function Fail { param($msg) Write-Host "  [XX] $msg" -ForegroundColor Red }
function Info { param($msg) Write-Host $msg -ForegroundColor Cyan }

# ── Step 1: Check Python ────────────────────────────────────────────────────
Info "`n-- Step 1/5: Python --"
$pyCmd = $null

# Try py launcher first (standard Windows Python)
if (Get-Command "py" -ErrorAction SilentlyContinue) {
    try {
        $ver = & py -c "import sys; print(sys.version_info.major)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $ver -eq "3") {
            $pyCmd = "py"
        }
    } catch {
        # py launcher exists but didn't work, try python
    }
}

if (-not $pyCmd -and (Get-Command "python" -ErrorAction SilentlyContinue)) {
    try {
        $ver = & python -c "import sys; print(sys.version_info.major)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $ver -eq "3") {
            $pyCmd = "python"
        }
    } catch {
        # python exists but not Python 3
    }
}

if ($pyCmd) {
    try {
        $pyVer = & $pyCmd -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
        if ($LASTEXITCODE -ne 0 -or -not $pyVer) {
            Fail "Could not determine Python version"
            exit 1
        }
        $parts = $pyVer -split "\."
        if ([int]$parts[0] -gt 3 -or ([int]$parts[0] -eq 3 -and [int]$parts[1] -ge 9)) {
            Ok "Python $pyVer found"
        } else {
            Fail "Python 3.9+ required (found $pyVer)"
            exit 1
        }
    } catch {
        Fail "Could not determine Python version: $_"
        exit 1
    }
} else {
    Fail "Python not found. Install from https://python.org (check 'Add to PATH')"
    exit 1
}

# ── Step 2: Python venv + pip packages ──────────────────────────────────────
Info "`n-- Step 2/5: Python packages --"

if (Test-Path $VenvDir) {
    Skip "Virtual environment at $VenvDir"
} else {
    Info "  Creating virtual environment..."
    & $pyCmd -m venv $VenvDir
    if ($LASTEXITCODE -ne 0) {
        Fail "Failed to create virtual environment"
        exit 1
    }
    Ok "Virtual environment created"
}

# ── Activate venv (dot-source, NOT call operator) ──────────────────────────
$activateScript = Join-Path $VenvDir "Scripts\Activate.ps1"
if (-not (Test-Path $activateScript)) {
    Fail "Activation script not found at $activateScript"
    exit 1
}
. $activateScript

# Use venv python/pip directly via full paths to avoid PATH resolution issues
$venvPython = Join-Path $VenvDir "Scripts\python.exe"
$venvPip    = Join-Path $VenvDir "Scripts\pip.exe"

if (-not (Test-Path $venvPython)) {
    Fail "venv python not found at $venvPython"
    exit 1
}

# Check core packages  --  use the venv's python directly
$corePkgs = @("rich", "yaml", "requests", "bs4", "pytest")
$missingCore = @()
foreach ($pkg in $corePkgs) {
    $modName = if ($pkg -eq "bs4") { "bs4" } elseif ($pkg -eq "yaml") { "yaml" } else { $pkg }
    try {
        & $venvPython -c "import $modName" 2>$null
        if ($LASTEXITCODE -ne 0) { $missingCore += $pkg }
    } catch {
        $missingCore += $pkg
    }
}

if ($missingCore.Count -eq 0) {
    Skip "Core Python packages already installed"
} else {
    Info "  Installing $($missingCore.Count) missing package(s)..."
    & $venvPip install -q -r (Join-Path $ScriptDir "requirements.txt")
    if ($LASTEXITCODE -ne 0) {
        Fail "Failed to install core packages"
        exit 1
    }
    Ok "Core packages installed"
}

# Dashboard packages
$dashPkgs = @("fastapi", "uvicorn", "pydantic")
$missingDash = @()
foreach ($pkg in $dashPkgs) {
    try {
        & $venvPython -c "import $pkg" 2>$null
        if ($LASTEXITCODE -ne 0) { $missingDash += $pkg }
    } catch {
        $missingDash += $pkg
    }
}

if ($missingDash.Count -eq 0) {
    Skip "Dashboard packages already installed"
} else {
    Info "  Installing $($missingDash.Count) missing dashboard package(s)..."
    & $venvPip install -q -r (Join-Path $ScriptDir "dashboard\requirements.txt")
    if ($LASTEXITCODE -ne 0) {
        Fail "Failed to install dashboard packages"
        exit 1
    }
    Ok "Dashboard packages installed"
}

# ── Step 3: LLM Backend (Ollama vs llama.cpp) ──────────────────────────────
Info "`n-- Step 3/5: LLM Backend --"
Write-Host ""
Write-Host "  Which LLM backend do you want?" -ForegroundColor Cyan
Write-Host "    1) Ollama (recommended)  --  easy setup, auto-manages models" -ForegroundColor White
Write-Host "    2) llama.cpp (faster)     --  raw performance, manual model download" -ForegroundColor White
Write-Host ""

$llmChoice = ""
while ($llmChoice -ne "1" -and $llmChoice -ne "2") {
    $llmChoice = Read-Host "  Enter 1 or 2 [1]"
    if (-not $llmChoice) { $llmChoice = "1" }
    if ($llmChoice -ne "1" -and $llmChoice -ne "2") {
        Warn "Please enter 1 or 2"
    }
}

$useOllama = ($llmChoice -eq "1")
$llmBackend = if ($useOllama) { "ollama" } else { "llamacpp" }

if ($useOllama) {
    # ── Ollama path ────────────────────────────────────────────────────────
    Info "  Setting up Ollama..."

    # Check if Ollama is installed
    $ollamaFound = $false
    if (Get-Command "ollama" -ErrorAction SilentlyContinue) {
        $ollamaFound = $true
        try {
            $ollamaVer = & ollama --version 2>$null
            Ok "Ollama installed: $ollamaVer"
        } catch {
            Ok "Ollama found"
        }
    }

    if (-not $ollamaFound) {
        # Check common install locations
        $ollamaCandidates = @(
            (Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe"),
            (Join-Path $env:ProgramFiles "Ollama\ollama.exe")
        )
        foreach ($c in $ollamaCandidates) {
            if (Test-Path $c) {
                $ollamaFound = $true
                Ok "Ollama found at $c"
                break
            }
        }
    }

    if (-not $ollamaFound) {
        Warn "Ollama not found. Install it from: https://ollama.com"
        Warn "  Download Ollama for Windows, then re-run this script."
        Warn ""
        Warn "  Falling back to llama.cpp instead..."
        $useOllama = $false
        $llmBackend = "llamacpp"
    }
}

if ($useOllama) {
    # Pull the model into Ollama
    $ollamaModel = "qwen3:1.7b"
    Info "  Checking Ollama model ($ollamaModel)..."

    # Check if model is already pulled
    $modelList = ""
    try {
        $modelList = & ollama list 2>$null
    } catch {
        # ollama list failed  --  model probably not pulled yet
    }

    if ($modelList -match "qwen3:1\.7b") {
        Skip "Ollama model $ollamaModel already available"
    } else {
        Info "  Pulling $ollamaModel (~1.1 GB)  --  this may take a minute..."
        & ollama pull $ollamaModel
        if ($LASTEXITCODE -ne 0) {
            Fail "Failed to pull Ollama model"
            Warn "  Try manually: ollama pull $ollamaModel"
            exit 1
        } else {
            Ok "Ollama model $ollamaModel ready"
        }
    }

    # Check if Ollama server is already running
    $ollamaRunning = $false
    try {
        $response = Invoke-WebRequest -Uri "http://localhost:11434/api/tags" -TimeoutSec 2 -UseBasicParsing
        if ($response.StatusCode -eq 200) {
            $ollamaRunning = $true
        }
    } catch {
        # not running
    }

    if (-not $ollamaRunning) {
        Info "  Starting Ollama server..."
        # Start Ollama serve in background
        Start-Process -FilePath "ollama" -ArgumentList "serve" -WindowStyle Hidden -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 3
        try {
            $response = Invoke-WebRequest -Uri "http://localhost:11434/api/tags" -TimeoutSec 5 -UseBasicParsing
            if ($response.StatusCode -eq 200) {
                Ok "Ollama server started on http://localhost:11434"
            }
        } catch {
            Warn "Ollama server started  --  may need a moment to be ready"
        }
    } else {
        Ok "Ollama server already running on http://localhost:11434"
    }
} else {
    # ── llama.cpp path ─────────────────────────────────────────────────────
    if (-not (Test-Path $BinDir)) { New-Item -ItemType Directory -Path $BinDir | Out-Null }

    $llamaExe = Join-Path $BinDir "llama-server.exe"

    # Check common locations
    $llamaFound = $false
    $llamaBin = $null
    $candidates = @(
        $llamaExe,
        (Join-Path $env:LOCALAPPDATA "Programs\llama.cpp\llama-server.exe"),
        (Join-Path $env:ProgramFiles "llama.cpp\llama-server.exe")
    )

    foreach ($c in $candidates) {
        if (Test-Path $c) {
            $llamaBin = $c
            $llamaFound = $true
            Skip "llama-server found at $c"
            break
        }
    }

    if (-not $llamaFound) {
        if (Get-Command "llama-server" -ErrorAction SilentlyContinue) {
            $llamaBin = (Get-Command "llama-server").Source
            $llamaFound = $true
            Skip "llama-server found at $llamaBin"
        }
    }

    if (-not $llamaFound) {
        Info "  Downloading llama.cpp for Windows x64..."

        # Get latest release tag
        $latestTag = ""
        try {
            $release = Invoke-RestMethod -Uri "https://api.github.com/repos/ggml-org/llama.cpp/releases/latest" -TimeoutSec 10
            $latestTag = $release.tag_name
        } catch {
            try {
                $headers = @{ "User-Agent" = "JobRadar-Setup" }
                $response = Invoke-WebRequest -Uri "https://api.github.com/repos/ggml-org/llama.cpp/releases/latest" -Headers $headers -TimeoutSec 10 -UseBasicParsing
                $json = $response.Content | ConvertFrom-Json
                $latestTag = $json.tag_name
            } catch {
                Warn "Could not reach GitHub API: $($_.Exception.Message)"
            }
        }

        if ($latestTag) {
            Info "  Latest release: $latestTag"
            $downloaded = $false

            foreach ($pattern in @("llama-server-windows-x64.zip", "llama-win-x64.zip")) {
                $dlUrl = "https://github.com/ggml-org/llama.cpp/releases/download/$latestTag/$pattern"
                $tmpZip = Join-Path $env:TEMP "llama.zip"
                try {
                    Invoke-WebRequest -Uri $dlUrl -OutFile $tmpZip -TimeoutSec 120 -UseBasicParsing
                    $downloaded = $true
                    break
                } catch {
                    # try next pattern
                }
            }

            if ($downloaded) {
                Info "  Extracting..."
                $tmpExtract = Join-Path $env:TEMP "llama_extract"
                if (Test-Path $tmpExtract) {
                    Remove-Item $tmpExtract -Recurse -Force -ErrorAction SilentlyContinue
                }
                Expand-Archive -Path $tmpZip -DestinationPath $tmpExtract -Force
                $found = Get-ChildItem -Path $tmpExtract -Recurse -Filter "llama-server.exe" | Select-Object -First 1
                if ($found) {
                    Copy-Item $found.FullName $llamaExe -Force
                    $llamaBin = $llamaExe
                    Ok "llama-server installed at $llamaExe"
                } else {
                    Get-ChildItem -Path $tmpExtract -Recurse -Filter "llama-*.exe" | ForEach-Object {
                        Copy-Item $_.FullName $BinDir -Force
                    }
                    if (Test-Path $llamaExe) {
                        $llamaBin = $llamaExe
                        Ok "llama-server installed at $llamaExe"
                    } else {
                        Warn "Download succeeded but llama-server.exe not found in archive"
                    }
                }
                Remove-Item $tmpExtract -Recurse -Force -ErrorAction SilentlyContinue
                Remove-Item $tmpZip -Force -ErrorAction SilentlyContinue
            } else {
                Warn "Could not download llama.cpp from GitHub releases"
            }
        }

        if (-not $llamaBin -or -not (Test-Path $llamaExe)) {
            Fail "llama-server not available"
            Warn "  Install manually from: https://github.com/ggml-org/llama.cpp/releases"
            exit 1
            }
    }
}

# ── Step 4: LLM Model (only for llama.cpp) ──────────────────────────────────
if ($llmBackend -eq "llamacpp") {
    Info "`n-- Step 4/5: LLM Model --"
    if (-not (Test-Path $ModelDir)) { New-Item -ItemType Directory -Path $ModelDir | Out-Null }

    $modelExists = Get-ChildItem -Path $ModelDir -Filter "*.gguf" -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($modelExists) {
        Skip "Model found: $($modelExists.Name)"
    } else {
        Info "  Downloading qwen3-1.7b Q4_K_M (~1.1 GB)..."
        Info "  This is a small, fast model - good for CPU inference"

        $modelUrl = "https://huggingface.co/unsloth/Qwen3-1.7B-GGUF/resolve/main/Qwen3-1.7B-Q4_K_M.gguf"
        $modelPart = "$ModelFile.part"

        try {
            $wc = New-Object System.Net.WebClient
            try {
                $wc.DownloadFile($modelUrl, $modelPart)
            } finally {
                $wc.Dispose()
            }

            Move-Item $modelPart $ModelFile -Force
            $size = [math]::Round((Get-Item $ModelFile).Length / 1GB, 1)
            Ok "Model saved: $ModelFile ($size GB)"
        } catch {
            Remove-Item $modelPart -Force -ErrorAction SilentlyContinue
            Fail "Model download failed: $_"
            Warn "Download manually from:"
            Warn "  $modelUrl"
            Warn "  Save as: qwen3-1.7b-q4_k_m.gguf in $ModelDir\"
        }
    }
} else {
    # Ollama manages its own models  --  skip GGUF download
    Info "`n-- Step 4/5: LLM Model --"
    Skip "Ollama manages models automatically (no GGUF download needed)"
}

# ── Step 5: Profile config ──────────────────────────────────────────────────
Info "`n-- Step 5/5: Configuration --"
$profilePath = Join-Path $ScriptDir "profile.yaml"

if (Test-Path $profilePath) {
    Skip "profile.yaml exists"
} else {
    @"
# JobRadar Profile - edit this with your details
name: "Your Name"
title: "Software Engineer"
experience_years: 3
skills:
  - Python
  - JavaScript
  - Docker
desired_roles:
  - Backend Engineer
  - Full Stack Developer
salary_min: 80000
salary_max: 150000
location_preference: "Remote"
remote_ok: true
industries:
  - Technology
  - SaaS
"@ | Out-File -FilePath $profilePath -Encoding utf8
    Warn "Created default profile.yaml - edit it with your real details!"
}

$companiesPath = Join-Path $ScriptDir "companies.yaml"
if (Test-Path $companiesPath) {
    Skip "companies.yaml exists"
} else {
    Warn "companies.yaml not found - using defaults in jobradar/sources/"
}

# ── Done ────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  JobRadar is ready!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Quick start:"
Write-Host "    cd $ScriptDir"
Write-Host "    . .venv\Scripts\Activate.ps1"
Write-Host ""
Write-Host "  CLI search (no AI - fast):"
Write-Host "    python -m jobradar -q `"python developer`" --no-ai"
Write-Host ""
Write-Host "  CLI search (with AI scoring):"
if ($useOllama) {
    Write-Host "    # Make sure Ollama is running: ollama serve"
    Write-Host "    python -m jobradar -q `"python developer`" -p profile.yaml"
} else {
    Write-Host "    # Start llama-server first:"
    Write-Host "    $llamaBin --model $ModelFile --port 8080 --host 0.0.0.0"
    Write-Host "    python -m jobradar -q `"python developer`" -p profile.yaml"
}
Write-Host ""
Write-Host "  Web dashboard:"
Write-Host "    Set-Location dashboard; python -m uvicorn app:app --port 3000"
Write-Host "    Open http://localhost:3000"
Write-Host ""
