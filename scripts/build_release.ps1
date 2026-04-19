Param(
    [string]$Version = "1.0.2"
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
$iss = $iss -replace '#define MyAppVersion ".*"', ('#define MyAppVersion "{0}"' -f $Version)
$iss = $iss -replace 'refs/tags/v[0-9]+\.[0-9]+\.[0-9]+\.zip', "refs/tags/v$Version.zip"
Set-Content -Path $issPath -Value $iss

# Keep source notice URL aligned with this exact binary release.
$sourceOfferPath = "SOURCE_CODE_OFFER.txt"
$sourceOffer = Get-Content $sourceOfferPath -Raw
$sourceOffer = $sourceOffer -replace 'refs/tags/v[0-9]+\.[0-9]+\.[0-9]+\.zip', "refs/tags/v$Version.zip"
Set-Content -Path $sourceOfferPath -Value $sourceOffer

# Create source zip for same version
if (-not (Test-Path "release")) { New-Item -ItemType Directory -Path "release" | Out-Null }
$srcZip = "release\quick-camera-profile-v$Version-source.zip"
if (Test-Path $srcZip) { Remove-Item $srcZip -Force }

if (Get-Command git -ErrorAction SilentlyContinue) {
    # Prefer tracked-source archive to avoid zipping build artifacts.
    git archive --format zip --output $srcZip HEAD
} else {
    # Fallback: zip workspace excluding volatile build/release dirs.
    $paths = Get-ChildItem -Force | Where-Object {
        $_.Name -notin @("release", "dist", "build", ".venv", "__pycache__", ".git")
    } | Select-Object -ExpandProperty FullName
    Compress-Archive -Path $paths -DestinationPath $srcZip -Force
}

Write-Host "Build complete."
Write-Host "Next: compile installer with Inno Setup:"
Write-Host "  iscc installer\\QuickCameraProfile.iss"
