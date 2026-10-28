# Local Agent sidecar package

This folder is the Tauri resource staging location for the platform-specific
`yingxu-local-agent` PyInstaller onedir package. Build the package for the same
OS and architecture as the desktop host, then place the complete
`yingxu-local-agent/` directory here before bundling Tauri. Keep the executable,
`_internal/`, and `sidecar-manifest.json` together. The generated package is
ignored by Git because it is platform-specific and large; the source and build
instructions belong in `infra/desktop_agent/`.

Without a staged package, the desktop app remains usable and reports the local
Agent as unavailable. Release packaging must stage and verify the sidecar
before producing the installer.
