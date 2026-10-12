$ErrorActionPreference = 'Stop'
$projectDir = $PSScriptRoot
$projectPython = Join-Path $projectDir '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $projectPython)) {
    throw 'Hiányzik a Python-környezet. Kövesd a README.md telepítési lépéseit.'
}
Push-Location -LiteralPath $projectDir
try {
    & $projectPython -m factory_yard @args
} finally {
    Pop-Location
}
