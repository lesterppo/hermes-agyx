# agyx as a self-healing coding loop — expansion + live-test gaps

The `agyx` native tool (`tools/agyx_tool.py`) routes ALL capabilities through
the paid `agy` OAuth login when `agy` is on PATH. Beyond text/read/img/gen it
supports a real coding-agent loop.

## Coding-loop args (added, live-verified)
- `exec` (str): shell command for `agy`'s own `run_command` tool (e.g.
  `python3 -m pytest -q`). Output folded into the reply.
- `verify` (str): command the TOOL itself runs after agy finishes (shell=True,
  cwd=out_dir, bounded to min(timeout,120)s). Non-zero = failure. Only runs if
  passed. Reports `verify_exit` / `verify_output`.
- `auto_fix` (bool): on verify failure, re-prompt agy and re-verify, repeating
  until verify passes OR `max_fix_rounds` is reached (never unbounded).
  `result["auto_fixed"]` = did the last retry pass; `result["fix_rounds"]` =
  retries taken; `result["rounds"]` = total agy calls (initial + retries).
- `max_fix_rounds` (int, default 1, hard cap 3).
- `watch_dirs` (list[str]): extra dirs to watch beyond `out_dir` (see gap 4).
- `timeout` (int, default 300): per-agy-call bound.

Result JSON: `success, text, written_files (incl. out-of-tree edits), images,
verify_exit, verify_output, rounds, auto_fixed/fix_rounds (if retried),
elapsed_s, error`.

## Live-verified behavior (paid agy OAuth, real quota)
- Multi-file fix vs a PRE-WRITTEN strict pytest suite (agent can't grade its own
  work): 5/5 pass, ~31s, 1 round. Add an 8-thread concurrency test → agy adds a
  lock unprompted, 6/6 pass, ~46s.
- auto_fix retry FIRED live (rounds=2, auto_fixed=True) by under-specifying the
  prompt but demanding an extra file in `verify` (e.g. a VERSION file). This is
  the reliable way to exercise the retry: make `verify` require something the
  main prompt omits.
- out-of-tree edit: pointed agy at `/tmp/agyx_repo/calc.py` (bug `a - b`),
  `written_files` returned `['/tmp/agyx_repo/calc.py']` and the real file became
  `a + b` — proving gap-4's watch_dirs + read-parent auto-watch works on real files.
- agy is strong enough that naive "buggy code" tasks pass in one round. To test
  the retry path deliberately, use the verify-demands-more trick above.

## Gaps found + ALL NOW FIXED (this batch)
1. FIXED — in-place edits were invisible. `_snapshot_dir` tracked only a SET of
   paths, so agy editing existing files gave `written_files=None`. Now snapshots
   `{path:(mtime_ns,size)}` and `_changed_files(before,after)` reports created OR
   modified files. This was the worst gap — tool silently under-reported its work.
2. FIXED — cache noise leaked into written_files. `_changed_files` filters
   `.pyc`, `__pycache__`, `.pytest_cache`, `.mypy_cache`, `.ruff_cache`, `.git/`,
   `.tox`, `.hypothesis`.
3. FIXED — structured error surface. `_agy_errored()` scans the reply for
   sentinels ("Agent execution terminated due to error", "quota exceeded",
   "RESOURCE_EXHAUSTED", "UNAUTHENTICATED", "PERMISSION_DENIED", "failed to
   launch", "timed out after"). If found AND no files were produced, returns
   `success:False` with `error:"agy internal failure: <snippet>"` instead of
   masquerading failure as success. Also: if `verify` still fails after all fix
   rounds, `success:False` with `verify_exit` + last output.
4. FIXED — out-of-tree edits. `run_via_agy` snapshots `out_dir` + every
   `watch_dirs` entry + the PARENT DIR of every `read` path automatically. Edits
   to an existing repo outside `out_dir` are reported in `written_files`.
   VERIFIED LIVE (see above).
5. FIXED — bounded multi-round auto_fix. The single hard-coded retry is now a
   loop bounded by `max_fix_rounds` (default 1, hard cap 3 via
   `max(0, min(int(max_fix_rounds), 3))`). Reports `fix_rounds` + `auto_fixed`.
   VERIFIED LIVE (rounds=2, fix_rounds=1).
6. FIXED — concurrency. `_AgyLock` (fcntl on POSIX, no-op where fcntl absent)
   serializes the actual `agy` subprocess invocation (agy shares one OAuth/
   browser profile). Degrades to no-op on Windows rather than crash.
7. ADDED — observability. Every result carries `elapsed_s` (real wall-clock
   seconds for the whole task) so quota/latency cost is visible.

## Test/verify pitfalls learned
- run_via_agy signature is now (prompt, read, img, gen, out_dir, exec=None,
  timeout=300, watch_dirs=None). Any mock `fake_run_via_agy` MUST include BOTH
  `exec=None` AND `watch_dirs=None` or the routing tests fail with a TypeError.
- Registry dispatch needs the module IMPORTED first (`import tools.agyx_tool` +
  `discover_builtin_tools()`); a bare `registry.dispatch("agyx",...)` in a fresh
  process returns "Unknown tool: agyx".
- agy image analysis fails on degenerate tiny images (32x32 hand-built PNG →
  "Agent execution terminated due to error") but works fine on real images.
  Not a tool bug; the tool surfaces agy's reply (now also a clean `success:False`
  via `_agy_errored`).
- 25 network-free unit tests cover: in-place+new detection with noise filtering,
  `_snapshot_dir` shape, `_agy_errored` detection, `max_fix_rounds` capping (incl.
  the hard cap of 3), `watch_dirs` passthrough, `elapsed_s` presence, and
  `_run_verify` real-shell exit codes. Run:
  `scripts/run_tests.sh tests/tools/test_agyx_tool.py -q`.
