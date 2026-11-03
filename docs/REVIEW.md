# Project review — 2026-11-03

Reviewed the CLI, scanning, rules, termination, reporting, packaging setup, and
existing tests. This change focuses on termination correctness and CLI output.

## Fixed findings

| Priority | Finding | Change |
| --- | --- | --- |
| High | Windows forced termination used `/T`, bypassing rule checks for child processes. | Target only the selected PID. |
| High | Failed `taskkill` commands and direct force mode could be reported as successful. | Check command exit status and observe process exit before reporting success. |
| Medium | POSIX `ProcessLookupError` incorrectly meant the process still existed; Windows matched PID text anywhere in output. | Distinguish missing processes from permission errors; parse the exact CSV PID column. |
| Medium | Process-query errors looked like an already exited process. | Report failure rather than assume absence. |
| High | Nonpositive PIDs can address POSIX process groups if passed directly to the terminator. | Reject these PIDs before any OS call. |
| Medium | `--execute --format json` emitted two JSON documents and confirmation text on stdout. | Emit one final JSON result; put the preview, prompt, and diagnostics on stderr. |
| Medium | `--top 0` and negative values silently selected up to 200 processes. | Reject nonpositive values during argument parsing. |
| Low | Empty candidate sets were always described as whitelist exclusions. | Include unmatched names and memory thresholds in the explanation. |

Waits now use a monotonic clock and allow a short verification window after a
forced termination request. A rejected Windows termination command is reported
as a failure; it does not trigger a silent forced retry.

## Validation

- Baseline: 23 tests passed on Windows.
- Updated suite: 44 tests passed with `python -X utf8 -m unittest discover -s tests -v`.
- Added 21 tests covering termination, process checks, JSON execution/cancellation,
  failures, and candidate limits.
- Execution tests mock the scanner and terminator. Termination tests mock OS
  commands and signals. Existing integration tests only scan or preview.
- Linux/macOS termination branches were checked with mocks, not on native hosts.
- The existing ignored executable in `dist/` has not been rebuilt; these changes
  apply to source and future builds.

## Remaining follow-up work

- Process identity is still based on PID alone. PID reuse between scanning,
  confirmation, and termination can target a replacement process. Creation-time
  checks or platform process handles would be needed to address this race.
- Configuration loading checks JSON syntax but does not fully validate field
  types or numeric ranges. For example, negative `match.max_count` disables the
  configured cap, and malformed protection fields can fail or weaken filtering.
  Strict configuration validation should be a separate change with explicit
  compatibility decisions.
- Without psutil, executable paths and full ancestor information are unavailable;
  path protection and ancestor coverage remain limited as described in the README.
