$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$python = '.venv/Scripts/python.exe'
if (-not (Test-Path $python)) {
    if (Get-Command py -ErrorAction SilentlyContinue) { py -3 -m venv .venv }
    elseif (Get-Command python -ErrorAction SilentlyContinue) { python -m venv .venv }
    else { throw 'Python 3.10+ is required. Install Python and retry.' }
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the virtual environment.' }
}
& $python -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)'
if ($LASTEXITCODE -ne 0) { throw 'FlowCode Studio requires Python 3.10 or newer.' }
$needsInstall = -not (Test-Path '.venv/.flowcode-deps-version')
if (-not $needsInstall) {
    $needsInstall = (Get-Content '.venv/.flowcode-deps-version' -Raw).Trim() -ne '0.25.0'
}
try { & $python -c 'import eel, jedi, black, websocket' 2>$null; if ($LASTEXITCODE -ne 0) { $needsInstall = $true } }
catch { $needsInstall = $true }
if ($needsInstall) {
    & $python -m pip install --disable-pip-version-check -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
    Set-Content -NoNewline '.venv/.flowcode-deps-version' '0.25.0'
}
& $python main.py
if ($LASTEXITCODE -ne 0) { throw 'FlowCode Studio exited with an error.' }
