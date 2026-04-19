# Source Distribution Policy

For each binary release tag `vX.Y.Z`, publish one matching source release.

Required artifacts:
1. Binary installer (`QuickCameraProfile-Setup-vX.Y.Z.exe`)
2. Source zip for exact tag (`quick-camera-profile-vX.Y.Z-source.zip`)

Release checklist:
1. Tag commit in git (`vX.Y.Z`)
2. Build binaries from tagged commit only
3. Publish source archive from same tag
4. Update `SOURCE_CODE_OFFER.txt` with exact URLs
5. Include `LICENSE`, `THIRD_PARTY_NOTICES.txt`, and `SOURCE_CODE_OFFER.txt` in installer
