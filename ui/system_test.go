package main

import (
	"os"
	"strings"
	"testing"
)

// The figure lib/memory.py's available_from_vm_stat gives for the same
// sample; tests/test_memory.py checks the Python side against it too.
const sampleAvailableBytes = 5800263680

func TestAvailableFromVMStatMatchesPython(t *testing.T) {
	sample, err := os.ReadFile("testdata/vm_stat.txt")
	if err != nil {
		t.Fatal(err)
	}
	got, err := availableFromVMStat(string(sample))
	if err != nil {
		t.Fatal(err)
	}
	if got != sampleAvailableBytes {
		t.Fatalf("got %d bytes, lib/memory.py gives %d", got, sampleAvailableBytes)
	}
}

func TestAvailableFromVMStatRefusesPartialOutput(t *testing.T) {
	if _, err := availableFromVMStat("Pages free: 3.\n"); err == nil || !strings.Contains(err.Error(), "page size") {
		t.Fatalf("got %v", err)
	}
	_, err := availableFromVMStat("Mach Virtual Memory Statistics: (page size of 16384 bytes)\nPages free: 3.\n")
	if err == nil || !strings.Contains(err.Error(), "Pages inactive") {
		t.Fatalf("got %v", err)
	}
}

func TestAvailableFromVMStatWithNothingCompressed(t *testing.T) {
	out := "Mach Virtual Memory Statistics: (page size of 100 bytes)\nPages free: 10.\nPages inactive: 50.\n" +
		"Pages purgeable: 1.\nFile-backed pages: 20.\nPages stored in compressor: 0.\nPages occupied by compressor: 0.\n"
	got, err := availableFromVMStat(out)
	if err != nil || got != (10+20+1)*100 {
		t.Fatalf("got %d, %v", got, err)
	}
}

func TestModelName(t *testing.T) {
	for folder, want := range map[string]string{
		"mlx-community--Qwen3.5-4B-4bit@0e7f":    "mlx-community/Qwen3.5-4B-4bit",
		"modelscope--org--Some--Model@abc":       "modelscope:org/Some--Model",
		"NexVeridian--Qwen3-Coder-Next-8bit@fbc": "NexVeridian/Qwen3-Coder-Next-8bit",
	} {
		if got := modelName(folder); got != want {
			t.Errorf("modelName(%q) = %q, want %q", folder, got, want)
		}
	}
}

func TestGrouped(t *testing.T) {
	for n, want := range map[int64]string{0: "0", 999: "999", 1000: "1,000", 262144: "262,144", 1234567: "1,234,567"} {
		if got := grouped(n); got != want {
			t.Errorf("grouped(%d) = %q, want %q", n, got, want)
		}
	}
}
