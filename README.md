# hermes-agyx

Hermes-native **`agyx`** tool — a coding/image agent backed by your **paid
`agy` (Google Antigravity / Code Assist) OAuth login**.

All five capabilities route through the `agy` CLI using your existing paid
subscription — no Gemini API key required for the primary path:

| Capability | How |
|---|---|
| Text / Q&A | `agy` agent |
| Local file **READ** | `read=` → text inlined / `agy view_file` |
| Image **ANALYSIS** | `img=` → `agy view_file` |
| Local file **WRITE** | `agy write_to_file` into `out_dir` |
| Image **GENERATION** | `gen=` → `agy` built-in `generate_image` (paid) |
| **Self-healing coding loop** | `exec` + `verify` + `auto_fix` (bounded retries) |

A key-only fallback to the public Gemini REST API is used only when `agy` is
absent **and** a `GEMINI_API_KEY` is set.

---

## Prerequisites

- **Hermes Agent** installed (https://github.com/NousResearch/hermes-agent).
- **`agy`** on `PATH`, authenticated with your paid Google Antigravity / Code
  Assist OAuth login. Confirm with:
  ```bash
  agy -p "hello"
  ```
- (Optional fallback) a `GEMINI_API_KEY` if you don't have `agy`.

> **Geo-blocked regions** (e.g. Hong Kong): agy requires a proxy bypass to pass
> the eligibility check. See `skills/agy-bypass/references/bypass-setup.md` for
> the full setup (mitmproxy + systemd + wrapper script). **agy v1.1.5 is
> required** — v1.1.7 changed the `onboardUser` proto format in a way our
> bypass cannot match.

No third-party Python packages are required at runtime — the tool uses only the
standard library (`urllib`, `subprocess`, `fcntl`).

---

## Install (no core edits — drop-in plugin)

The tool is a **Hermes plugin**. It registers itself via `ctx.register_tool(...)`
and never modifies core Hermes files.

```bash
# Option A: copy into your Hermes plugins dir (zero install)
mkdir -p ~/.hermes/plugins
cp -r agyx_plugin ~/.hermes/plugins/agyx_plugin

# Option B: pip-install the package (also fine; Hermes discovers entry-point-free
# plugins by directory, so copying Option A is simplest)
pip install .
```

Restart Hermes. The `agyx` tool appears in the **`agy`** toolset, gated by
availability (zero schema footprint until `agy` is present or `GEMINI_API_KEY`
is set).

### Verify registration

```python
# Inside a Hermes tool context:
from tools.registry import registry, discover_builtin_tools
discover_builtin_tools()
print(registry.dispatch("agyx", {"prompt": "say hi", "out_dir": "/tmp/agyx_check"}))
```

(When installed as a plugin, Hermes discovers it automatically on startup — no
explicit `discover_builtin_tools()` import is needed.)

---

## Usage (as a Hermes tool)

The agent calls `agyx` like any other tool. Key arguments:

| Arg | Meaning |
|---|---|
| `prompt` | Task. For image-gen only, may be empty and use `gen`. |
| `read` | List of local file paths to read into context. |
| `img` | List of local image paths for `agy` to analyze. |
| `gen` | Image-generation prompt; result saved to `out_dir`. |
| `exec` | Shell command for `agy` to run (`run_command`) — build/run/verify. |
| `verify` | Shell command the tool runs after `agy` to check success. |
| `auto_fix` | If `verify` fails, re-prompt `agy` to fix (bounded). |
| `max_fix_rounds` | Max auto-fix retries (default 1, hard cap 3). |
| `watch_dirs` | Extra dirs to watch for edits outside `out_dir`. |
| `continue_conv` | Resume the most recent `agy` conversation for multi-turn tasks. |
| `conversation_id` | Resume a specific `agy` conversation by ID. |
| `add_dir` | Add workspace directories for project context (--add-dir). |
| `mode` | Agent execution mode: `accept-edits` (auto-apply) or `plan`. |
| `effort` | Reasoning effort: `low`, `medium`, or `high`. |
| `timeout` | Per-`agy`-call timeout seconds (default 300). |
| `out_dir` | Where files land (default `./agyx_out`). |

### Self-healing coding loop (example)

Read buggy code → `agy` fixes + writes a test → `exec` runs it → `verify` checks
the exit code → on failure `auto_fix` asks `agy` to fix again (bounded).

```python
registry.dispatch("agyx", {
    "prompt": "Fix bugs in calc.py; write a passing test",
    "read":    ["./buggy/calc.py"],
    "exec":    "cd ./buggy && python3 -m pytest -q",
    "verify":  "cd ./buggy && python3 -m pytest -q",
    "auto_fix": True,
    "max_fix_rounds": 3,
    "out_dir": "./buggy",
})
```

Result JSON includes `success`, `text`, `written_files`, `images`,
`verify_exit`, `verify_output`, `rounds`, `auto_fixed`/`fix_rounds`, `elapsed_s`,
and `error`.

---

## Usage (standalone CLI)

```bash
# text + read a file
agyx "Explain what this script does" --read ./script.py --out-dir ./agyx_out

# multi-turn conversation
agyx "Implement feature X" --read ./source.py --out-dir ./src
agyx "Now add tests for that feature" --continue --out-dir ./src

# with workspace context
agyx "Find the bug" --add-dir ./project --read ./project/main.py

# plan mode with high effort
agyx "Refactor the auth module" --read ./auth.py --mode plan --effort high

# image generation
agyx "unused" --gen "A blue circle on white" --out-dir ./agyx_out

# self-healing loop
agyx "Fix bugs in calc.py" --read ./buggy/calc.py \
    --verify "cd ./buggy && python3 -m pytest -q" \
    --auto-fix --max-fix-rounds 3
```

---

## Tests

Network-free (mock `agy`/subprocess/Gemini). Run:

```bash
pip install -e ".[test]"
pytest
```

These prove parse/save/loop logic without consuming quota. Always re-prove the
real path (live `agy`) before claiming a capability works — see
`skills/agy-bypass/references/agyx-testing-recipe.md`.

---

## How it works

File writes in `agy -p` (print/one-shot) mode use **write fences** because agy's
`write_to_file` tool only executes in interactive mode. The model emits code like:

    ```write:relative/path
    <file contents>
    ```

The tool extracts the fence, persists the file to `out_dir`, and replaces the
fence with `[WROTE FILE /path]` in the returned text.

```
agyx → `agy` CLI (paid OAuth, Google internal Cloud Code API)  [PRIMARY]
        read → inline/agy view_file
        img  → agy view_file
        write→ agy write_to_file into out_dir
        gen  → agy generate_image (paid)
        exec → agy run_command
        (out_dir + watch_dirs + read-parents diffed before/after → written_files)

↳ FALLBACK (only if agy absent AND GEMINI_API_KEY set):
        direct generativelanguage.googleapis.com?key=… (text/read/write/analysis
        + image gen if the key has image quota)
```

Concurrent `agyx` calls are serialized by a cross-process lock because `agy`
shares one OAuth/browser profile.

---

## Privacy & safety

- **No secrets in this repo.** Image generation uses your own `agy` login; the
  tool never embeds API keys or tokens.
- The tool only writes/creates files under `out_dir` (and optionally
  `watch_dirs`/read-parents) — it does not touch Hermes internals.
- The shell is only ever executed by `agy`'s `run_command` (for `exec`) or by
  the tool's own **bounded** `verify` (only when you pass `verify`). There is no
  unbounded internal loop.

## License

MIT — see [LICENSE](LICENSE).
