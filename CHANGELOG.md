## 0.34.0

- Add TypeScript compiler-backed Quick Fix / Code Actions for JS/TS diagnostics on the current line, available via Ctrl+. and Edit menu.
- Present compiler-proposed changes with per-action, multi-file unified-diff preview and explicit Apply/Cancel before writing.
- Require a saved active buffer; restrict changes to existing in-workspace JS/TS files, cap file/batch sizes, reject unsupported commands/new files, and revalidate SHA-256 baselines before atomic application.
- Add integration tests for missing-import fixes, preview-only behavior, active-buffer save guard and stale-preview refusal; extend Playwright smoke coverage for Quick Fix preview/apply.
- Full backend suite: 121 tests passed; mocked-Eel UI smoke passed with zero page/recovery JavaScript errors. Native Windows launch remains unverified.

## 0.33.0

- Add Python conditional breakpoints: right-click a gutter line to set/edit an expression such as `item == 3`.
- Pass validated per-line conditions to the subprocess-isolated Python `bdb` debugger; preserve expression case for variables and attributes.
- Support condition updates while a debug session is active, clearing conditions with a normal breakpoint command and keeping session/local-storage recovery in sync.
- Validate expression syntax, breakpoint-line references, one-line length limits and session-recovery payloads.
- Add integration/UI regressions proving a loop breakpoint stops only when the condition evaluates true, with zero browser/recovery errors.

## 0.32.0

- Extend per-hunk Git workflow with confirmed **Discard Hunk**, reverting only the selected unstaged hunk from the working tree.
- Refuse discard/stage actions for a file whose editor buffer has unsaved changes; require a fresh signature and confirmation before discarding.
- Add integration and UI smoke coverage for hunk discard, preserving unrelated changes, stale patch refusal, and dirty-buffer guards.

## 0.31.0

- Add per-hunk staging and unstaging for tracked text files in Source Control, keeping other changes in the same file outside the selected index operation.
- Display each hunk's range, additions/deletions and a bounded patch preview with explicit Stage Hunk / Unstage Hunk controls.
- Rebuild the patch from Git on every mutation and verify a SHA-256 hunk signature to refuse stale UI selections; reject workspace escapes, untracked files, binary/no-hunk diffs and oversized diffs.
- Add real Git integration tests for staging and reversing one selected hunk, preserving unrelated modifications, and rejecting stale signatures/invalid paths.
- Extend mocked-Eel Playwright smoke coverage for hunk preview and stage-action routing.

## 0.30.0

- Added inline Git merge conflict controls: Accept Current, Accept Incoming, Accept Both, and Next Conflict.
- Supports standard Git conflict blocks and diff3 blocks with an ancestor section.
- Keeps resolutions in the active editor buffer until the user saves; it does not stage or commit automatically.
- Playwright smoke coverage now resolves three conflict blocks using all three strategies and saves the result.

## 0.29.0

- Add an in-app workspace task editor to create, edit, and delete `.vscode/tasks.json` entries without hand-editing JSON.
- Expose supported task working directory and environment values in the editor; Run and Debug task rows now offer explicit Run and Edit actions.
- Validate labels, task type, command/argument bounds, environment variable names/values, workspace-contained working directories, symlink paths, malformed JSON, and task-count/file-size limits.
- Write task configuration using atomic replacement, preserve unknown root/task/options fields on edits, and warn users that workspace tasks can execute code when explicitly run.
- Add backend CRUD/security regression tests and Playwright UI smoke coverage for create/update/delete and explicit execution.

## 0.28.0

- Add TypeScript semantic diagnostics for type errors, not just syntax errors.
- Enable JavaScript semantic/JSDoc diagnostics for `// @ts-check` files and projects whose root `tsconfig.json` sets `compilerOptions.checkJs: true`.
- Add regression tests for TypeScript and checked-JavaScript type mismatches.

## 0.27.0

- Add TypeScript semantic type diagnostics to the existing syntax and `tsconfig.json` diagnostics; report mapped line/column positions and deduplicate messages.
- Add a regression test for a type mismatch missed by syntax-only checks.
- Carry forward Go to Symbol, launch configuration CRUD, Testing activity, workspace settings, debugger improvements, and the established safety bounds.

## 0.26.0
- Added **Go to Symbol in Workspace** (`Ctrl+T`) for Python and lightweight JavaScript/TypeScript declaration discovery, fuzzy search/ranking, and click-to-open file/line navigation.
- Python symbols are discovered via `ast` without executing source; JavaScript/TypeScript use a deliberately lightweight declaration scanner (not a full parser or language server).
- Added Run & Debug launch-configuration create/edit/delete controls for supported Python and Node.js configs. The backend validates input and workspace-contained paths, rejects malformed JSON and symlinks, preserves unknown config metadata when updating, and commits changes via bounded atomic writes.
- Searches honor open editor buffers, including unsaved/new source files. Scan skips dependency/build/cache folders and symlinks, with limits on open buffers, source bytes, entries, files, nesting depth, and declaration count.
- Added regression coverage for nested Python declarations, JS/TS declarations, unsaved buffers, malformed source, POSIX/Windows path traversal and symlink refusal, plus mocked-Eel UI smoke coverage for `Ctrl+T` fuzzy selection and navigation.
- Fixed a Command Palette hover/click race that could remove an option just before activation; the selected item now updates without rebuilding the button list.
- Stabilized Python debugging startup by setting a first-line pause when no explicit breakpoints are supplied; launch-configuration args, cwd and environment integration test passes.
- Added launch-config CRUD regression tests covering atomic persistence, preservation of unknown fields, duplicate names, unsafe paths, unsupported types, malformed JSON, and symlinked `.vscode` directories.

## 0.25.0
- Added a Testing activity / lightweight Test Explorer that discovers conventional Python and Node.js test files and root `package.json` test scripts. Explicit actions can run Python tests (pytest when installed, otherwise unittest), Node's built-in test runner, or `npm test`.
- Test execution is disabled when no matching suite/runner is available, and dirty editor buffers must save successfully before a run. Commands use fixed argv for direct runners, bounded output/time, a 120-second timeout, and an explicit UI warning that project tests execute workspace code.
- Bounded discovery skips common dependency/build/cache folders and symlinks, is capped at 300 recognized test files / 20,000 entries and directory depth 12, and handles malformed or oversized `package.json` without running it.
- Added backend tests for conventional patterns, ignored dependency folders, real temporary Python/Node/npm test runs, invalid runner selections, and missing-suite errors; browser smoke now validates discovery and all runner routes.
- Added/updated workspace settings documentation, launcher/version markers and release metadata.

## 0.24.0
- Added validated workspace-specific overrides backed by `.vscode/settings.json`: editor font size, word wrap, minimap, Auto Save mode/delay, spaces versus tabs, and tab size.
- Added a Workspace Settings section in the Settings activity with inherit/reset controls; effective settings now apply when a workspace opens and update the built-in editor and Monaco options.
- Built-in Tab indentation and paired-bracket Enter indentation now honor workspace tab/space settings.
- Saving a workspace settings file is atomic and bounded to 256 KB; recognized values are type/range checked, unknown keys are preserved, malformed JSON is not overwritten, and symlinked workspace settings paths are rejected.
- Added sample workspace settings plus backend and browser regression coverage for override save/reset, effective styling, tab indentation, malformed files, unsupported values, symlinks and workspace isolation.

## 0.23.0
- Added a central side-by-side Git diff viewer with line numbers, aligned changed rows and color-coded insertions/deletions; clicking a changed file opens the comparison in the editor workspace.
- Added safe Git object reads with a 1 MB per-file cap, binary-file rejection, 2,500 lines per side, 1,500 aligned rows and per-line truncation to keep rendering bounded.
- Preserved the plain unified diff and staged diff commands; renamed files compare their previous HEAD path to the new working-tree path when Git status supplies the rename origin.
- Added backend coverage for changed, untracked, missing and oversized/binary paths, and browser coverage for changed-file click-to-compare and central viewer rendering.

## 0.22.0
- Added remote-aware Git controls for configured remote listing, remote-tracking branch listing, fetch/prune, explicit push, and fast-forward-only pull. Calls use validated remote/branch identifiers as separate argv values, disable interactive terminal credential prompts, and never force-push.
- Added local branch merge, bounded unresolved-conflict file enumeration, click-to-open conflict files with fresh on-disk content, and merge abort. The UI now refreshes the conflict list automatically when a merge or pull fails.
- Reloads clean open editor tabs after successful merge, pull or merge abort, while preserving the existing dirty-buffer preflight.
- Added real integration tests using a local bare Git remote for initial push, fetch, fast-forward pull, push-after-commit, and conflict detect/abort; browser smoke tests now cover remote parameter routing and conflict list/reload/abort.

## 0.21.0
- Expanded Source Control with branch listing, validated create-and-switch / branch switching, path-scoped diff and staged diff, file history, unstage, and commit workflows.
- Added six real local-Git integration tests for branch switching, unborn-repository unstage, commit/diff workflows, space-containing and renamed paths, invalid branch names and workspace path escape rejection.
- Git status now includes a bounded structured changed-file list with click-to-select/diff. Git UI blocks stage/commit/branch changes when relevant editor buffers are unsaved, prompts before branch changes, and reloads clean tabs after a branch switch.

## 0.20.0
- Added a bounded workspace-wide Problems scan across supported Python, JavaScript/TypeScript, JSON, HTML, CSS/SCSS and XML sources, including the current unsaved buffers without writing them to disk.
- Workspace diagnostics now identify the relative file path and support click-to-open and jump-to-line/column navigation. Dependency/build/cache directories and symlinks are excluded; file count, total bytes, per-file size and scan time are capped.
- Hardened Run and HTML/Markdown Preview so failed saves or unresolved external-file conflicts stop execution/preview instead of silently using stale on-disk content.
- Added backend tests for workspace scanning, dependency exclusion, traversal rejection and non-mutating unsaved-buffer validation; added browser smoke coverage for scan results and navigation.

## 0.19.0
- Added per-workspace restart recovery for open tabs and unsaved drafts, persisted by Python to the per-user state directory so recovery does not depend on Eel's random browser port.
- Recovery snapshots use atomic writes and validate workspace-relative paths, draft sizes and hashes. If disk content changed or a file disappeared while the app was closed, the recovered draft remains dirty and the existing conflict flow prevents silent overwrites.
- Made UI preferences and last-workspace restoration persist through the Python backend; added persisted per-workspace debugger breakpoints and Watch Expressions.
- Added backend tests for settings validation, workspace isolation, session round-trips, large payload limits and stale-disk conflict protection, plus a mocked-renderer recovery smoke test.

## 0.18.0
- Replaced the POSIX pipe-only terminal with a real controlling PTY; shell prompts and chunked output are available even without a newline.
- Added optional Windows WinPTY adapter through `requirements-win-pty.txt`, while retaining the pipe fallback when optional installation is unavailable.
- Improved terminal output handling so CR/LF, ANSI CSI and OSC control sequences do not leak into ordinary text output. The UI remains line-submitted and is not a full terminal emulator.
- Added a real subprocess regression test proving that a program launched from the integrated shell detects stdin as a TTY.

## 0.17.0
- Added optional TypeScript compiler language-service support for JavaScript/TypeScript completion, go-to-definition, cross-file references, hover/signature info, syntactic diagnostics and multi-file Rename Symbol.
- Added an optional `package.json` TypeScript tool dependency; without it, the desktop app continues to start and basic local-word suggestions remain available.
- Added TypeScript compiler formatting for JS/TS via Shift+Alt+F, plus backend and mocked-browser regression coverage for language-service actions, configured path aliases, cross-file rename and formatting.

## 0.16.0
- Added `.vscode/launch.json` for Python and Node.js launch configurations, expanding `${file}`/workspace variables and passing args, cwd, env and Node runtimeArgs to the debugger process.
- Added launch-config validation and tests for per-language discovery, invalid/out-of-workspace paths, unsupported attach mode, and real Python/Node args/cwd/env propagation.
- Added saved Watch Expressions to both debugger engines, evaluating against the paused program context and marking unsafe expressions as trusted-code-only.
- Added a Run & Debug launch selector that switches per active file and defaults to its first valid launch configuration.

## 0.15.0
- Added Node.js JavaScript source debugging via the Inspector/CDP WebSocket on loopback: pause on entry, line breakpoints, continue, step into/over, local variable snapshots, console output and stop/exit cleanup.
- Integrated Python and Node.js debugger routing into the Run and Debug UI with shared breakpoint gutter rendering and persistent per-workspace breakpoints.
- Added real subprocess integration tests for Node debugger lifecycle, breakpoint hits, locals, stepping, output de-duplication and stopping, plus mocked UI coverage.

## 0.14.0
- Added optional configurable Auto Save (0.5, 1, 2, 5 or 10 seconds), off by default; timers are scoped per file.
- Made saves capture a buffer snapshot so edits typed while an asynchronous save is in flight remain dirty and get a follow-up save.

## 0.13.0
- Added a save-conflict view showing the local unsaved buffer and current on-disk version, with Reload, Keep Editing and confirmed Overwrite choices.
- Extended atomic save concurrency protection with an explicit force flag used only after the overwrite confirmation.

## 0.12.0
- Changed editor saves to same-directory atomic replacement and added SHA-256 optimistic concurrency checks to avoid silently overwriting external edits.
- Added last-opened workspace restoration via local UI preferences.
- Updated multi-file refactors to refresh open-tab hashes after applying replacements.

## 0.11.0
- Added Black-based Python document formatting (88-column default), preview-only backend API and Shift+Alt+F command.
- Expanded syntax diagnostics to HTML tag balance, CSS delimiter/string structure and XML well-formedness checks; warning and error counts are now reported separately.
- Added regression tests for the new validators and Python formatting behavior.

# Changelog

## 0.10.0
- Added workspace-wide literal Find/Replace with diff previews, match-case option, explicit confirmation, per-file hash checks and atomic staged apply/rollback.
- Added UI tests for Python intelligence, symbol rename and workspace-wide Find/Replace flows, plus backend coverage for stale-file refusal and rollback.

## 0.9.0
- Added Jedi-powered Python completion, Go to Definition, Find References and symbol inspection.
- Added multi-file Python Rename Symbol with a diff preview, explicit Apply, save-before-refactor, file-hash checks and rollback on replace failures.
- Added regression tests for cross-file rename, stale-file refusal, rename rollback, task shell mode/environment expansion and Python intelligence.
- Improved completion responsiveness with a small debounce and made launchers skip unnecessary dependency reinstalls.

## 0.8.0
- Added Jedi-powered Python completions, definitions, hover/documentation and project references.
- Added workspace/file-size guards and tests for unsupported languages and oversized buffers.

## 0.7.0
- Added a Python source debugger isolated in a child process, with line breakpoints, Continue, Step Into, Step Over, Stop, paused-line highlighting and local-variable snapshots.
- Added live breakpoint updates while paused and a debug console showing output and exception tracebacks.
- Added task discovery/execution from `.vscode/tasks.json`, workspace-bounded working directories and a 120-second timeout.

## 0.6.0
- Added optional Monaco Editor 0.57.0 loading from jsDelivr, with automatic fallback when unavailable. Monaco assets are not bundled.
- Synced active models, applied settings, and mapped available Python/JSON/Node diagnostics to Monaco markers.
- Added JavaScript syntax diagnostics, persistent non-PTY shell, HTML preview with local assets, JSON formatting, Find/Replace, settings persistence and basic Git actions.
- Added Open File/Save As dialogs, safe Markdown preview, Outline/Timeline and expanded backend tests.

## Scope
FlowCode Studio's semantic version applies to this project. It is not a Microsoft Visual Studio Code release and does not claim full VS Code feature parity.
