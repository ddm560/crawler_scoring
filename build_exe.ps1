$python = ".\.venv\Scripts\python.exe"

& $python -m PyInstaller `
    --onefile `
    --name domains_scorer `
    app_cli.py

if ($LASTEXITCODE -ne 0) {
    Write-Error "PyInstaller build failed."
    exit $LASTEXITCODE
}

Write-Host "Build complete: dist\domains_scorer.exe"
