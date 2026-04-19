# Quick Camera Profile (QCP)

Desktop app for one-click camera profile generation (ICC for Capture One and DCP for Lightroom/ACR).

This project is licensed under GPL-3.0 and is intended for commercial distribution with source-code compliance.

## Commercial Distribution

You may sell QCP installers (for example via Lemon Squeezy) under GPL-3.0 terms.

When distributing binaries, you must provide:
1. GPL-3.0 license text
2. Copyright/attribution notices
3. Corresponding source code for the exact release

## Source Code Links (Set Before Release)

- Project repository: `https://github.com/<your-account>/quick-camera-profile`
- Exact release source archive: `https://github.com/<your-account>/quick-camera-profile/archive/refs/tags/vX.Y.Z.zip`

## Build

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python build.py --dir
```

## Installer Build (Windows)

1. Install Inno Setup 6
2. Build app bundle (`dist\\QuickProfile\\`)
3. Compile `installer\\QuickCameraProfile.iss`

## Included Legal Files

- `LICENSE`
- `THIRD_PARTY_NOTICES.txt`
- `SOURCE_CODE_OFFER.txt`
- `COPYING_SOURCE.md`

## Credits

- DCamProf engine by Anders Torger and contributors
- Quick Camera Profile UI and integration by Roman Alurkoff and contributors
