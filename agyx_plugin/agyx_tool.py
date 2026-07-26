#!/usr/bin/env python3
"""
agyx — Hermes-native AGY-bypass tool backed by the user's paid agy OAuth login.

This is the in-repo implementation of the AGY (Google Antigravity/Code Assist)
bypass expanded to a real tool surface. EVERY capability is driven through the
`agy` CLI (Google Antigravity), which uses the user's paid OAuth login via
Google's internal Cloud Code API — no Gemini API key required:

  - local file READ   (read=)      -> text inlined / agy view_file
  - local file WRITE  (write_to_file via agy) -> agent persists results to disk
  - image ANALYSIS    (img=)       -> agy view_file on the image
  - image GENERATION  (gen=)       -> agy generate_image tool (paid subscription)

The public Gemini REST endpoint (generativelanguage.googleapis.com) is only used
as a KEY-ONLY FALLBACK when `agy` is not installed but a GEMINI_API_KEY is set.
The primary, unified path is the paid agy OAuth login.

It is a SERVICE-GATED tool (check_fn on `agy` availability, then GEMINI_API_KEY).
It is registered in the `agy` toolset and also ships in _HERMES_CORE_TOOLS
(gated by check_fn, so zero schema footprint when neither auth is configured).

The standalone CLI wrapper lives at ~/.hermes/scripts/agyx.py and imports this
module, so there is a single source of truth.

All handlers return a JSON string (Hermes tool convention).
"""
import base64
import json
import mimetypes
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

# `agyx_tool` is the single source of truth for all logic. When installed as a
# Hermes plugin, registration is done by `agyx_plugin/__init__.py` via
# `ctx.register_tool(...)`. To stay self-contained (and importable without a
# full Hermes core import), we provide a tiny `tool_error` shim here instead of
# importing it from `tools.registry`. The `registry.register(...)` call that
# existed in the in-repo version is intentionally removed — the plugin loader
# registers the tool.
def tool_error(message: str, success: bool = False) -> str:
    """Return a compact JSON error string (Hermes tool convention)."""
    return json.dumps({"success": success, "error": str(message)}, ensure_ascii=False)

KEY_PATH = os.path.expanduser("~/.hermes/.env")
DEFAULT_TEXT_MODEL = "gemini-3.5-flash"
DEFAULT_IMG_GEN_MODEL = "gemini-2.5-flash-image"
MAX_BYTES = 8_000_000  # ~8MB inline cap; larger files are refused with a message

# agyx talks DIRECTLY to the public Gemini API. Never route through an
# HTTP(S) proxy (e.g. the agy-bypass mitmproxy) — clear proxy env vars.
for _pv in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
            "ALL_PROXY", "all_proxy"):
    os.environ.pop(_pv, None)

TEXT_EXTS = {
    ".txt", ".md", ".py", ".js", ".ts", ".tsx", ".jsx", ".json", ".yaml",
    ".yml", ".toml", ".cfg", ".ini", ".sh", ".bash", ".zsh", ".csv", ".tsv",
    ".log", ".html", ".css", ".scss", ".xml", ".sql", ".go", ".rs", ".c",
    ".h", ".cpp", ".hpp", ".java", ".kt", ".swift", ".rb", ".php", ".pl",
    ".r", ".ipynb", ".tex", ".rst", ".dockerfile", ".env", ".gitignore",
}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"}


def get_api_keys() -> List[str]:
    keys: List[str] = []
    for name in ("GEMINI_API_KEY", "GEMINI_API_KEY_2", "GEMINI_API_KEY_3",
                 "GEMINI_API_KEY_4", "GEMINI_API_KEY_5"):
        v = os.environ.get(name, "").strip().strip('"').strip("'")
        if v and v not in keys:
            keys.append(v)
    try:
        with open(KEY_PATH) as f:
            for line in f:
                line = line.strip()
                if "=" in line:
                    k, v = line.split("=", 1)
                    if k.strip() in ("GEMINI_API_KEY", "GEMINI_API_KEY_2",
                                     "GEMINI_API_KEY_3", "GEMINI_API_KEY_4",
                                     "GEMINI_API_KEY_5"):
                        v = v.strip().strip('"').strip("'")
                        if v and v not in keys:
                            keys.append(v)
    except Exception:
        pass
    return keys


def check_agyx_requirements() -> bool:
    """Availability gate: a paid `agy` login (preferred) OR a Gemini API key.

    Image generation and the full file/image tool surface require the paid `agy`
    OAuth login. A Gemini API key enables the direct-REST fallback only.
    """
    return bool(which_agy()) or bool(get_api_keys())


def mime_for(path: str) -> str:
    mt, _ = mimetypes.guess_type(path)
    return mt or "application/octet-stream"


def read_file_part(path: str) -> Dict[str, Any]:
    """Return a Gemini content part for a local file (text inline or image inline)."""
    if not os.path.isfile(path):
        return {"text": f"[FILE ERROR] not found: {path}"}
    ext = os.path.splitext(path)[1].lower()
    size = os.path.getsize(path)
    if ext in TEXT_EXTS:
        if size > MAX_BYTES:
            return {"text": f"[FILE ERROR] {path} is {size} bytes (> {MAX_BYTES}); too large to inline."}
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                content = fh.read()
        except Exception as e:
            return {"text": f"[FILE ERROR] could not read {path}: {e}"}
        return {"text": f'<FILE path="{path}" bytes="{size}">\n{content}\n</FILE>'}
    if ext in IMAGE_EXTS:
        if size > MAX_BYTES:
            return {"text": f"[FILE ERROR] image {path} too large to attach ({size} bytes)."}
        try:
            with open(path, "rb") as fh:
                b64 = base64.b64encode(fh.read()).decode()
        except Exception as e:
            return {"text": f"[FILE ERROR] could not read image {path}: {e}"}
        return {"inlineData": {"mimeType": mime_for(path), "data": b64}}
    return {"text": f"[FILE SKIP] {path} is type {mime_for(path)} ({size} bytes); "
                    f"not inlined. Use a supported text or image file."}


def call_gemini_generate(model: str, contents: List[Dict], thinking: bool = False,
                         img_gen: bool = False, timeout: int = 180
                         ) -> Tuple[int, Any]:
    """Single non-streaming call. Returns (status, data dict or error str).
    On 429 it honors the server's retry window so a single call rides out the
    free-tier cooldown instead of failing immediately."""
    keys = get_api_keys()
    if not keys:
        return 400, {"error": "No GEMINI_API_KEY* found in env or ~/.hermes/.env"}

    gen_cfg: Dict[str, Any] = {}
    if img_gen:
        gen_cfg["responseModalities"] = ["IMAGE", "TEXT"]
    if thinking:
        gen_cfg["thinkingConfig"] = {"thinkingBudget": 8192}
    payload: Dict[str, Any] = {"contents": contents}
    if gen_cfg:
        payload["generationConfig"] = gen_cfg

    def retry_seconds(body_text: str) -> float:
        m = re.search(r"retry in\s+([\d.]+)\s*s", body_text)
        if m:
            return float(m.group(1)) + 1.0
        return 0.0

    deadline = time.time() + max(timeout, 120)
    last_err = ""
    while time.time() < deadline:
        for attempt in range(len(keys)):
            key = keys[attempt % len(keys)]
            url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
                   f"{model}:generateContent?key={key}")
            req = urllib.request.Request(
                url, data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"}, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    return r.status, json.loads(r.read().decode())
            except urllib.error.HTTPError as e:
                body = e.read().decode(errors="replace")
                if e.code in (429, 503):
                    wait = retry_seconds(body) if e.code == 429 else 3.0
                    # Pad generously: free-tier window is a sliding 60s.
                    wait = wait + 15.0 if e.code == 429 else wait + 5.0
                    if time.time() + wait < deadline:
                        time.sleep(wait)
                        break  # retry outer loop with fresh key
                    return e.code, {"error": body[:500]}
                return e.code, {"error": body[:500]}
            except Exception as e:
                last_err = str(e)
                return 0, {"error": last_err}
    return 429, {"error": last_err or "Rate limited (retry window exceeded)"}


def extract_text_and_images(data: Dict) -> Tuple[str, List[Tuple[str, str]]]:
    text_parts: List[str] = []
    images: List[Tuple[str, str]] = []
    for c in data.get("candidates", []):
        for p in c.get("content", {}).get("parts", []):
            if "text" in p:
                text_parts.append(p["text"])
            if "inlineData" in p:
                images.append((p["inlineData"].get("mimeType"), p["inlineData"].get("data")))
    return "\n".join(text_parts).strip(), images


WRITE_RE = re.compile(r"```write:([^\n]+)\n(.*?)```", re.DOTALL)


def handle_write_requests(text: str, out_dir: str) -> Tuple[str, List[str]]:
    """Detect ```write:PATH ...``` fences and persist them. Returns (kept_text, wrote_list)."""
    wrote: List[str] = []
    out_dir = os.path.abspath(out_dir)

    def _do(m):
        rel = m.group(1).strip().lstrip("/")
        content = m.group(2)
        content = content[:-1] if content.endswith("\n") else content
        dest = os.path.join(out_dir, rel)
        os.makedirs(os.path.dirname(dest) or out_dir, exist_ok=True)
        with open(dest, "w", encoding="utf-8") as fh:
            fh.write(content)
        wrote.append(dest)
        return f"[WROTE FILE {dest}]"

    kept = WRITE_RE.sub(_do, text)
    return kept, wrote


def do_image_generation(prompt: str, img_model: str, out_dir: str) -> Tuple[Optional[List[str]], str]:
    contents = [{"role": "user", "parts": [{"text": prompt}]}]
    st, data = call_gemini_generate(img_model, contents, img_gen=True, timeout=240)
    if st != 200:
        err = data.get("error", "unknown") if isinstance(data, dict) else str(data)
        return None, f"image generation failed (HTTP {st}): {err}"
    text, images = extract_text_and_images(data)
    if not images:
        return None, f"image generation returned no image. Model text: {text[:200]}"
    out_dir = os.path.abspath(out_dir)
    os.makedirs(out_dir, exist_ok=True)
    saved: List[str] = []
    for i, (mime, b64) in enumerate(images):
        ext = (mime or "image/png").split("/")[-1].replace("+xml", "")
        if ext == "svg+xml":
            ext = "svg"
        fn = os.path.join(out_dir, f"image_{int(time.time())}_{i}.{ext}")
        with open(fn, "wb") as fh:
            fh.write(base64.b64decode(b64))
        saved.append(fn)
    return saved, text


def which_agy() -> Optional[str]:
    """Return the path to the agy binary if present, else None.

    Prefers `agy` (the wrapper, which sets proxy env vars for geo-bypass).
    Falls back to agy.real if wrapper is absent (e.g. proxy not needed).
    """
    import shutil
    for name in ("agy", "agy.real"):
        p = shutil.which(name)
        if p:
            return p
    return None


def _is_wrapper(agy_path: str) -> bool:
    """Return True if agy_path is the proxy wrapper (not agy.real directly)."""
    return os.path.basename(agy_path) == "agy" and "agy.real" not in agy_path


def _agy_env(agy_path: str) -> dict:
    """Build env dict for agy subprocess.

    If using the wrapper (needs proxy for eligibility bypass), keep proxy vars.
    If using agy.real directly (OAuth path), clear proxy vars.
    """
    env = dict(os.environ)
    if _is_wrapper(agy_path):
        # Wrapper needs proxy vars for eligibility bypass — keep them.
        return env
    # Direct agy.real — clear proxy vars for OAuth path.
    for pv in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY",
               "http_proxy", "https_proxy", "all_proxy"):
        env.pop(pv, None)
    return env


def generate_image_via_agy(prompt: str, out_dir: str,
                           timeout: int = 300) -> Tuple[Optional[List[str]], str]:
    """Image generation via the `agy` CLI using the user's own paid OAuth login.

    The public Gemini REST endpoint requires the `generative-language` OAuth
    scope, which `agy`'s token does not carry (it only has `cloud-platform`).
    Image generation for a paid subscription is exposed through `agy`'s internal
    Cloud Code API (model gemini-3-flash-agent, built-in generate_image tool).
    We shell out to `agy -p` and tell it to save the PNG to an exact path inside
    out_dir, then verify the file landed. No proxy / API key required.
    """
    agy = which_agy()
    if not agy:
        return None, "agy binary not found on PATH (needed for paid-subscription image generation)."
    out_dir = os.path.abspath(os.path.expanduser(out_dir))
    os.makedirs(out_dir, exist_ok=True)
    # Deterministic target so we can verify it was written.
    import random, string
    stamp = int(time.time())
    token = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
    target = os.path.join(out_dir, f"agy_image_{stamp}_{token}.png")
    instruction = (
        "Generate an image based on this description: "
        f"{prompt!r}. "
        "Use the write_to_file tool to save the resulting image to this EXACT absolute "
        f"path: {target}. Do not describe it, do not save anywhere else. When done, "
        "reply with exactly the word DONE."
    )
    env = _agy_env(agy)
    try:
        proc = subprocess.run(
            [agy, "--dangerously-skip-permissions", "-p", instruction],
            env=env, capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return None, f"agy image generation timed out after {timeout}s."
    except Exception as e:
        return None, f"agy image generation failed to launch: {e}"
    if not os.path.isfile(target) or os.path.getsize(target) == 0:
        # Surface agy's stderr/stdout tail for diagnosis.
        diag = (proc.stderr or proc.stdout or "")[-400:]
        return None, f"agy did not write the image to {target}. agy output: {diag}"
    return [target], f"Image generated via agy (paid subscription). Saved to {target}."


def _snapshot_dir(out_dir: str) -> dict:
    """Return {path: (mtime_ns, size)} for every regular file under out_dir.

    Tracking mtime+size (not just the path set) lets the caller detect files
    that were MODIFIED in place — the common case for a coding agent editing
    existing source — not only files that were newly created.
    """
    seen: dict = {}
    if not os.path.isdir(out_dir):
        return seen
    for root, _dirs, files in os.walk(out_dir):
        for fn in files:
            p = os.path.join(root, fn)
            try:
                st = os.stat(p)
                seen[p] = (st.st_mtime_ns, st.st_size)
            except OSError:
                continue
    return seen


def _changed_files(before: dict, after: dict) -> list:
    """Files that were created OR modified (mtime/size changed) between snapshots.

    Excludes tool-generated noise (bytecode caches, pytest/mypy/ruff caches,
    VCS internals) so `written_files` reflects real source/output the agent
    produced, not incidental artifacts from running the code.
    """
    noise = ("__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
             ".git/", "/.tox/", ".hypothesis")
    changed = []
    for p, meta in after.items():
        if p.endswith(".pyc") or any(n in p for n in noise):
            continue
        if p not in before or before[p] != meta:
            changed.append(p)
    return sorted(changed)


def run_via_agy(
    prompt: str,
    read: Optional[List[str]],
    img: Optional[List[str]],
    gen: Optional[str],
    out_dir: str,
    exec: Optional[str] = None,
    timeout: int = 300,
    watch_dirs: Optional[List[str]] = None,
    continue_conv: bool = False,
    conversation_id: Optional[str] = None,
    add_dir: Optional[List[str]] = None,
    mode: Optional[str] = None,
    effort: Optional[str] = None,
) -> Tuple[str, List[str], List[str]]:
    """Drive the `agy` CLI (paid OAuth login) for text, file read, file write,
    image analysis, image generation, and LOCAL CODE EXECUTION — all unified
    behind the one paid auth path.

    `agy` is a coding agent with LOCAL file tools (view_file / write_to_file /
    run_command) and a built-in generate_image tool, all gated by the user's paid
    subscription. We construct one self-contained instruction, shell out to
    `agy -p`, then detect any files it created/wrote by diffing out_dir before
    and after the call.

    `exec` (optional shell command) is appended as a run_command instruction so
    `agy` can run/verify code and capture output. This turns the tool into a
    real coding agent loop (read → write → execute → report), not just a
    text/file wrapper.

    `watch_dirs` are additional directories to diff for created/modified files —
    use when agy may edit files OUTSIDE out_dir (e.g. an existing repo you point
    it at via `read`). The parents of `read` paths are watched automatically.

    Concurrency: the actual `agy` invocation is serialized by a cross-process
    lock, because agy shares one OAuth/browser session.

    Returns (text_reply, written_files, image_files).
    """
    agy = which_agy()
    if not agy:
        return "", [], []
    out_dir = os.path.abspath(os.path.expanduser(out_dir))
    os.makedirs(out_dir, exist_ok=True)

    # Directories to snapshot: out_dir + any explicit watch_dirs + parents of
    # every `read` path (so in-place edits to source outside out_dir are seen).
    watch = {out_dir}
    for wd in (watch_dirs or []):
        watch.add(os.path.abspath(os.path.expanduser(wd)))
    for p in (read or []):
        parent = os.path.dirname(os.path.abspath(os.path.expanduser(p)))
        if parent:
            watch.add(parent)

    # Build the instruction. agy reads text by us inlining it; images it views
    # via its own view_file tool (it understands absolute paths).
    instr_lines = []
    if read:
        for p in read:
            p = os.path.abspath(os.path.expanduser(p))
            if os.path.isfile(p) and os.path.splitext(p)[1].lower() in TEXT_EXTS:
                try:
                    with open(p, "r", encoding="utf-8", errors="replace") as fh:
                        body = fh.read()
                except Exception:
                    body = ""
                instr_lines.append(
                    f"The following file's contents are provided for context "
                    f"(path: {p}):\n<FILE path=\"{p}\">\n{body}\n</FILE>"
                )
            else:
                # Non-text (e.g. binary) or missing — let agy view_file it.
                instr_lines.append(f"Read the file at this absolute path: {p}")
    if img:
        for im in img:
            im = os.path.abspath(os.path.expanduser(im))
            instr_lines.append(
                f"Use the view_file tool on the image at this absolute path and "
                f"analyze it: {im}"
            )
    instr_lines.append(f"USER REQUEST: {prompt}")
    if exec:
        instr_lines.append(
            f"Then use the run_command tool to execute this shell command and "
            f"include its output in your final answer:\n"
            f"```\n{exec}\n```"
        )
    if gen:
        instr_lines.append(
            f"Use the generate_image tool to create an image from this description: "
            f"{gen!r}. Then use write_to_file to save it into this exact directory: "
            f"{out_dir}. When done, reply with exactly the word DONE."
        )
    else:
        instr_lines.append(
            f"If you need to create or modify files, output them using write fences:\n"
            f"```write:relative/path\n<file contents>\n```\n"
            f"The file will be saved to directory: {out_dir}. "
            f"When finished, give your final answer as plain text."
        )
    instruction = "\n\n".join(instr_lines)

    def _snapshot_all():
        merged: dict = {}
        for d in watch:
            merged.update(_snapshot_dir(d))
        return merged

    before = _snapshot_all()
    env = _agy_env(agy)

    # Build agy args with all optional flags
    agy_cmd = [agy, "--dangerously-skip-permissions"]
    if conversation_id:
        agy_cmd.extend(["--conversation", conversation_id])
    elif continue_conv:
        agy_cmd.append("--continue")
    if add_dir:
        for d in add_dir:
            agy_cmd.extend(["--add-dir", os.path.abspath(os.path.expanduser(d))])
    if mode:
        agy_cmd.extend(["--mode", mode])
    if effort:
        agy_cmd.extend(["--effort", effort])
    agy_cmd.extend(["-p", instruction])

    try:
        with _AgyLock(timeout=max(timeout, 60)):
            proc = subprocess.run(
                agy_cmd,
                env=env, capture_output=True, text=True, timeout=timeout,
            )
    except subprocess.TimeoutExpired:
        after = _snapshot_all()
        changed = _changed_files(before, after)
        return (f"agy timed out after {timeout}s.",
                [f for f in changed if not _is_image(f)],
                [f for f in changed if _is_image(f)])
    except Exception as e:
        return (f"agy failed to launch: {e}", [], [])
    text = (proc.stdout or "").strip() or (proc.stderr or "").strip()

    after = _snapshot_all()
    changed = _changed_files(before, after)
    written = [f for f in changed if not _is_image(f)]
    images = [f for f in changed if _is_image(f)]

    # Handle write fences in text output (```write:PATH\n...```)
    # agy -p doesn't execute write_to_file; model emits fenced code blocks instead.
    kept_text, fence_written = handle_write_requests(text, out_dir)
    if fence_written:
        for fw in fence_written:
            if fw not in written:
                written.append(fw)
        text = kept_text

    return text, written, images


def _is_image(path: str) -> bool:
    ext = os.path.splitext(path)[1].lower()
    return ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg")


# Sentinels agy prints when its own agent loop fails (not a tool-usage error).
_AGY_ERROR_SENTINELS = (
    "Agent execution terminated due to error",
    "Error: Agent execution",
    "quota exceeded",
    "RESOURCE_EXHAUSTED",
    "UNAUTHENTICATED",
    "PERMISSION_DENIED",
    "failed to launch",
    "timed out after",
)


def _agy_errored(text: str) -> Optional[str]:
    """Return a short error string if agy's reply signals an internal failure.

    agy returns exit 0 even when its agent loop aborts, burying the failure in
    the reply text. This detects those sentinels so the tool can set
    success=False instead of masquerading a failed run as a successful one.
    """
    if not text:
        return None
    low = text.lower()
    for s in _AGY_ERROR_SENTINELS:
        if s.lower() in low:
            # Return the sentence containing the sentinel for context.
            idx = low.find(s.lower())
            snippet = text[max(0, idx - 20): idx + len(s) + 120].strip()
            return snippet or s
    return None


class _AgyLock:
    """Best-effort cross-process lock around agy invocations.

    agy shares one browser profile / OAuth session, so two concurrent calls can
    corrupt each other. This serializes them via a lock file. Uses fcntl when
    available (POSIX); degrades to a no-op if the platform lacks it so the tool
    still works single-threaded on any OS.
    """

    def __init__(self, timeout: int = 600):
        import tempfile
        self._path = os.path.join(tempfile.gettempdir(), "agyx_agy.lock")
        self._timeout = timeout
        self._fh = None

    def __enter__(self):
        try:
            import fcntl
        except ImportError:
            return self  # no locking available; proceed
        self._fh = open(self._path, "w")
        deadline = time.time() + self._timeout
        while True:
            try:
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except OSError:
                if time.time() >= deadline:
                    # Give up waiting; proceed unlocked rather than hang forever.
                    return self
                time.sleep(0.5)

    def __exit__(self, *exc):
        if self._fh is not None:
            try:
                import fcntl
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
            try:
                self._fh.close()
            except Exception:
                pass
            self._fh = None
        return False


# ---------------------------------------------------------------------------
# Public entry point used by both the Hermes tool handler and the CLI shim.
# ---------------------------------------------------------------------------
def agyx_run(
    prompt: str = "",
    read: Optional[List[str]] = None,
    img: Optional[List[str]] = None,
    model: str = DEFAULT_TEXT_MODEL,
    img_model: str = DEFAULT_IMG_GEN_MODEL,
    gen: Optional[str] = None,
    out_dir: str = "./agyx_out",
    think: bool = False,
    max_iter: int = 12,
    exec: Optional[str] = None,
    timeout: int = 300,
    auto_fix: bool = False,
    verify: Optional[str] = None,
    max_fix_rounds: int = 1,
    watch_dirs: Optional[List[str]] = None,
    continue_conv: bool = False,
    conversation_id: Optional[str] = None,
    add_dir: Optional[List[str]] = None,
    mode: Optional[str] = None,
    effort: Optional[str] = None,
) -> Dict[str, Any]:
    """Run an agyx task. Returns a structured dict (the tool wraps it in JSON).

    PRIMARY PATH (paid subscription): when the `agy` CLI is on PATH and logged
    in, ALL capabilities — text, file read, image analysis, file write, code
    execution, and image generation — are driven through `agy` (its paid OAuth
    login). No Gemini API key required.

    `auto_fix=True` with a `verify` shell command runs a self-healing loop: agy
    writes/executes, the tool runs `verify`; on non-zero exit, agy is called
    again with the failure output and asked to fix. Bounded by `max_fix_rounds`
    (default 1, capped at 3) so it can never loop forever.

    `watch_dirs` extends file-change detection beyond out_dir (e.g. to edit an
    existing repo). Parents of `read` paths are watched automatically.

    The result always carries `elapsed_s`. If agy's reply signals an internal
    failure (quota, auth, agent abort), `success` is set False with `error`.

    FALLBACK PATH: when `agy` is absent but a Gemini API key is present, the
    original direct public-REST logic is used (text/read/write/analysis + image
    gen via a key with image quota).
    """
    _t_start = time.time()
    out_dir = os.path.abspath(os.path.expanduser(out_dir))
    os.makedirs(out_dir, exist_ok=True)
    result: Dict[str, Any] = {"success": True}
    max_fix_rounds = max(0, min(int(max_fix_rounds), 3))  # bounded [0,3]

    # ---- Unified paid-agy path (all capabilities) ----
    if which_agy():
        attempts = []
        # First pass.
        text, written, images = run_via_agy(
            prompt=prompt, read=read, img=img, gen=gen,
            out_dir=out_dir, exec=exec, timeout=timeout, watch_dirs=watch_dirs,
            continue_conv=continue_conv, conversation_id=conversation_id,
            add_dir=add_dir, mode=mode, effort=effort,
        )
        verify_exit = None
        verify_out = ""
        if verify and not gen:
            verify_exit, verify_out = _run_verify(verify, out_dir, timeout)
        attempts.append({"text": text, "written": written, "images": images,
                         "verify_exit": verify_exit, "verify_out": verify_out[:800]})
        # Auto-fix retries on verify failure, bounded by max_fix_rounds.
        if auto_fix and verify and not gen:
            fix_round = 0
            while verify_exit not in (None, 0) and fix_round < max_fix_rounds:
                fix_round += 1
                fix_prompt = (
                    f"The previous attempt failed verification (fix attempt "
                    f"{fix_round}/{max_fix_rounds}). The verify command "
                    f"`{verify}` exited with code {verify_exit}. Its output:\n"
                    f"{verify_out}\n\nPlease fix the code/files so the verify "
                    f"command passes, then report the final result."
                )
                text, written, images = run_via_agy(
                    prompt=fix_prompt, read=read, img=img, gen=None,
                    out_dir=out_dir, exec=exec, timeout=timeout, watch_dirs=watch_dirs,
                    add_dir=add_dir, mode=mode, effort=effort,
                )
                verify_exit, verify_out = _run_verify(verify, out_dir, timeout)
                attempts.append({"text": text, "written": written, "images": images,
                                 "verify_exit": verify_exit, "verify_out": verify_out[:800]})
            if fix_round > 0:
                result["auto_fixed"] = verify_exit == 0
                result["fix_rounds"] = fix_round

        if gen:
            if images:
                result["images"] = images
            else:
                result["success"] = False
                result["error"] = (
                    "agy did not produce an image. Reply was: "
                    + (text[:300] or "(empty)")
                )
                result["elapsed_s"] = round(time.time() - _t_start, 1)
                return result
        if text:
            result["text"] = text
        if written:
            result["written_files"] = written
        if images:
            result.setdefault("images", images)
        if continue_conv or conversation_id:
            result["conversation_continued"] = True
        if verify is not None:
            result["verify_exit"] = verify_exit
            if verify_out:
                result["verify_output"] = verify_out[:800]
        if attempts:
            result["rounds"] = len(attempts)

        # Detect agy internal failures buried in an exit-0 reply.
        agy_err = _agy_errored(text)
        if agy_err and not written and not result.get("images"):
            result["success"] = False
            result["error"] = f"agy internal failure: {agy_err}"
        # A verify that still fails after all rounds is not a success.
        if verify is not None and verify_exit not in (None, 0):
            result["success"] = False
            result.setdefault("error",
                              f"verify still failing (exit {verify_exit}) "
                              f"after {result.get('fix_rounds', 0)} fix round(s).")
        if not text and not written and not result.get("images"):
            result["success"] = False
            result["error"] = result.get("error", "agy returned no output.")
        result["elapsed_s"] = round(time.time() - _t_start, 1)
        return result

    # ---- Fallback: direct public Gemini REST (requires GEMINI_API_KEY) ----
    if not get_api_keys():
        result["success"] = False
        result["error"] = ("Neither `agy` (paid OAuth) nor a GEMINI_API_KEY is "
                           "available. Install/authenticate `agy` for the full "
                           "feature set, or set a Gemini API key for the "
                           "key-only fallback.")
        return result

    # Pure image generation mode (key fallback)
    if gen:
        saved, info = do_image_generation(gen, img_model, out_dir)
        if saved:
            result["images"] = saved
            if info:
                result["note"] = info
        else:
            result["success"] = False
            result["error"] = info
        return result

    # Build initial contents
    contents: List[Dict] = []
    sys_text = (
        "You are a coding assistant with LOCAL file tools. "
        "To save a file, reply with a fenced block exactly like:\n"
        "```write:relative/path.txt\n<contents>\n```\n"
        "Only use write: for files you are explicitly asked to create or modify. "
        "For reading, the user's attached <FILE> blocks are already in context."
    )
    user_parts: List[Dict] = []
    for p in (read or []):
        user_parts.append(read_file_part(os.path.expanduser(p)))
    for im in (img or []):
        user_parts.append(read_file_part(os.path.expanduser(im)))
    user_parts.append({"text": f"{sys_text}\n\nUSER REQUEST:\n{prompt}"})
    contents.append({"role": "user", "parts": user_parts})

    wrote_any: List[str] = []
    final_text: str = ""
    for _ in range(max_iter):
        st, data = call_gemini_generate(model, contents, thinking=think, timeout=180)
        if st != 200:
            err = data.get("error", "unknown") if isinstance(data, dict) else str(data)
            result["success"] = False
            result["error"] = f"Gemini HTTP {st}: {err}"
            return result
        text, images = extract_text_and_images(data)
        if images and not img:
            for i, (mime, b64) in enumerate(images):
                ext = (mime or "image/png").split("/")[-1]
                fn = os.path.join(out_dir, f"image_{int(time.time())}_{i}.{ext}")
                os.makedirs(out_dir, exist_ok=True)
                with open(fn, "wb") as fh:
                    fh.write(base64.b64decode(b64))
                result.setdefault("images", []).append(fn)
        kept, wrote = handle_write_requests(text, out_dir)
        wrote_any.extend(wrote)
        if wrote:
            result.setdefault("written_files", []).extend(wrote)
        if not wrote:
            final_text = kept
            break
        contents.append({"role": "model", "parts": [{"text": text}]})
        contents.append({"role": "user", "parts": [
            {"text": "Files written. Continue: if the task is complete, give the final answer; "
                     "otherwise continue editing or create more files."}]})
    result["text"] = final_text
    if not final_text and not result.get("written_files") and not result.get("images"):
        result["success"] = False
        result["error"] = result.get("error", "No response produced (max iterations?).")
    return result


def _run_verify(verify: str, out_dir: str, timeout: int) -> Tuple[int, str]:
    """Run a user-supplied verification shell command under out_dir.

    Returns (exit_code, combined_output). Never raises; failures are reported
    as exit code -1 with the error text. The command is run by the tool itself
    (bounded by `timeout`) — this is the one place agyx executes a shell, and
    only when the caller explicitly passes `verify`.
    """
    env = dict(os.environ)
    for pv in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        env.pop(pv, None)
    try:
        proc = subprocess.run(
            verify, shell=True, cwd=out_dir, env=env,
            capture_output=True, text=True, timeout=min(timeout, 120),
        )
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except subprocess.TimeoutExpired:
        return -1, f"verify timed out after {min(timeout, 120)}s"
    except Exception as e:  # noqa: BLE001 - surface any launch error to the agent
        return -1, f"verify failed to launch: {e}"


# ---------------------------------------------------------------------------
# Hermes tool handler + registration
# ---------------------------------------------------------------------------
def _handle_agyx(args: Dict[str, Any], **kwargs) -> str:
    try:
        res = agyx_run(
            prompt=args.get("prompt", ""),
            read=args.get("read"),
            img=args.get("img"),
            model=args.get("model", DEFAULT_TEXT_MODEL),
            img_model=args.get("img_model", DEFAULT_IMG_GEN_MODEL),
            gen=args.get("gen"),
            out_dir=args.get("out_dir", "./agyx_out"),
            think=bool(args.get("think", False)),
            max_iter=int(args.get("max_iter", 12)),
            exec=args.get("exec"),
            timeout=int(args.get("timeout", 300)),
            auto_fix=bool(args.get("auto_fix", False)),
            verify=args.get("verify"),
            max_fix_rounds=int(args.get("max_fix_rounds", 1)),
            watch_dirs=args.get("watch_dirs"),
            continue_conv=bool(args.get("continue_conv", False)),
            conversation_id=args.get("conversation_id"),
            add_dir=args.get("add_dir"),
            mode=args.get("mode"),
            effort=args.get("effort"),
        )
        return json.dumps(res, ensure_ascii=False)
    except Exception as e:
        return tool_error(f"agyx failed: {e}", success=False)


AGYX_SCHEMA = {
    "name": "agyx",
    "description": (
        "AGY (Google Antigravity/Code Assist) bypass — a coding agent backed by "
        "your paid agy OAuth login. Use it to: (1) READ local files into context "
        "(read), (2) ANALYZE local images (img), (3) WRITE local files, (4) RUN "
        "local shell commands to build/execute/verify code (exec), and (5) "
        "GENERATE images from a text prompt (gen). ALL capabilities run through "
        "the agy CLI using your paid subscription — no Gemini API key required. "
        "With auto_fix=True and a verify command, it self-heals: agy writes + the "
        "tool runs verify; on failure agy is asked to fix once. Falls back to a "
        "direct Gemini API key only when agy is not installed. Output is compact "
        "JSON with the text answer, written_files, images, and verify_exit."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": "The task / question. For generation use the 'gen' arg instead.",
            },
            "read": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Local file path(s) to read into context (text inlined; "
                               "non-text/binary handed to agy's view_file).",
            },
            "img": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Local image path(s) for agy to analyze via view_file.",
            },
            "gen": {
                "type": "string",
                "description": "If set, generate an image from this prompt via your paid agy "
                               "subscription and save it to out_dir. Returns the saved image path(s).",
            },
            "exec": {
                "type": "string",
                "description": "Shell command for agy to run via its run_command tool "
                               "(e.g. 'python3 solution.py'). Output is included in the reply. "
                               "Use for build/run/verify steps.",
            },
            "verify": {
                "type": "string",
                "description": "Shell command the tool runs itself after agy finishes, to "
                               "check success (non-zero exit = failure). Requires auto_fix "
                               "for the retry loop. Bounded to min(timeout,120)s. Only "
                               "runs if you pass it explicitly. If verify still fails after "
                               "all fix rounds, success=False is returned (with verify_exit "
                               "and the last output).",
            },
            "auto_fix": {
                "type": "boolean",
                "description": "If true and verify fails, repeatedly ask agy to fix until "
                               "verify passes or max_fix_rounds is reached. Bounded: default "
                               "1 retry, cap 3 (no unbounded loop).",
            },
            "max_fix_rounds": {
                "type": "integer",
                "description": "Max auto-fix retries when auto_fix=True (default 1, hard cap 3). "
                               "The self-healing loop never exceeds this.",
            },
            "watch_dirs": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Extra directories to watch for created/modified files, beyond "
                               "out_dir. Parents of every 'read' path are watched automatically. "
                               "Use this when agy edits an existing repo outside out_dir.",
            },
            "timeout": {
                "type": "integer",
                "description": "Per-agy-call timeout in seconds (default 300). Each agy "
                               "invocation (incl. auto-fix retry) is bounded by this.",
            },
            "model": {
                "type": "string",
                "description": f"Text model used only by the key-only fallback (default {DEFAULT_TEXT_MODEL}). "
                               "Ignored when agy (paid OAuth) is available.",
            },
            "img_model": {
                "type": "string",
                "description": f"Image-generation model used only by the key-only fallback "
                               f"(default {DEFAULT_IMG_GEN_MODEL}). Ignored when agy is available.",
            },
            "out_dir": {
                "type": "string",
                "description": "Directory for generated/written files (default ./agyx_out).",
            },
            "think": {
                "type": "boolean",
                "description": "Enable Gemini thinking (Gemini 2.5+ only).",
            },
            "max_iter": {
                "type": "integer",
                "description": "Tool-loop iteration cap (default 12).",
            },
            "continue_conv": {
                "type": "boolean",
                "description": "Continue the most recent agy conversation (-c). Use for multi-turn tasks.",
            },
            "conversation_id": {
                "type": "string",
                "description": "Resume a specific agy conversation by ID (--conversation).",
            },
            "add_dir": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Add workspace directories (--add-dir). agy reads these for project context.",
            },
            "mode": {
                "type": "string",
                "enum": ["accept-edits", "plan"],
                "description": "Agent execution mode: accept-edits (auto-apply changes) or plan (plan only).",
            },
            "effort": {
                "type": "string",
                "enum": ["low", "medium", "high"],
                "description": "Reasoning effort level for the current session.",
            },
        },
        "required": ["prompt"],
    },
}


# NOTE: registration happens in `agyx_plugin/__init__.py` via
# `ctx.register_tool(...)` — do NOT call registry.register() here, so this
# module stays importable without a full Hermes core import and the tool is
# registered exactly once (by the plugin loader).
