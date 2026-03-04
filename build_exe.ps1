$python = ".\.venv\Scripts\python.exe"

& $python -m PyInstaller `
    --onefile `
    --name crawler_scoring `
    app_cli.py

if ($LASTEXITCODE -ne 0) {
    Write-Error "PyInstaller build failed."
    exit $LASTEXITCODE
}

Write-Host "Build complete: dist\crawler_scoring.exe"
