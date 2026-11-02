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
# Muse Glimmer's text config:
# https://huggingface.co/mlx-community/Muse-Glimmer-30B-4bit/blob/3e7677d7a40d348a3daba263a2b1c0aa41910710/config.json
GLIMMER = {
    "model_type": "muse_glimmer_text",
    "num_hidden_layers": 52,
    "layer_types": (["sliding_attention"] * 3 + ["full_attention"]) * 13,
    "num_attention_heads": 32,
    "num_key_value_heads": 2,
    "head_dim": 128,
    "hidden_size": 6656,
    "sliding_window": 2048,
}


class SizesTest(unittest.TestCase):
    def test_vm_stat_sample_matches_the_web_ui(self):
        # ui/system_test.go expects the same figure from the Go port.
        sample = (REPO / "ui" / "testdata" / "vm_stat.txt").read_text()
        self.assertEqual(memory.available_from_vm_stat(sample), 5800263680)

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
        self.assertEqual(memory.layer_counts(interval), (10, 30, 0))
        self.assertEqual(memory.layer_counts(KAT), (10, 30, 0))
        self.assertEqual(memory.layer_counts(DENSE), (4, 0, 0))
        self.assertEqual(memory.layer_counts(GLIMMER), (13, 0, 39))
        # Other models name sliding layers too but cache them their own way.
        for model_type in ("gemma3_text", None):
            other = GLIMMER | {"model_type": model_type}
            with self.subTest(model_type), self.assertRaisesRegex(memory.UnsupportedModel, "sliding_attention"):
                memory.kv_cache_bytes(other, 100, 8)
        with self.assertRaisesRegex(memory.UnsupportedModel, "chunked_attention"):
            memory.layer_counts(GLIMMER | {"layer_types": ["chunked_attention"] * 52})

    def test_sliding_layers_hold_one_window_at_16_bits(self):
        # 13 full layers x keys and values x 2 heads x 128; 39 sliding layers the same, capped at 2048 tokens.
        full = 13 * 2 * 2 * 128
        sliding = 39 * 2 * 2 * 128
        self.assertEqual(memory.kv_cache_bytes(GLIMMER, 1000, 8), (full + sliding) * 2 * 1000)
        per_value = 8 / 8 + 4 / 64
        self.assertEqual(memory.kv_cache_bytes(GLIMMER, 131072, 8), int(full * per_value * 131072 + sliding * 2 * 2048))
        self.assertEqual(memory.kv_cache_bytes(GLIMMER, 131072, 8) - memory.kv_cache_bytes(GLIMMER, 131071, 8),
                         int(full * per_value))

    def test_sliding_layers_grow_by_one_step_during_prefill(self):
        # Each sliding layer holds window + step - 1 tokens while prefilling (RotatingKVCache._update_concat).
        sliding = 39 * 2 * 2 * 128
        one_layer = 2 * 2 * 128
        at_switch = 13 * one_layer * 2 * 5000
        self.assertEqual(memory.growth_bytes(GLIMMER, 131072, 8, 512),
                         max(int(13 * one_layer * (1 + 4 / 64) * 131072) // 13, at_switch)
                         + sliding * 2 * 512 + one_layer * 2 * (2048 + 512))
        self.assertEqual(memory.growth_bytes(KAT, 131072, 8, 512), memory.growth_bytes(KAT, 131072, 8, 16))

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
        # A cut-down Muse Glimmer measured on 2026-11-01 (lib/memory.py).
        for layers, tokens, step, peak in [
            (8, 4000, 512, 0.524), (8, 6000, 512, 0.542), (8, 16384, 512, 0.885), (8, 16384, 128, 0.301),
            (8, 32768, 512, 1.413), (8, 32768, 64, 0.296), (8, 65536, 512, 2.457), (8, 65536, 128, 0.752),
            (8, 65536, 32, 0.350), (16, 65536, 512, 4.579), (16, 131072, 128, 2.491), (16, 131072, 16, 0.721),
        ]:
            cut = GLIMMER | {"num_hidden_layers": layers,
                             "layer_types": (["sliding_attention"] * 3 + ["full_attention"]) * (layers // 4)}
            # The probe generated 4 tokens after the prompt.
            measured.append((cut, tokens + 4, step, peak))
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
        for config in (KAT, GLIMMER):
            for kv_bits in (4, 8):
                previous = 0
                for tokens in list(range(1, 12000, 7)) + list(range(12000, 270000, 997)):
                    now = memory.request_bytes(config, tokens, 16, kv_bits)
                    self.assertGreaterEqual(now, previous, (config is KAT, kv_bits, tokens))
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

    def test_weights_leave_out_what_the_model_drops(self):
        import json
        import struct
        import tempfile
        from pathlib import Path

        def safetensors(path, tensors):
            # tensors: name -> byte count; the data follows the header in order.
            header, offset = {"__metadata__": {"format": "mlx"}}, 0
            for name, size in tensors.items():
                header[name] = {"dtype": "U8", "shape": [size], "data_offsets": [offset, offset + size]}
                offset += size
            text = json.dumps(header).encode()
            path.write_bytes(struct.pack("<Q", len(text)) + text + b"\0" * offset)
            return path.stat().st_size

        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            whole = safetensors(d / "model-00001-of-00002.safetensors", {"language_model.model.layers.0.w": 1000,
                                                                          "vision_tower.blocks.0.w": 300})
            whole += safetensors(d / "model-00002-of-00002.safetensors", {"vision_adapter.w": 20, "vision_projection.w": 5,
                                                                          "language_model.lm_head.weight": 70})
            (d / "config.json").write_text(json.dumps({"model_type": "muse_glimmer", "text_config": {}}))
            self.assertEqual(memory.weights_bytes(d), whole - 325)
            # Other models load every tensor, whatever its name.
            (d / "config.json").write_text(json.dumps({"model_type": "qwen3_5_moe"}))
            self.assertEqual(memory.weights_bytes(d), whole)
            (d / "model-00002-of-00002.safetensors").write_bytes(b"\xff" * 8 + b"{}")
            (d / "config.json").write_text(json.dumps({"model_type": "muse_glimmer"}))
            with self.assertRaisesRegex(SystemExit, "not a safetensors file"):
                memory.weights_bytes(d)


if __name__ == "__main__":
    unittest.main()
