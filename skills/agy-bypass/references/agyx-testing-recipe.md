# agyx live-verification recipe (paid agy OAuth path)

Deterministic, re-runnable checks that prove `agyx` actually works through the
user's paid `agy` login — not just that the module imports. Re-run after any
refactor of `tools/agyx_tool.py`.

## 1. Registration + discovery (catches the "Unknown tool" trap)

```python
from tools.registry import registry, discover_builtin_tools
import tools.agyx_tool as m
discover_builtin_tools()          # MUST run after import, same process
assert registry.get_entry("agyx") is not None
assert registry.get_entry("agyx").check_fn() is True   # agy on PATH
```

## 2. Live multi-capability sweep via registry.dispatch

Run all five paths through the real auth path (each shells out to `agy`):

```python
from tools.registry import registry, discover_builtin_tools
import tools.agyx_tool as m
discover_builtin_tools()
out = "/tmp/agyx_live"
import os; os.makedirs(out, exist_ok=True)

# text
d = json.loads(registry.dispatch("agyx", {"prompt":"Reply with exactly: HELLO", "out_dir":out}, task_id="text"))
# file read (real file)
d = json.loads(registry.dispatch("agyx", {"prompt":"What does this do? 1 sentence.", "read":["/path/calc.py"], "out_dir":out}, task_id="read"))
# image analysis — USE A REAL IMAGE (degenerate 32x32 PNGs fail in agy!)
d = json.loads(registry.dispatch("agyx", {"prompt":"Describe this image in one sentence.", "img":["/path/real.jpg"], "out_dir":out}, task_id="img"))
# file write
d = json.loads(registry.dispatch("agyx", {"prompt":"Create greeting.txt containing exactly: hi", "out_dir":out}, task_id="write"))
# image generation
d = json.loads(registry.dispatch("agyx", {"gen":"a yellow star on blue", "out_dir":out}, task_id="gen"))
assert d["images"] and os.path.getsize(d["images"][0]) > 0
```

## 3. Self-healing coding loop (complex task)

```python
verify = "cd /tmp/proj && python3 test_compute.py"
d = json.loads(registry.dispatch("agyx", {
    "prompt": "Fix <path>/compute.py bugs and write test_compute.py with assertions.",
    "read": ["/tmp/proj/compute.py"],
    "exec": "cd /tmp/proj && python3 test_compute.py",
    "verify": verify,
    "auto_fix": True,
    "out_dir": "/tmp/proj",
    "timeout": 300,
}, task_id="complex"))
assert d["verify_exit"] == 0   # agy fixed + test passed (retry if needed)
assert d["elapsed_s"] > 0       # observability field always present
```

## 4. Live proof: out-of-tree edit detection (gap 4)

Point agy at a file OUTSIDE `out_dir` and confirm `written_files` reports it:

```python
repo = "/tmp/agyx_repo"                 # make this dir + calc.py with bug `a - b`
open(f"{repo}/calc.py", "w").write("def add(a,b):\n    return a - b\n")
d = json.loads(registry.dispatch("agyx", {
    "prompt": "Read calc.py. Fix add() to return a + b IN PLACE.",
    "read": [f"{repo}/calc.py"],
    "watch_dirs": [repo],              # also auto-watched via read-parent
    "out_dir": "/tmp/agyx_live3",
}, task_id="outoftree"))
assert f"{repo}/calc.py" in d["written_files"]   # NOT just files in out_dir
assert "return a + b" in open(f"{repo}/calc.py").read()
```

## 5. Live proof: bounded multi-round auto_fix (gap 5)

Under-specify the prompt but make `verify` demand an extra file, so the first
pass fails and a retry fires. Cap retries with `max_fix_rounds`:

```python
verify = ("cd /tmp/agyx_live5 && python3 -c \"import greet; assert greet.hello('X')=='Hi X'\" "
          "&& test -f TOKEN && grep -q 'SECRET42' TOKEN")
d = json.loads(registry.dispatch("agyx", {
    "prompt": "Create greet.py in out_dir with hello(name)->'Hi {name}'. That's all.",
    "verify": verify,
    "auto_fix": True,
    "max_fix_rounds": 3,               # hard cap; never loops forever
    "out_dir": "/tmp/agyx_live5",
}, task_id="multiround"))
# rounds == 1 + fix_rounds; auto_fixed True iff verify passed after retries
assert d["rounds"] >= 2 and d["fix_rounds"] >= 1
```

## 6. Structured-error surfacing (gap 3)

A degenerate image (or quota/auth abort) makes agy reply with a sentinel and
exit 0. The tool must turn that into `success:False`:

```python
# unit-level (no live quota needed):
import tools.agyx_tool as m
assert m._agy_errored("Error: Agent execution terminated due to error.") is not None
assert m._agy_errored("all good") is None
```

## Known-good results (from this session)
- text / read / write / gen: all succeeded in one `agy` call each (~7–60s).
- image analysis: works on a real 603 KB JPEG ("a glowing yellow star on a dark
  blue background"); FAILS on a hand-built 32×32 red PNG with "Agent execution
  terminated due to error" — a fixture artifact, not a tool bug (now a clean
  `success:False` via `_agy_errored`).
- complex fix+test+verify loop: completed in ~19s, verify_exit=0.
- out-of-tree edit (section 4): VERIFIED LIVE — written_files included the
  repo file, real file became `a + b`.
- bounded multi-round fix (section 5): VERIFIED LIVE — rounds:2, fix_rounds:1,
  auto_fixed:True, verify_exit:0.
- 25 network-free unit tests pass (`scripts/run_tests.sh tests/tools/test_agyx_tool.py`).

## Pitfalls
- `dispatch` returns "Unknown tool: agyx" if you forgot `import tools.agyx_tool`
  + `discover_builtin_tools()` before dispatching in the same process.
- agy image-analysis errors on degenerate test PNGs — use a real image.
- agy image-gen needs the proxy env CLEARED (handled inside `run_via_agy`); do
  not wrap these calls in `HTTPS_PROXY=127.0.0.1:8085`.
- Mock `fake_run_via_agy` must accept `(prompt, read, img, gen, out_dir,
  exec=None, timeout=300, watch_dirs=None)` — both `exec` and `watch_dirs`
  kwargs are required by the routing tests.
