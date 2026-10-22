"""lib/memory.py: the estimates the memory guard relies on."""

import sys
import unittest

from helpers import REPO

sys.path.insert(0, str(REPO / "lib"))
import memory  # noqa: E402

GIB = memory.GIB

# The text configs of the presets, as far as the estimate reads them.
KAT = {
    "num_hidden_layers": 40,
    "layer_types": (["linear_attention"] * 3 + ["full_attention"]) * 10,
    "num_attention_heads": 16,
    "num_key_value_heads": 2,
    "head_dim": 256,
    "hidden_size": 2048,
    "linear_num_value_heads": 32,
    "linear_key_head_dim": 128,
    "linear_value_head_dim": 128,
}
DENSE = {"num_hidden_layers": 4, "num_attention_heads": 8, "hidden_size": 512}


class SizesTest(unittest.TestCase):
    def test_parse_size(self):
        self.assertEqual(memory.parse_bytes("512M"), 512 * 2**20)
        self.assertEqual(memory.parse_bytes("2g"), 2 * GIB)
        self.assertEqual(memory.parse_bytes("1.5G"), int(1.5 * GIB))
        self.assertEqual(memory.parse_bytes("100"), 100)
        for bad in ("", "lots", "1T", "-1G", "1 G G"):
            with self.subTest(bad), self.assertRaises(SystemExit):
                memory.parse_bytes(bad)

    def test_kv_cache_matches_hand_calculation(self):
        # 10 full-attention layers x keys and values x 2 heads x 256 = 10240 values a token.
        values = 10 * 2 * 2 * 256
        linear = 30 * 32 * 128 * 128 * 4
        self.assertEqual(memory.kv_cache_bytes(KAT, 4096, 8), values * 2 * 4096 + linear)
        per_value = 8 / 8 + 4 / 64
        self.assertEqual(memory.kv_cache_bytes(KAT, 262144, 8), int(values * per_value * 262144 + linear))
        self.assertLess(memory.kv_cache_bytes(KAT, 262144, 4), memory.kv_cache_bytes(KAT, 262144, 8))

    def test_kv_cache_without_layer_types_or_head_dim(self):
        # Every layer full attention; head size from hidden_size / heads.
        self.assertEqual(memory.kv_cache_bytes(DENSE, 100, 8), 4 * 2 * 8 * 64 * 2 * 100)
        self.assertEqual(memory.kv_cache_bytes(DENSE | {"num_key_value_heads": None}, 100, 8), 4 * 2 * 8 * 64 * 2 * 100)

    def test_quantized_from_the_threshold_on(self):
        values = 10 * 2 * 2 * 256
        linear = 30 * 32 * 128 * 128 * 4
        self.assertEqual(memory.kv_cache_bytes(KAT, 4999, 8), values * 2 * 4999 + linear)
        self.assertEqual(memory.kv_cache_bytes(KAT, 5000, 8), int(values * (1 + 4 / 64) * 5000 + linear))

    def test_layer_layouts(self):
        interval = {k: v for k, v in KAT.items() if k != "layer_types"} | {"full_attention_interval": 4}
        self.assertEqual(memory.layer_counts(interval), (10, 30))
        self.assertEqual(memory.layer_counts(KAT), (10, 30))
        self.assertEqual(memory.layer_counts(DENSE), (4, 0))
        sliding = KAT | {"layer_types": ["sliding_attention", "full_attention"] * 20}
        with self.assertRaisesRegex(memory.UnsupportedModel, "sliding_attention"):
            memory.kv_cache_bytes(sliding, 100, 8)

    def test_attention_scores(self):
        self.assertEqual(memory.attention_bytes(KAT, 512, 262144), int(2.25 * 16 * 512 * 262144 * 2))
        # A prompt shorter than the step only computes that many rows.
        self.assertEqual(memory.attention_bytes(KAT, 512, 100), int(2.25 * 16 * 100 * 100 * 2))

    def test_measured_cases_stay_under_the_estimate(self):
        # Peaks above the weights measured on 2026-10-22 (lib/memory.py).
        qwen35_4b = {
            "num_hidden_layers": 32,
            "layer_types": (["linear_attention"] * 3 + ["full_attention"]) * 8,
            "num_attention_heads": 16,
            "num_key_value_heads": 4,
            "head_dim": 256,
            "hidden_size": 2560,
            "linear_num_value_heads": 32,
            "linear_key_head_dim": 128,
            "linear_value_head_dim": 128,
        }
        measured = [
            (qwen35_4b, 16384, 512, 1.450),
            (qwen35_4b, 16384, 128, 0.901),
            (qwen35_4b, 32768, 512, 2.179),
            (qwen35_4b, 32768, 128, 1.200),
            (qwen35_4b, 32768, 64, 1.166),
            (qwen35_4b, 32768, 32, 1.067),
            (qwen35_4b, 65536, 512, 3.771),
            (qwen35_4b, 65536, 128, 2.080),
            (KAT, 8192, 64, 0.372),
        ]
        for config, tokens, step, peak in measured:
            with self.subTest(tokens=tokens, step=step):
                self.assertGreater(memory.request_bytes(config, tokens, step, 8) / GIB, peak)


class StepTest(unittest.TestCase):
    def test_steps_halve_down_to_the_minimum(self):
        self.assertEqual(list(memory.prefill_steps(512)), [512, 256, 128, 64, 32, 16])
        self.assertEqual(list(memory.prefill_steps(768)), [768, 384, 192, 96, 48, 24, 16])
        self.assertEqual(list(memory.prefill_steps(600))[-1], 16)
        self.assertEqual(list(memory.prefill_steps(16)), [16])
        with self.assertRaises(ValueError):
            list(memory.prefill_steps(8))

    def test_pick_step_prefers_the_largest_that_fits(self):
        plenty = 100 * GIB
        self.assertEqual(memory.pick_step(KAT, 262144, 8, plenty, 512), 512)
        tight = memory.request_bytes(KAT, 262144, 16, 8)
        self.assertEqual(memory.pick_step(KAT, 262144, 8, tight, 512), 16)
        self.assertIsNone(memory.pick_step(KAT, 262144, 8, tight - 1, 512))
        short = memory.request_bytes(KAT, 1000, 512, 8)
        self.assertEqual(memory.pick_step(KAT, 1000, 8, short, 512), 512)

    def test_max_tokens_fitting(self):
        room = memory.request_bytes(KAT, 50000, 16, 8)
        fits = memory.max_tokens_fitting(KAT, 8, room, 512, 262144)
        self.assertGreaterEqual(fits, 50000)
        self.assertIsNotNone(memory.pick_step(KAT, fits, 8, room, 512))
        self.assertIsNone(memory.pick_step(KAT, fits + 1, 8, room, 512))
        self.assertEqual(memory.max_tokens_fitting(KAT, 8, 0, 512, 262144), 0)
        self.assertEqual(memory.max_tokens_fitting(KAT, 8, 100 * GIB, 512, 4096), 4096)

    def test_the_default_model_fits_most_of_its_window(self):
        weights = int(11.41 * GIB)
        room = 16 * GIB - weights - 512 * memory.MIB
        fits = memory.max_tokens_fitting(KAT, 8, room, 512, 262144)
        self.assertGreater(fits, 240000)
        self.assertLess(fits, 262144)
        self.assertGreater(memory.request_bytes(KAT, 262144, 512, 8), memory.request_bytes(KAT, 262144, 16, 8) + 3 * GIB)

    def test_estimate_never_falls_as_context_grows(self):
        for kv_bits in (4, 8):
            previous = 0
            for tokens in list(range(1, 12000, 7)) + list(range(12000, 270000, 997)):
                now = memory.request_bytes(KAT, tokens, 16, kv_bits)
                self.assertGreaterEqual(now, previous, (kv_bits, tokens))
                previous = now


class SystemTest(unittest.TestCase):
    def test_available_memory_and_disk_are_plausible(self):
        available = memory.available_system_bytes()
        self.assertGreater(available, 0)
        self.assertLess(available, 1024 * GIB)
        self.assertGreater(memory.free_disk_bytes(str(REPO)), 0)

    VM_STAT = """Mach Virtual Memory Statistics: (page size of 16384 bytes)
Pages free:                               11979.
Pages active:                            565050.
Pages inactive:                          532191.
Pages speculative:                        32526.
Pages purgeable:                          15660.
"Translation faults":                7686145995.
File-backed pages:                       278883.
Anonymous pages:                         850884.
Pages stored in compressor:              987423.
Pages occupied by compressor:            222739.
"""

    def test_available_memory_from_vm_stat(self):
        # Free, file-backed and purgeable in full; inactive app memory for what compressing it frees.
        saving = 1 - 222739 / 987423
        pages = 11979 + 278883 + 15660 + int((532191 - 278883) * saving)
        self.assertEqual(memory.available_from_vm_stat(self.VM_STAT), pages * 16384)
        nothing_compressed = self.VM_STAT.replace("987423", "0").replace("222739", "0")
        self.assertEqual(memory.available_from_vm_stat(nothing_compressed), (11979 + 278883 + 15660) * 16384)

    def test_vm_stat_without_expected_fields_is_an_error(self):
        with self.assertRaisesRegex(RuntimeError, "File-backed pages"):
            memory.available_from_vm_stat(self.VM_STAT.replace("File-backed pages", "Other"))
        with self.assertRaisesRegex(RuntimeError, "page size"):
            memory.available_from_vm_stat("Pages free: 1.\n")

    def test_weights_need_safetensors(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d, self.assertRaises(SystemExit):
            memory.weights_bytes(d)


if __name__ == "__main__":
    unittest.main()
