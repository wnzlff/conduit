# Builds a self-contained Windows build of Conduit into dist\Conduit\ using PyInstaller.
# Usage:  .\scripts\build_exe.ps1  [-Zip]
param(
    [switch]$Zip
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not (Test-Path ".venv")) {
    Write-Host "Creating virtual environment..."
    python -m venv .venv
}
$py = Join-Path $root ".venv\Scripts\python.exe"

& $py -m pip install --upgrade pip
& $py -m pip install -e ".[dev]"

if (Test-Path "build") { Remove-Item -Recurse -Force "build" }
if (Test-Path "dist\Conduit") { Remove-Item -Recurse -Force "dist\Conduit" }

& $py -m PyInstaller --noconfirm --clean conduit.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

$version = (& $py -c "import conduit; print(conduit.__version__)").Trim()
Write-Host "Built dist\Conduit\Conduit.exe (version $version)"

if ($Zip) {
    $archive = "dist\Conduit-$version-windows-x64.zip"
    if (Test-Path $archive) { Remove-Item -Force $archive }
    Compress-Archive -Path "dist\Conduit\*" -DestinationPath $archive
    Write-Host "Archive: $archive"
}
