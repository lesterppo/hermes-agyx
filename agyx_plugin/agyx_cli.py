#!/usr/bin/env python3
"""agyx — Hermes-native AGY bypass CLI wrapper (standalone, plugin-shipped).

Thin shim over `agyx_plugin.agyx_tool.agyx_run`. The real implementation lives
in `agyx_plugin/agyx_tool.py` (single source of truth). Works whether run from
the repo root or installed as a package.

Usage:
  python -m agyx_plugin.agyx_cli "prompt" [--read PATH] [--img PATH]
        [--gen "prompt"] [--out-dir DIR] [--verify CMD] [--auto-fix]
"""
import argparse
import json
import os
import sys

# Make the local package importable if run as a bare script.
_HERE = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)

try:
    from agyx_plugin.agyx_tool import (
        agyx_run,
        DEFAULT_TEXT_MODEL,
        DEFAULT_IMG_GEN_MODEL,
    )
except Exception as e:  # pragma: no cover
    sys.stderr.write(
        f"[agyx] Could not import agyx_plugin.agyx_tool: {e}\n"
        f"[agyx] The native Hermes tool (after installing this plugin) is the "
        f"primary interface; this CLI is optional.\n"
    )
    sys.exit(1)


def main():
    ap = argparse.ArgumentParser(description="Hermes-native AGY bypass (file+image+img-gen).")
    ap.add_argument("prompt", help="The user prompt / task (use 'unused' for --gen only).")
    ap.add_argument("--img", action="append", default=[], help="Local image to analyze (repeatable).")
    ap.add_argument("--read", action="append", default=[], help="Local file to read into context (repeatable).")
    ap.add_argument("--model", default=DEFAULT_TEXT_MODEL, help="Text/multimodal model.")
    ap.add_argument("--img-model", default=DEFAULT_IMG_GEN_MODEL, help="Image-generation model.")
    ap.add_argument("--gen", default=None, help="Generate an image from this prompt.")
    ap.add_argument("--out-dir", default="./agyx_out", help="Output dir for generated/written files.")
    ap.add_argument("--exec", default=None, help="Shell command for agy to run via run_command.")
    ap.add_argument("--verify", default=None, help="Shell verify command the tool runs after agy.")
    ap.add_argument("--auto-fix", action="store_true", help="Self-heal when verify fails (bounded).")
    ap.add_argument("--max-fix-rounds", type=int, default=1, help="Max auto-fix retries (cap 3).")
    ap.add_argument("--watch-dirs", action="append", default=[], help="Extra dirs to watch for edits.")
    ap.add_argument("--timeout", type=int, default=300, help="Per-agy-call timeout (s).")
    ap.add_argument("--think", action="store_true", help="Enable Gemini thinking.")
    ap.add_argument("--max-iter", type=int, default=12, help="Tool-loop iteration cap.")
    args = ap.parse_args()
    try:
        res = agyx_run(
            prompt=args.prompt,
            read=args.read or None,
            img=args.img or None,
            model=args.model,
            img_model=args.img_model,
            gen=args.gen,
            out_dir=args.out_dir,
            exec=args.exec,
            verify=args.verify,
            auto_fix=args.auto_fix,
            max_fix_rounds=args.max_fix_rounds,
            watch_dirs=args.watch_dirs or None,
            timeout=args.timeout,
            think=args.think,
            max_iter=args.max_iter,
        )
        print(json.dumps(res, ensure_ascii=False, indent=2))
        sys.exit(0 if res.get("success", True) else 1)
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
