$python = ".\.venv\Scripts\python.exe"

& $python -m PyInstaller domains_scorer.spec

if ($LASTEXITCODE -ne 0) {
    Write-Error "PyInstaller build failed."
    exit $LASTEXITCODE
}

Write-Host "Build complete: dist\domains_scorer.exe"
