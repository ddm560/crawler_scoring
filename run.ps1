$python = ".\.venv\Scripts\python.exe"

function Read-WithDefault {
    param(
        [string]$Prompt,
        [string]$DefaultValue
    )

    $value = Read-Host "$Prompt [$DefaultValue]"
    if ([string]::IsNullOrWhiteSpace($value)) {
        return $DefaultValue
    }
    return $value.Trim()
}

$inputFile = Read-WithDefault -Prompt "Input domains file" -DefaultValue ".\input\domains.txt"
$featuresFile = Read-WithDefault -Prompt "Features output JSONL" -DefaultValue "features.jsonl"
$concurrency = Read-WithDefault -Prompt "Concurrency" -DefaultValue "60"
$pages = Read-WithDefault -Prompt "Pages per domain" -DefaultValue "6"
$timeout = Read-WithDefault -Prompt "Timeout (seconds)" -DefaultValue "10"
$resumeAnswer = Read-WithDefault -Prompt "Resume from existing features file? (Y/N)" -DefaultValue "Y"

$args = @(
    "extract_features.py",
    "--input", $inputFile,
    "--out-jsonl", $featuresFile,
    "--concurrency", $concurrency,
    "--pages", $pages,
    "--timeout", $timeout
)

if ($resumeAnswer -match "^(y|yes)$") {
    $args += "--resume"
}

& $python @args

if ($LASTEXITCODE -eq 0) {
    & $python finalize_scores.py --features-jsonl $featuresFile
}
else {
    Write-Error "Feature extraction failed. Scoring step was skipped."
    exit $LASTEXITCODE
}
