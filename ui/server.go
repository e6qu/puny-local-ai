package main

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"
)

const (
	logTailBytes = 16384
	stopTimeout  = 30 * time.Second
)

// ModelServer starts and stops the bin/serve-qwen servers of this repo.
type ModelServer struct {
	repo string
	log  string

	mu       sync.Mutex
	cmd      *exec.Cmd     // the server this UI started last
	exited   chan struct{} // closed once cmd has exited
	stopped  bool          // whether Stop ended it, rather than an error
	exitCode int
}

func NewModelServer(repo string) *ModelServer {
	return &ModelServer{repo: repo, log: filepath.Join(repo, "state", "logs", "serve-qwen.log")}
}

// Served is a server of this repo that bin/serve-qwen recorded and that runs.
type Served struct {
	PID          int
	Port         string
	WindowTokens int
	ModelDir     string
}

// Running lists the servers of this repo that still run, from the records
// bin/serve-qwen keeps in state/served/port-PORT ("PID WINDOW").
func (s *ModelServer) Running() []Served {
	records, _ := filepath.Glob(filepath.Join(s.repo, "state", "served", "port-*"))
	sort.Strings(records)
	var found []Served
	for _, record := range records {
		data, err := os.ReadFile(record)
		if err != nil {
			continue
		}
		fields := strings.Fields(string(data))
		if len(fields) == 0 {
			continue
		}
		pid, err := strconv.Atoi(fields[0])
		if err != nil {
			continue
		}
		line := command(pid)
		if !strings.Contains(line, filepath.Join(s.repo, "lib", "serve.py")) {
			continue
		}
		served := Served{PID: pid, Port: strings.TrimPrefix(filepath.Base(record), "port-")}
		if len(fields) == 2 {
			served.WindowTokens, _ = strconv.Atoi(fields[1])
		}
		parts := strings.Fields(line)
		for i, part := range parts {
			if part == "--model-dir" && i+1 < len(parts) {
				served.ModelDir = parts[i+1]
			}
		}
		found = append(found, served)
	}
	return found
}

// starting reports whether the server this UI started is still loading or
// running; s.mu must be held.
func (s *ModelServer) starting() bool {
	if s.cmd == nil {
		return false
	}
	select {
	case <-s.exited:
		return false
	default:
		return true
	}
}

func (s *ModelServer) Start() error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if running := s.Running(); len(running) > 0 {
		return &CommandError{fmt.Sprintf("a server already runs (pid %d); stop it first, or use Restart", running[0].PID)}
	}
	if s.starting() {
		return &CommandError{fmt.Sprintf("a server is already starting (pid %d); wait for it, or use Restart", s.cmd.Process.Pid)}
	}
	if err := os.MkdirAll(filepath.Dir(s.log), 0o755); err != nil {
		return err
	}
	out, err := os.Create(s.log)
	if err != nil {
		return err
	}
	defer out.Close()
	cmd := exec.Command(filepath.Join(s.repo, "bin", "serve-qwen"))
	cmd.Stdout, cmd.Stderr = out, out
	// Its own session, so it keeps running when the UI stops.
	cmd.SysProcAttr = &syscall.SysProcAttr{Setsid: true}
	if err := cmd.Start(); err != nil {
		return err
	}
	s.cmd, s.exited, s.stopped = cmd, make(chan struct{}), false
	exited := s.exited
	go func() {
		cmd.Wait()
		s.mu.Lock()
		s.exitCode = cmd.ProcessState.ExitCode()
		s.mu.Unlock()
		close(exited)
	}()
	return nil
}

// Stop stops every server of this repo, wherever it was started.
func (s *ModelServer) Stop() error {
	s.mu.Lock()
	defer s.mu.Unlock()
	var pids []int
	for _, served := range s.Running() {
		pids = append(pids, served.PID)
	}
	if s.starting() && !contains(pids, s.cmd.Process.Pid) {
		pids = append(pids, s.cmd.Process.Pid)
	}
	for _, pid := range pids {
		syscall.Kill(pid, syscall.SIGTERM)
	}
	s.stopped = true
	deadline := time.Now().Add(stopTimeout)
	for time.Now().Before(deadline) {
		// The UI's own child is collected by the goroutine Start began, so it
		// does not linger as a zombie that would count as alive.
		left := false
		for _, pid := range pids {
			left = left || alive(pid)
		}
		if !left {
			return nil
		}
		s.mu.Unlock()
		time.Sleep(200 * time.Millisecond)
		s.mu.Lock()
	}
	return &CommandError{fmt.Sprintf("the server did not stop within %s", stopTimeout)}
}

func contains(list []int, value int) bool {
	for _, v := range list {
		if v == value {
			return true
		}
	}
	return false
}

// ServerStatus is what the UI shows about the model server.
type ServerStatus struct {
	BaseURL       string `json:"base_url"`
	State         string `json:"state"` // running, starting, stopped or "stopped (exit N)"
	Model         string `json:"model"`
	WindowTokens  int    `json:"window_tokens"`
	PID           int    `json:"pid"`
	ResidentBytes int64  `json:"resident_bytes"`
	Log           string `json:"log"`
	Elsewhere     string `json:"elsewhere"`
}

func (s *ModelServer) Status(baseURL string) ServerStatus {
	status := ServerStatus{BaseURL: baseURL, State: "stopped", Log: tail(s.log, logTailBytes)}
	parts, err := url.Parse(baseURL)
	if err != nil {
		status.State = "stopped"
		return status
	}
	root := strings.TrimSuffix(baseURL, "/v1")
	healthy := false
	client := http.Client{Timeout: 3 * time.Second}
	if response, err := client.Get(root + "/health"); err == nil {
		var body struct{ Status string }
		healthy = json.NewDecoder(io.LimitReader(response.Body, 4096)).Decode(&body) == nil && body.Status == "ok"
		response.Body.Close()
	}
	s.mu.Lock()
	switch {
	case healthy:
		status.State = "running"
	case s.starting():
		status.State = "starting"
	case s.cmd != nil && !s.stopped:
		status.State = fmt.Sprintf("stopped (exit %d)", s.exitCode)
	}
	s.mu.Unlock()
	for _, served := range s.Running() {
		if served.Port != parts.Port() {
			// Settings changed since it started, such as the port.
			status.Elsewhere = fmt.Sprintf("a server of this repo runs on port %s, not at %s; restart it to apply the settings", served.Port, baseURL)
			continue
		}
		if healthy {
			status.PID, status.WindowTokens = served.PID, served.WindowTokens
			status.ResidentBytes = residentBytes(served.PID)
			if served.ModelDir != "" {
				status.Model = modelName(filepath.Base(served.ModelDir))
			}
		}
	}
	return status
}

// modelName turns a folder in state/models, ORG--NAME@COMMIT or
// modelscope--ORG--NAME@COMMIT, back into the model's name, as
// lib/models.py's downloaded() does.
func modelName(folder string) string {
	stem, _, _ := strings.Cut(folder, "@")
	prefix := ""
	if rest, ok := strings.CutPrefix(stem, "modelscope--"); ok {
		prefix, stem = "modelscope:", rest
	}
	owner, name, _ := strings.Cut(stem, "--")
	return prefix + owner + "/" + name
}

// tail is the end of a file, at most limit bytes, or "" when it is missing.
func tail(path string, limit int64) string {
	f, err := os.Open(path)
	if err != nil {
		return ""
	}
	defer f.Close()
	info, err := f.Stat()
	if err != nil {
		return ""
	}
	if info.Size() > limit {
		f.Seek(info.Size()-limit, io.SeekStart)
	}
	data, _ := io.ReadAll(f)
	return strings.ToValidUTF8(string(data), "�")
}
