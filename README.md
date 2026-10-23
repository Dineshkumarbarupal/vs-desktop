# VS Desktop — FlowCode Studio

This repository is initialized for the FlowCode Studio desktop IDE project.

## Current repository status

The current GitHub commit contains this README only. The full source ZIP has **not** been uploaded into this repository yet. The connected GitHub operation available in this session can commit UTF-8 text, but it does not provide a direct binary-file upload from the local runtime; the runtime also cannot reach GitHub using ordinary `git push`.

## Verified source bundle available from this conversation

- **Release:** FlowCode Studio v0.34.0
- **SHA-256:** `18b3d1b01195552dbc625ee2e1cae4d2993f5cefd02abcc0fa65f7a5cc6302cc`
- [Download the source ZIP from this conversation](sandbox:/mnt/data/flowcode-studio-v0.34.0.zip)

The archive contains the complete project directory, Python/Eel backend, HTML/CSS/JavaScript frontend, tests, sample workspace, requirements, launch scripts and documentation.

## Publish the source to this repository from a machine with Git access

1. Download and extract the ZIP.
2. Open PowerShell in the extracted `flowcode-studio` directory.
3. Run:

```powershell
git init
git branch -M main
git remote add origin https://github.com/Dineshkumarbarupal/vs-desktop.git
git add .
git commit -m "Add FlowCode Studio v0.34.0 source"
git push -u origin main
```

If `origin` already exists, skip `git remote add origin`. GitHub authentication is required.

## Run on Windows

1. Extract the ZIP.
2. Double-click `run.bat`, or run `.`\`launch.ps1` from PowerShell.

Python 3.10+ is required. Node.js is optional for JavaScript/TypeScript tooling.

## Verification note

The v0.34.0 source bundle passed its recorded backend suite and mocked-Eel UI smoke checks. Native Windows desktop launch has not been independently verified in this environment.

This is an original VS Code-inspired editor; it does not include or redistribute Microsoft Visual Studio Code.
