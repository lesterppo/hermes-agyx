"""Regression tests for the agyx Hermes-native tool (tools/agyx_tool.py).

NETWORK-FREE. These mock:
  - tools.agyx_tool.run_via_agy   -> the unified paid-agy path (shells out to agy)
  - tools.agyx_tool.call_gemini_generate -> the key-only fallback
  - tools.agyx_tool.subprocess.run -> agy process launch

They lock in the routing decision: when `agy` is on PATH, EVERY capability
(text, file read, image analysis, file write, image generation) is driven
through `agy` (paid OAuth). The direct public Gemini REST call is only a
fallback when `agy` is absent and a GEMINI_API_KEY exists.

Run: scripts/run_tests.sh tests/tools/test_agyx_tool.py -q
"""

import importlib
import os
from unittest import mock

import tools.agyx_tool as m
from tools.agyx_tool import agyx_run, run_via_agy, which_agy, check_agyx_requirements


class TestRunViaAgyUnified:
    """run_via_agy is the single backend for all capabilities when agy is present."""

    def test_returns_text_written_and_images(self, tmp_path):
        out = str(tmp_path / "out")
        os.makedirs(out, exist_ok=True)
        created = [os.path.join(out, "note.txt"), os.path.join(out, "pic.png")]

        def fake_run_via_agy(prompt, read, img, gen, out_dir, exec=None, timeout=300, watch_dirs=None):
            # Simulate agy having written a file + an image into out_dir.
            for c in created:
                with open(c, "wb") as fh:
                    fh.write(b"x" * 16)
            return "final answer", [created[0]], [created[1]]

        with mock.patch.object(m, "which_agy", return_value="/bin/agy"), \
             mock.patch.object(m, "run_via_agy", side_effect=fake_run_via_agy):
            res = agyx_run(prompt="do the thing", read=["/x.py"], img=["/y.png"],
                           out_dir=out)
        assert res["success"] is True
        assert res["text"] == "final answer"
        assert res["written_files"] == [created[0]]
        assert res["images"] == [created[1]]

    def test_gen_mode_requires_image_output(self, tmp_path):
        out = str(tmp_path / "out")
        os.makedirs(out, exist_ok=True)

        def fake_run_via_agy(prompt, read, img, gen, out_dir, exec=None, timeout=300, watch_dirs=None):
            # agy produced no image -> gen must report failure.
            return "I could not generate that.", [], []

        with mock.patch.object(m, "which_agy", return_value="/bin/agy"), \
             mock.patch.object(m, "run_via_agy", side_effect=fake_run_via_agy):
            res = agyx_run(gen="a cat", out_dir=out)
        assert res["success"] is False
        assert "image" in res["error"].lower()

    def test_gen_mode_success(self, tmp_path):
        out = str(tmp_path / "out")
        os.makedirs(out, exist_ok=True)
        cat_img = os.path.join(out, "cat.png")

        def fake_run_via_agy(prompt, read, img, gen, out_dir, exec=None, timeout=300, watch_dirs=None):
            with open(cat_img, "wb") as fh:
                fh.write(b"\x89PNG\r\n\x1a\n")
            return "DONE", [], [cat_img]

        with mock.patch.object(m, "which_agy", return_value="/bin/agy"), \
             mock.patch.object(m, "run_via_agy", side_effect=fake_run_via_agy):
            res = agyx_run(gen="a cat", out_dir=out)
        assert res["success"] is True
        assert res["images"] == [cat_img]


class TestFallbackKeyOnly:
    """When agy is absent but a key exists, the direct Gemini REST path is used."""

    def test_no_agy_no_key_errors(self):
        with mock.patch.object(m, "which_agy", return_value=None), \
             mock.patch.object(m, "get_api_keys", return_value=[]):
            res = agyx_run(prompt="hi", out_dir="/tmp/x")
        assert res["success"] is False
        assert "agy" in res["error"].lower() or "key" in res["error"].lower()

    def test_text_falls_back_to_gemini(self, tmp_path):
        out = str(tmp_path / "out")
        fake_resp = (200, {"candidates": [{"content": {"parts": [{"text": "hi there"}]}}]})
        with mock.patch.object(m, "which_agy", return_value=None), \
             mock.patch.object(m, "get_api_keys", return_value=["FAKE"]), \
             mock.patch.object(m, "call_gemini_generate", return_value=fake_resp):
            res = agyx_run(prompt="hi", out_dir=out)
        assert res["success"] is True
        assert res["text"] == "hi there"

    def test_gen_falls_back_to_gemini_image(self, tmp_path):
        out = str(tmp_path / "out")
        png = "iVBORw0KGgo="
        fake_resp = (200, {"candidates": [{"content": {
            "parts": [{"inlineData": {"mimeType": "image/png", "data": png}}]}}]})
        with mock.patch.object(m, "which_agy", return_value=None), \
             mock.patch.object(m, "get_api_keys", return_value=["FAKE"]), \
             mock.patch.object(m, "call_gemini_generate", return_value=fake_resp):
            res = agyx_run(gen="a cat", out_dir=out)
        assert res["success"] is True
        assert res["images"] and res["images"][0].endswith(".png")


class TestRunViaAgyDirDiff:
    """run_via_agy detects written/image files by diffing out_dir."""

    def test_detects_created_files(self, tmp_path):
        out = str(tmp_path / "out")
        # Pre-seed before-call state by writing into out_dir via the fake runner.
        captured = {}

        def fake_run(cmd, **kwargs):
            # Create files in out_dir to simulate agy having written them.
            os.makedirs(out, exist_ok=True)
            txt = os.path.join(out, "readme.txt")
            png = os.path.join(out, "fig.png")
            with open(txt, "w") as fh:
                fh.write("hello")
            with open(png, "wb") as fh:
                fh.write(b"\x89PNG\r\n\x1a\n")
            return mock.Mock(returncode=0, stdout="done", stderr="")

        with mock.patch.object(m, "which_agy", return_value="/bin/agy"), \
             mock.patch("tools.agyx_tool.subprocess.run", side_effect=fake_run), \
             mock.patch.object(m, "read_file_part", return_value={"text": "ctx"}):
            text, written, images = run_via_agy(
                prompt="make files", read=None, img=None, gen=None, out_dir=out)
        assert "readme.txt" in written[0]
        assert "fig.png" in images[0]


class TestCheckFn:
    """Availability gate prefers agy, falls back to a key."""

    def test_agy_present(self):
        with mock.patch.object(m, "which_agy", return_value="/bin/agy"), \
             mock.patch.object(m, "get_api_keys", return_value=[]):
            assert check_agyx_requirements() is True

    def test_key_only(self):
        with mock.patch.object(m, "which_agy", return_value=None), \
             mock.patch.object(m, "get_api_keys", return_value=["FAKE"]):
            assert check_agyx_requirements() is True

    def test_neither(self):
        with mock.patch.object(m, "which_agy", return_value=None), \
             mock.patch.object(m, "get_api_keys", return_value=[]):
            assert check_agyx_requirements() is False


class TestRegistration:
    """Tool must be registered in the agy toolset."""

    def test_registered(self):
        entry = m.registry.get_entry("agyx")
        assert entry is not None
        assert entry.toolset == "agy"
        assert callable(entry.check_fn)

    def test_schema_has_new_params(self):
        props = m.AGYX_SCHEMA["parameters"]["properties"]
        for p in ("gen", "read", "img", "exec", "verify", "auto_fix", "timeout"):
            assert p in props, f"missing schema param {p}"


class TestExecAndVerify:
    """exec / verify / auto_fix expand the tool into a self-healing coding loop."""

    def test_exec_passed_to_run_via_agy(self, tmp_path):
        out = str(tmp_path / "out")
        captured = {}

        def fake_run_via_agy(prompt, read, img, gen, out_dir, exec=None, timeout=300, watch_dirs=None):
            captured["exec"] = exec
            return "ran ok", [], []

        with mock.patch.object(m, "which_agy", return_value="/bin/agy"), \
             mock.patch.object(m, "run_via_agy", side_effect=fake_run_via_agy):
            res = agyx_run(prompt="build it", exec="python3 x.py", out_dir=out)
        assert res["success"] is True
        assert captured["exec"] == "python3 x.py"

    def test_verify_reports_exit_code(self, tmp_path):
        out = str(tmp_path / "out")
        with mock.patch.object(m, "which_agy", return_value="/bin/agy"), \
             mock.patch.object(m, "run_via_agy", return_value=("ok", [], [])), \
             mock.patch.object(m, "_run_verify", return_value=(0, "pass")):
            res = agyx_run(prompt="x", verify="true", out_dir=out)
        assert res["verify_exit"] == 0
        assert res["rounds"] == 1

    def test_auto_fix_retries_on_verify_failure(self, tmp_path):
        out = str(tmp_path / "out")
        calls = {"n": 0}

        def fake_run_via_agy(prompt, read, img, gen, out_dir, exec=None, timeout=300, watch_dirs=None):
            calls["n"] += 1
            # first pass "fails" (verify will be non-zero), second "fixes"
            return ("attempt", [], []) if calls["n"] == 1 else ("fixed", [], [])

        verify_results = [(1, "boom"), (0, "ok")]

        def fake_verify(cmd, od, timeout):
            return verify_results.pop(0)

        with mock.patch.object(m, "which_agy", return_value="/bin/agy"), \
             mock.patch.object(m, "run_via_agy", side_effect=fake_run_via_agy), \
             mock.patch.object(m, "_run_verify", side_effect=fake_verify):
            res = agyx_run(prompt="x", verify="check", auto_fix=True, out_dir=out)
        assert calls["n"] == 2, "should have retried once"
        assert res["auto_fixed"] is True
        assert res["rounds"] == 2
        assert res["verify_exit"] == 0

    def test_no_autofix_on_verify_failure_is_single_pass(self, tmp_path):
        out = str(tmp_path / "out")
        with mock.patch.object(m, "which_agy", return_value="/bin/agy"), \
             mock.patch.object(m, "run_via_agy", return_value=("ok", [], [])), \
             mock.patch.object(m, "_run_verify", return_value=(2, "fail")):
            res = agyx_run(prompt="x", verify="check", auto_fix=False, out_dir=out)
        assert res["rounds"] == 1
        assert res["verify_exit"] == 2
        assert "auto_fixed" not in res

    def test_run_verify_runs_command_and_returns_exit(self, tmp_path):
        # _run_verify actually executes a shell command (network-free safe ones).
        code, outp = m._run_verify("echo hello", str(tmp_path), 60)
        assert code == 0 and "hello" in outp
        code2, _ = m._run_verify("exit 3", str(tmp_path), 60)
        assert code2 == 3

    def test_auto_fix_respects_max_fix_rounds_cap(self, tmp_path):
        out = str(tmp_path / "out")

        def fake_run_via_agy(prompt, read, img, gen, out_dir, exec=None,
                             timeout=300, watch_dirs=None):
            return ("still failing", [], [])

        calls = {"n": 0}

        def fake_verify(cmd, od, timeout):
            calls["n"] += 1
            return (1, "boom")  # always fails

        with mock.patch.object(m, "which_agy", return_value="/bin/agy"), \
             mock.patch.object(m, "run_via_agy", side_effect=fake_run_via_agy), \
             mock.patch.object(m, "_run_verify", side_effect=fake_verify):
            res = agyx_run(prompt="x", verify="check", auto_fix=True,
                           max_fix_rounds=3, out_dir=out)
        # 1 initial + 3 retries = 4 total; never more than cap+1
        assert calls["n"] == 4
        assert res["rounds"] == 4
        assert res["fix_rounds"] == 3
        assert res["auto_fixed"] is False
        assert res["success"] is False
        assert "verify still failing" in res["error"]

    def test_max_fix_rounds_hard_capped_at_three(self, tmp_path):
        out = str(tmp_path / "out")

        def fake_run_via_agy(prompt, read, img, gen, out_dir, exec=None,
                             timeout=300, watch_dirs=None):
            return ("failing", [], [])

        calls = {"n": 0}

        def fake_verify(cmd, od, timeout):
            calls["n"] += 1
            return (1, "boom")

        with mock.patch.object(m, "which_agy", return_value="/bin/agy"), \
             mock.patch.object(m, "run_via_agy", side_effect=fake_run_via_agy), \
             mock.patch.object(m, "_run_verify", side_effect=fake_verify):
            agyx_run(prompt="x", verify="check", auto_fix=True,
                     max_fix_rounds=99, out_dir=out)
        assert calls["n"] == 4  # 1 + min(99,3)

    def test_agy_internal_error_sets_success_false(self, tmp_path):
        out = str(tmp_path / "out")
        # agy returns exit 0 but its reply text signals an aborted agent loop.
        err_reply = "Error: Agent execution terminated due to error."

        def fake_run_via_agy(prompt, read, img, gen, out_dir, exec=None,
                             timeout=300, watch_dirs=None):
            return (err_reply, [], [])

        with mock.patch.object(m, "which_agy", return_value="/bin/agy"), \
             mock.patch.object(m, "run_via_agy", side_effect=fake_run_via_agy), \
             mock.patch.object(m, "_run_verify", return_value=(None, "")):
            res = agyx_run(prompt="x", out_dir=out)
        assert res["success"] is False
        assert "agy internal failure" in res["error"]

    def test_result_always_carries_elapsed_s(self, tmp_path):
        out = str(tmp_path / "out")
        with mock.patch.object(m, "which_agy", return_value="/bin/agy"), \
             mock.patch.object(m, "run_via_agy", return_value=("ok", [], [])):
            res = agyx_run(prompt="x", out_dir=out)
        assert "elapsed_s" in res and isinstance(res["elapsed_s"], float)

    def test_watch_dirs_passed_through_to_run_via_agy(self, tmp_path):
        # watch_dirs (and read-path parents) must reach run_via_agy so edits
        # outside out_dir are detected.
        out = str(tmp_path / "out")
        seen = {}

        def fake_run_via_agy(prompt, read, img, gen, out_dir, exec=None,
                             timeout=300, watch_dirs=None):
            seen["watch_dirs"] = watch_dirs
            seen["read"] = read
            return ("ok", [], [])

        with mock.patch.object(m, "which_agy", return_value="/bin/agy"), \
             mock.patch.object(m, "run_via_agy", side_effect=fake_run_via_agy):
            agyx_run(prompt="x", out_dir=out, watch_dirs=["/tmp/elsewhere"],
                     read=["/srv/app/main.py"])
        assert seen["watch_dirs"] == ["/tmp/elsewhere"]
        assert seen["read"] == ["/srv/app/main.py"]


class TestChangedFiles:
    """_changed_files must catch created AND modified files, minus tool noise."""

    def test_detects_new_and_modified_not_noise(self, tmp_path):
        d = tmp_path
        src = d / "mod.py"
        src.write_text("x = 1\n")
        before = m._snapshot_dir(str(d))
        # modify existing + create new + create noise
        import time as _t
        _t.sleep(0.01)
        src.write_text("x = 2  # changed\n")
        (d / "new.py").write_text("y = 3\n")
        cache = d / "__pycache__"
        cache.mkdir()
        (cache / "mod.cpython-312.pyc").write_bytes(b"\x00")
        pc = d / ".pytest_cache"
        pc.mkdir()
        (pc / "lastfailed").write_text("{}")
        after = m._snapshot_dir(str(d))
        changed = m._changed_files(before, after)
        assert str(src) in changed, "in-place modification must be detected"
        assert str(d / "new.py") in changed, "new file must be detected"
        assert not any("__pycache__" in c or ".pytest_cache" in c or c.endswith(".pyc")
                       for c in changed), "tool noise must be filtered"

    def test_snapshot_is_dict_with_mtime_size(self, tmp_path):
        (tmp_path / "a.txt").write_text("hi")
        snap = m._snapshot_dir(str(tmp_path))
        assert isinstance(snap, dict)
        (meta,) = snap.values()
        assert len(meta) == 2  # (mtime_ns, size)


def test_module_imports_clean():
    importlib.reload(m)
    assert hasattr(m, "agyx_run") and hasattr(m, "run_via_agy")
