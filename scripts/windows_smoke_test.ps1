$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"

Set-Location $repoRoot

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw "uv is not installed or not on PATH."
}

Write-Host "Creating virtual environment..."
uv venv -p 3.11 .venv

Write-Host "Syncing dependencies..."
uv sync

Write-Host "Compiling Python files..."
$pythonFiles = Get-ChildItem -Path $repoRoot -Recurse -File -Filter *.py |
    Where-Object { $_.FullName -notmatch '[\\/]\.venv[\\/]' } |
    ForEach-Object { $_.FullName }

uv run --python $venvPython -m py_compile @pythonFiles

Write-Host "Checking CLI entrypoints..."
uv run --python $venvPython main.py --help | Out-Null
uv run --python $venvPython replay.py --help | Out-Null
uv run --python $venvPython simulate.py --help | Out-Null

Write-Host "Windows smoke test passed."
