"""lib/serve.py: the start-up check, the request and model guards, the watchdog and the wiring."""

import contextlib
import io
import sys
import tempfile
import types
import unittest

from helpers import REPO
from test_memory import KAT

sys.path.insert(0, str(REPO / "lib"))
import memory  # noqa: E402
import serve  # noqa: E402

GIB = memory.GIB
MIB = memory.MIB
WEIGHTS = int(11.41 * GIB)
GPU = 16 * GIB
MARGIN = 512 * MIB


def args(**overrides):
    values = dict(
        context_tokens=262144, kv_bits=8, cache_entries=0, margin_bytes=MARGIN,
        max_prefill_step_tokens=512, state_dir="/repo/state", disk_path="/repo",
    )
    values.update(overrides)
    return types.SimpleNamespace(**values)


class Patched(unittest.TestCase):
    """Replaces the memory and disk readings for one test, and records the disk paths read."""

    def setUp(self):
        self.available = 30 * GIB
        self.disk = 100 * GIB
        self.disk_paths = []
        originals = (memory.available_system_bytes, memory.free_disk_bytes)

        def disk(path):
            self.disk_paths.append(path)
            return self.disk

        memory.available_system_bytes = lambda: self.available
        memory.free_disk_bytes = disk
        self.addCleanup(lambda: setattr(memory, "available_system_bytes", originals[0]))
        self.addCleanup(lambda: setattr(memory, "free_disk_bytes", originals[1]))

    def startup(self, **overrides):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                serve.check_startup(args(**overrides), KAT, WEIGHTS, GPU)
                code = 0
            except SystemExit as e:
                code = e.code
        return code, out.getvalue(), err.getvalue()


class StartupTest(Patched):
    def test_starts_and_says_how_much_of_the_window_fits(self):
        code, out, _ = self.startup()
        self.assertEqual(code, 0)
        fits = memory.max_tokens_fitting(KAT, 8, GPU - WEIGHTS - MARGIN, 512, 262144)
        self.assertIn(f"GPU limit; {fits} tokens fit within it", out)
        self.assertIn(f"so the agents are told {fits} tokens and longer requests are refused", out)
        self.assertEqual(self.disk_paths, ["/repo"])

    def test_no_note_when_the_whole_window_fits(self):
        code, out, _ = self.startup(context_tokens=131072)
        self.assertEqual(code, 0)
        self.assertNotIn("longer requests are refused", out)

    def test_refuses_when_not_even_the_minimum_fits_the_gpu(self):
        code, _, err = self.startup(cache_entries=40)
        self.assertEqual(code, 1)
        self.assertRegex(err, r"not even 32768 tokens of context fit in the [\d.]+ GiB the GPU may use")

    def test_refuses_when_too_little_memory_is_free(self):
        self.available = 13 * GIB
        code, _, err = self.startup()
        self.assertEqual(code, 1)
        self.assertIn("Close other applications until about", err)

    def test_free_memory_boundary(self):
        room = memory.request_bytes(KAT, serve.MIN_START_TOKENS, 16, 8)
        self.available = WEIGHTS + MARGIN + memory.SYSTEM_RESERVE_BYTES + room
        self.assertEqual(self.startup()[0], 0)
        self.available -= 1
        self.assertEqual(self.startup()[0], 1)

    def test_short_context_needs_less(self):
        just_enough = WEIGHTS + MARGIN + memory.SYSTEM_RESERVE_BYTES + memory.request_bytes(KAT, 8192, 16, 8)
        self.available = just_enough
        self.assertEqual(self.startup()[0], 1)
        self.assertEqual(self.startup(context_tokens=8192)[0], 0)
        self.available = just_enough - 1
        self.assertEqual(self.startup(context_tokens=8192)[0], 1)

    def test_disk_boundary(self):
        self.disk = memory.DISK_RESERVE_BYTES
        self.assertEqual(self.startup()[0], 0)
        self.disk -= 1
        code, _, err = self.startup()
        self.assertEqual(code, 1)
        self.assertIn("of disk is free", err)


class RequestGuardTest(Patched):
    def setUp(self):
        super().setUp()
        self.original = serve.server.ResponseGenerator._tokenize
        self.addCleanup(lambda: setattr(serve.server.ResponseGenerator, "_tokenize", self.original))
        serve.server.ResponseGenerator._tokenize = lambda self, tok, request, gen_args: ([0] * request, "rest")
        self.active = WEIGHTS
        self.cached = 0
        originals = (serve.mx.get_active_memory, serve.mx.get_cache_memory)
        serve.mx.get_active_memory = lambda: self.active
        serve.mx.get_cache_memory = lambda: self.cached
        self.addCleanup(lambda: setattr(serve.mx, "get_active_memory", originals[0]))
        self.addCleanup(lambda: setattr(serve.mx, "get_cache_memory", originals[1]))
        serve.guard_requests(args(), KAT, GPU)

    def request(self, prompt_tokens, max_tokens=8192):
        generator = types.SimpleNamespace(cli_args=types.SimpleNamespace(prefill_step_size=512))
        result = serve.server.ResponseGenerator._tokenize(
            generator, None, prompt_tokens, types.SimpleNamespace(max_tokens=max_tokens)
        )
        return result, generator.cli_args.prefill_step_size

    def test_short_request_uses_the_full_step(self):
        result, step = self.request(1000)
        self.assertEqual(result, ([0] * 1000, "rest"))
        self.assertEqual(step, 512)

    def test_long_request_gets_a_smaller_step(self):
        _, step = self.request(200000)
        self.assertLess(step, 512)
        self.assertLessEqual(memory.request_bytes(KAT, 208192, step, 8), GPU - WEIGHTS - MARGIN)

    def test_requests_beyond_the_gpu_limit_are_refused(self):
        fits = memory.max_tokens_fitting(KAT, 8, GPU - WEIGHTS - MARGIN, 512, 262144)
        self.request(fits - 8192)
        with self.assertRaisesRegex(MemoryError, "request needs"):
            self.request(fits - 8192 + 1)

    def test_rejects_requests_over_the_context_window(self):
        with self.assertRaisesRegex(ValueError, "over the 262144-token context window"):
            self.request(260000, max_tokens=8192)

    def test_rejects_requests_that_do_not_fit_in_free_memory(self):
        self.available = 3 * GIB
        with self.assertRaisesRegex(MemoryError, "needs .* but only 2.00 GiB is free"):
            self.request(200000)
        _, step = self.request(20000)
        self.assertLessEqual(memory.request_bytes(KAT, 28192, step, 8), 2 * GIB)
        self.available = 1.5 * GIB  # less than the fixed overhead of any request
        with self.assertRaises(MemoryError):
            self.request(100)

    def test_mlx_cache_counts_as_free(self):
        self.available = 1.5 * GIB
        with self.assertRaises(MemoryError):
            self.request(1000)
        self.cached = 1 * GIB
        self.request(1000)

    def test_gpu_limit_counts_memory_already_in_use(self):
        self.active = WEIGHTS + 3 * GIB  # e.g. cached prompts
        with self.assertRaises(MemoryError):
            self.request(200000)


class ModelGuardTest(unittest.TestCase):
    def setUp(self):
        self.original = serve.server.ModelProvider.load
        self.addCleanup(lambda: setattr(serve.server.ModelProvider, "load", self.original))
        self.loads = []
        serve.server.ModelProvider.load = lambda provider, *a: self.loads.append(a) or "loaded"
        serve.guard_models()

    def test_the_served_model_loads(self):
        self.assertEqual(serve.server.ModelProvider.load(None, "default_model", None, "default_model"), "loaded")
        serve.server.ModelProvider.load(None, "default_model", None, None)
        self.assertEqual(len(self.loads), 2)

    def test_other_models_adapters_and_drafts_are_refused(self):
        for call, message in (
            (("org/other", None, "default_model"), "model 'org/other'"),
            (("default_model", "adapter", "default_model"), "adapter 'adapter'"),
            (("default_model", None, "org/draft"), "draft model 'org/draft'"),
        ):
            with self.subTest(call), self.assertRaisesRegex(ValueError, message):
                serve.server.ModelProvider.load(None, *call)
        self.assertEqual(self.loads, [])


class WatchdogTest(Patched):
    def run_watch(self, **overrides):
        """Runs three watchdog rounds with the clock and exit replaced in lib/serve.py only."""
        exits, rounds = [], []

        def fake_exit(code):
            exits.append(code)
            raise StopIteration

        def fake_sleep(seconds):
            rounds.append(seconds)
            if len(rounds) > 3:
                raise StopIteration

        originals = (serve.os, serve.time)
        serve.os = types.SimpleNamespace(_exit=fake_exit)
        serve.time = types.SimpleNamespace(sleep=fake_sleep)
        err = io.StringIO()
        try:
            with contextlib.redirect_stderr(err), self.assertRaises(StopIteration):
                serve.watch(args(**overrides))
        finally:
            serve.os, serve.time = originals
        return exits, err.getvalue()

    def test_quiet_when_there_is_room(self):
        self.assertEqual(self.run_watch(), ([], ""))
        self.assertEqual(set(self.disk_paths), {"/repo"})

    def test_memory_floor(self):
        self.available = memory.EMERGENCY_FLOOR_BYTES
        self.assertEqual(self.run_watch(), ([], ""))
        self.available -= 1
        exits, err = self.run_watch()
        self.assertEqual(exits, [1])
        self.assertIn("of memory is available. Close other applications", err)

    def test_disk_floor(self):
        self.disk = serve.DISK_EMERGENCY_FLOOR_BYTES
        self.assertEqual(self.run_watch(), ([], ""))
        self.disk -= 1
        exits, err = self.run_watch()
        self.assertEqual(exits, [1])
        self.assertIn("of disk is free. Free some disk space", err)

    def test_a_failing_reading_stops_the_server(self):
        def broken():
            raise RuntimeError("vm_stat broke")

        memory.available_system_bytes = broken
        exits, err = self.run_watch()
        self.assertEqual(exits, [1])
        self.assertIn("the memory watchdog failed", err)
        self.assertIn("vm_stat broke", err)


class MainTest(Patched):
    """serve.main wires everything together; mlx-lm itself is replaced."""

    def setUp(self):
        super().setUp()
        self.handed = None
        self.threads = []
        self.cache_limits = []
        patches = {
            (serve.server, "main"): lambda: setattr(self, "handed", list(sys.argv)),
            (serve.server.ResponseGenerator, "_tokenize"): serve.server.ResponseGenerator._tokenize,
            (serve.server.ModelProvider, "load"): serve.server.ModelProvider.load,
            (serve.mx, "device_info"): lambda: {"max_recommended_working_set_size": GPU},
            (serve.mx, "set_cache_limit"): self.cache_limits.append,
            (memory, "load_config"): lambda d: KAT,
            (memory, "weights_bytes"): lambda d: WEIGHTS,
            (serve.threading, "Thread"): lambda target, args, daemon: types.SimpleNamespace(
                start=lambda: self.threads.append((target, daemon))),
        }
        for (owner, name), value in patches.items():
            self.addCleanup(setattr, owner, name, getattr(owner, name))
            setattr(owner, name, value)

    def run_main(self, *extra, kv_bits="8", step="512"):
        original = sys.argv
        sys.argv = ["serve.py", "--model-dir", "/models/kat", "--context", "262144", "--kv-bits", kv_bits,
                    "--cache-entries", "0", "--margin", "512M", "--max-prefill-step", step,
                    "--state-dir", "/repo/state", "--", "--host", "127.0.0.1", "--port", "9", *extra]
        err = io.StringIO()
        try:
            with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
                serve.main()
        except SystemExit as e:
            return e.code, err.getvalue()
        finally:
            sys.argv = original
        return 0, err.getvalue()

    def test_wiring(self):
        tokenize, load = serve.server.ResponseGenerator._tokenize, serve.server.ModelProvider.load
        code, err = self.run_main()
        self.assertEqual(code, 0, err)
        self.assertEqual(self.handed, [
            "mlx_lm.server", "--model", "/models/kat", "--kv-bits", "8",
            "--kv-group-size", str(memory.KV_GROUP_SIZE), "--quantized-kv-start", str(memory.QUANTIZED_KV_START_TOKENS),
            "--prompt-cache-size", "0", "--prefill-step-size", "512", "--host", "127.0.0.1", "--port", "9",
        ])
        self.assertEqual(self.cache_limits, [memory.MLX_CACHE_LIMIT_BYTES])
        self.assertEqual(self.threads, [(serve.watch, True)])
        self.assertIsNot(serve.server.ResponseGenerator._tokenize, tokenize)
        self.assertIsNot(serve.server.ModelProvider.load, load)
        self.assertEqual(self.disk_paths, ["/repo"])

    def test_refuses_options_the_guard_depends_on(self):
        for option in ("--kv-bits", "--prefill-step-size=64", "--model", "--prompt-cache-bytes", "--draft-model"):
            with self.subTest(option):
                code, err = self.run_main(option, "1")
                self.assertEqual(code, 1)
                self.assertIn("is set by lib/serve.py itself", err)
                self.assertIsNone(self.handed)

    def test_refuses_another_mlx_lm_version(self):
        original = serve.mlx_lm.__version__
        serve.mlx_lm.__version__ = "0.99.0"
        try:
            code, err = self.run_main()
        finally:
            serve.mlx_lm.__version__ = original
        self.assertEqual(code, 1)
        self.assertIn("written against mlx-lm 0.32.2", err)

    def test_refuses_bad_settings(self):
        self.assertIn("QWEN_KV_BITS must be 4 or 8", self.run_main(kv_bits="16")[1])
        self.assertIn("QWEN_PREFILL_STEP must be at least 16", self.run_main(step="8")[1])

    def test_refuses_models_the_estimate_does_not_cover(self):
        memory.load_config = lambda d: KAT | {"layer_types": ["sliding_attention"] * 40}
        code, err = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("does not know the layer types sliding_attention", err)

    def test_pinned_version_matches(self):
        self.assertEqual(serve.mlx_lm.__version__, serve.MLX_LM_VERSION)
        self.assertIn(f"mlx-lm=={serve.MLX_LM_VERSION}", (REPO / "lib" / "mlx-lm.lock").read_text())


if __name__ == "__main__":
    unittest.main()
