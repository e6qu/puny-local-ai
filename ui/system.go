package main

import (
	"errors"
	"fmt"
	"os"
	"os/exec"
	"regexp"
	"strconv"
	"strings"
	"syscall"
)

var pageSizePattern = regexp.MustCompile(`page size of (\d+) bytes`)

// availableFromVMStat is what macOS can hand out without swapping, from
// vm_stat output, worked out exactly as lib/memory.py's
// available_from_vm_stat does (the tests compare the two): free, file-backed
// and purgeable pages count in full, and inactive app memory only for the
// room compressing it frees, at the compression ratio vm_stat reports now.
func availableFromVMStat(out string) (int64, error) {
	match := pageSizePattern.FindStringSubmatch(out)
	if match == nil {
		return 0, errors.New("vm_stat did not report its page size")
	}
	pageSizeBytes, _ := strconv.ParseInt(match[1], 10, 64)
	pages := map[string]int64{}
	for _, line := range strings.Split(out, "\n")[1:] {
		name, value, found := strings.Cut(line, ":")
		if !found {
			continue
		}
		value = strings.TrimSuffix(strings.TrimSpace(value), ".")
		if n, err := strconv.ParseInt(value, 10, 64); err == nil && n >= 0 {
			pages[strings.Trim(strings.TrimSpace(name), `"`)] = n
		}
	}
	var missing []string
	for _, name := range []string{"Pages free", "Pages inactive", "Pages purgeable", "File-backed pages",
		"Pages stored in compressor", "Pages occupied by compressor"} {
		if _, ok := pages[name]; !ok {
			missing = append(missing, name)
		}
	}
	if missing != nil {
		return 0, fmt.Errorf("vm_stat did not report %s", strings.Join(missing, ", "))
	}
	stored, occupied := pages["Pages stored in compressor"], pages["Pages occupied by compressor"]
	saving := 0.0
	if stored > 0 {
		saving = 1 - float64(occupied)/float64(stored)
	}
	inactiveApp := max(0, pages["Pages inactive"]-pages["File-backed pages"])
	available := pages["Pages free"] + pages["File-backed pages"] + pages["Pages purgeable"] +
		int64(float64(inactiveApp)*max(0, saving))
	return available * pageSizeBytes, nil
}

// Memory is what the status bar shows about this Mac's memory.
type Memory struct {
	TotalBytes     int64
	AvailableBytes int64
	Problem        string
}

func readMemory() Memory {
	var m Memory
	total, err := exec.Command("/usr/sbin/sysctl", "-n", "hw.memsize").Output()
	if err == nil {
		m.TotalBytes, err = strconv.ParseInt(strings.TrimSpace(string(total)), 10, 64)
	}
	if err != nil {
		m.Problem = fmt.Sprintf("cannot read the memory size: %v", err)
		return m
	}
	out, err := exec.Command("/usr/bin/vm_stat").Output()
	if err == nil {
		m.AvailableBytes, err = availableFromVMStat(string(out))
	}
	if err != nil {
		m.Problem = fmt.Sprintf("cannot read the free memory: %v", err)
	}
	return m
}

// freeDiskBytes is the space free for this user on the volume of path.
func freeDiskBytes(path string) (int64, error) {
	var stat syscall.Statfs_t
	if err := syscall.Statfs(path, &stat); err != nil {
		return 0, err
	}
	return int64(stat.Bavail) * int64(stat.Bsize), nil
}

// command is the command line of a running process, or "" when none runs.
func command(pid int) string {
	out, err := exec.Command("/bin/ps", "-o", "command=", "-p", strconv.Itoa(pid)).Output()
	if err != nil {
		return ""
	}
	return strings.TrimSpace(string(out))
}

// residentBytes is the memory a process holds now, from ps, or 0.
func residentBytes(pid int) int64 {
	out, err := exec.Command("/bin/ps", "-o", "rss=", "-p", strconv.Itoa(pid)).Output()
	if err != nil {
		return 0
	}
	kib, err := strconv.ParseInt(strings.TrimSpace(string(out)), 10, 64)
	if err != nil {
		return 0
	}
	return kib * 1024
}

func alive(pid int) bool {
	err := syscall.Kill(pid, 0)
	return err == nil || errors.Is(err, syscall.EPERM)
}

func fileExists(path string) bool {
	_, err := os.Stat(path)
	return err == nil
}
