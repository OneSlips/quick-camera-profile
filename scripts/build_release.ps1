Param(
    [string]$Version = "1.0.0"
)

$ErrorActionPreference = "Stop"

Write-Host "Building Quick Camera Profile release v$Version"

if (-not (Test-Path ".venv")) {
    python -m venv .venv
}

& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt pyinstaller

# Build bundled app folder
& .\.venv\Scripts\python.exe build.py --dir

# Update installer version constants
$issPath = "installer\QuickCameraProfile.iss"
$iss = Get-Content $issPath -Raw
$iss = $iss -replace '#define MyAppVersion ".*"', "#define MyAppVersion \"$Version\""
$iss = $iss -replace 'refs/tags/v[0-9]+\.[0-9]+\.[0-9]+\.zip', "refs/tags/v$Version.zip"
Set-Content -Path $issPath -Value $iss

# Create source zip for same version
if (-not (Test-Path "release")) { New-Item -ItemType Directory -Path "release" | Out-Null }
$srcZip = "release\quick-camera-profile-v$Version-source.zip"
if (Test-Path $srcZip) { Remove-Item $srcZip -Force }
Compress-Archive -Path * -DestinationPath $srcZip -Force

Write-Host "Build complete."
Write-Host "Next: compile installer with Inno Setup:"
Write-Host "  iscc installer\\QuickCameraProfile.iss"
