$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$Python = Join-Path $ScriptDir ".venv\Scripts\python.exe"

if (-not (Test-Path $Python)) {
    Write-Error "Python virtual environment not found at $Python"
    Write-Host "Create it first with: py -3.12 -m venv .venv"
    Read-Host "Press Enter to exit"
    exit 1
}

Set-Location $ScriptDir
& $Python app_cli.py
$Status = $LASTEXITCODE

if ($Status -ne 0) {
    Read-Host "Application failed. Press Enter to exit"
}

exit $Status
