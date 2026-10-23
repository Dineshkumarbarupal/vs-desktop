# VS Desktop — FlowCode Studio

This repository contains the verified FlowCode Studio desktop IDE source bundle.

## Source bundle

- **Release:** FlowCode Studio v0.34.0
- **Archive:** `FlowCode-Studio-v0.34.0.zip`
- **SHA-256:** `18b3d1b01195552dbc625ee2e1cae4d2993f5cefd02abcc0fa65f7a5cc6302cc`

The ZIP contains the complete project directory, including the Python/Eel backend, HTML/CSS/JavaScript frontend, tests, sample workspace, requirements, launch scripts and documentation.

## Run on Windows

1. Download and extract `FlowCode-Studio-v0.34.0.zip`.
2. Open the extracted `flowcode-studio` folder.
3. Double-click `run.bat`, or run `.`\`launch.ps1` from PowerShell.

Python 3.10+ is required. Node.js is optional for JavaScript/TypeScript tooling.

## Important verification note

The v0.34.0 source bundle passed its recorded backend suite and mocked-Eel UI smoke checks. Native Windows desktop launch has not been independently verified in this environment.

This is an original VS Code-inspired editor; it does not include or redistribute Microsoft Visual Studio Code.
