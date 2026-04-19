# Lemon Squeezy Launch Notes (GPL-Compliant)

## Product Setup

Create one product in Lemon Squeezy:
- Name: Quick Camera Profile
- Type: Software
- Delivery file: QuickCameraProfile-Setup-vX.Y.Z.exe

## Add Compliance Text (Product Description or License Section)

This software includes components licensed under GNU GPL v3.0.
A copy of the license is included in the installer.
Corresponding source code for this exact release is available at:
https://github.com/OneSlips/quick-camera-profile/archive/refs/tags/vX.Y.Z.zip
Project repository:
https://github.com/OneSlips/quick-camera-profile

## Release Workflow

1. Update version in scripts/build_release.ps1
2. Build app and source zip
3. Compile installer with Inno Setup
4. Create git tag vX.Y.Z
5. Publish source zip and installer
6. Update SOURCE_CODE_OFFER.txt URLs for vX.Y.Z
7. Upload installer to Lemon Squeezy

## Files that must ship with installer

- LICENSE
- THIRD_PARTY_NOTICES.txt
- SOURCE_CODE_OFFER.txt

## Customer-facing notes

- Paid installer purchase is allowed under GPL
- Customers still receive GPL rights and source-code access
- Selling support/services/updates is recommended for recurring revenue
