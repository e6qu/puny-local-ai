package main

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"fmt"
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

// A job is one run of a long repo command: a download, a setup item, a
// workflow run, a benchmark or the smoke test. Each job has a folder in
// state/jobs/ID with:
//
//	job.json  what was run, by whom and when
//	output    everything it printed
//	exit      its exit status, written by the shell that runs it
//	stopped   present when Stop ended it
//
// A job runs in its own session, so it keeps running when the UI stops, and
// a UI started later finds it again from these files.

// Kinds of jobs, and which of them use the model server or download: two
// jobs that share a group never run at the same time. bin/verify does both.
var jobGroups = map[string][]string{
	"download":   {"download"},
	"setup":      {"download"},
	"team-solve": {"model"},
	"goal":       {"model"},
	"bench":      {"model"},
	"evals":      {"model"},
	"smoke":      {"model"},
	"verify":     {"model", "download"},
}

// sharedGroup is a group both kinds of job belong to, or "".
func sharedGroup(kind, other string) string {
	for _, group := range jobGroups[kind] {
		for _, theirs := range jobGroups[other] {
			if group == theirs {
				return group
			}
		}
	}
	return ""
}

type Job struct {
	ID      string            `json:"id"`
	Kind    string            `json:"kind"`
	Title   string            `json:"title"`
	Command string            `json:"command"` // bin/NAME
	Args    []string          `json:"args"`
	Env     map[string]string `json:"env,omitempty"`
	Folder  string            `json:"folder,omitempty"` // the folder a workflow works in
	Started time.Time         `json:"started"`
	PID     int               `json:"pid"`

	dir string
}

// JobState is a job as the UI shows it.
type JobState struct {
	*Job
	State    string // running, done, failed (exit N), stopped or lost
	Ended    time.Time
	ExitCode int
}

func (s JobState) Running() bool { return s.State == "running" }

// Duration is how long it ran, or has run so far.
func (s JobState) Duration() time.Duration {
	end := s.Ended
	if s.Running() || end.IsZero() {
		end = time.Now()
	}
	return end.Sub(s.Started).Round(time.Second)
}

// CommandLine is the job as it would be typed in the terminal.
func (j *Job) CommandLine() string {
	var parts []string
	keys := make([]string, 0, len(j.Env))
	for k := range j.Env {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	for _, k := range keys {
		parts = append(parts, k+"="+shellQuote(j.Env[k]))
	}
	parts = append(parts, "./"+j.Command)
	for _, arg := range j.Args {
		parts = append(parts, shellQuote(arg))
	}
	return strings.Join(parts, " ")
}

func shellQuote(s string) string {
	if s != "" && strings.Trim(s, "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_./:@=,") == "" {
		return s
	}
	return "'" + strings.ReplaceAll(s, "'", `'\''`) + "'"
}

type Jobs struct {
	repo string
	root string
	mu   sync.Mutex
}

func NewJobs(repo string) *Jobs {
	return &Jobs{repo: repo, root: filepath.Join(repo, "state", "jobs")}
}

// Start runs bin/command with args and env as a new job.
func (js *Jobs) Start(kind, title, command string, args []string, env map[string]string, folder string) (*Job, error) {
	if _, ok := jobGroups[kind]; !ok {
		return nil, fmt.Errorf("unknown kind of job %q", kind)
	}
	js.mu.Lock()
	defer js.mu.Unlock()
	for _, other := range js.list() {
		group := sharedGroup(kind, other.Kind)
		if other.Running() && group != "" {
			why := "they share the model server"
			if group == "download" {
				why = "downloads run one at a time"
			}
			return nil, &CommandError{fmt.Sprintf("%q is still running, and %s; wait for it or stop it on the Jobs page", other.Title, why)}
		}
	}
	suffix := make([]byte, 3)
	rand.Read(suffix)
	now := time.Now()
	job := &Job{
		// Sorts by start time, to the microsecond; the suffix keeps it unique.
		ID:   fmt.Sprintf("%s-%06d-%s", now.Format("20060102-150405"), now.Nanosecond()/1000, hex.EncodeToString(suffix)),
		Kind: kind, Title: title,
		Command: "bin/" + command, Args: args, Env: env, Folder: folder, Started: now,
	}
	job.dir = filepath.Join(js.root, job.ID)
	if err := os.MkdirAll(job.dir, 0o755); err != nil {
		return nil, err
	}
	out, err := os.Create(filepath.Join(job.dir, "output"))
	if err != nil {
		return nil, err
	}
	defer out.Close()
	fmt.Fprintf(out, "$ %s\n", job.CommandLine())
	// The shell records the exit status, so it is known even when the UI
	// stopped meanwhile. The job's folder is $0, which also marks the process
	// as this job's in ps.
	cmd := exec.Command("/bin/sh", append([]string{"-c", `"$@"; echo $? > "$0/exit"`, job.dir,
		filepath.Join(js.repo, job.Command)}, args...)...)
	cmd.Dir = js.repo
	cmd.Env = os.Environ()
	for k, v := range env {
		cmd.Env = append(cmd.Env, k+"="+v)
	}
	cmd.Stdout, cmd.Stderr = out, out
	cmd.SysProcAttr = &syscall.SysProcAttr{Setsid: true}
	if err := cmd.Start(); err != nil {
		return nil, err
	}
	go cmd.Wait() // collects it, so it never lingers as a zombie
	job.PID = cmd.Process.Pid
	data, _ := json.MarshalIndent(job, "", "  ")
	if err := os.WriteFile(filepath.Join(job.dir, "job.json"), data, 0o644); err != nil {
		return nil, err
	}
	return job, nil
}

func (js *Jobs) load(dir string) (*Job, error) {
	data, err := os.ReadFile(filepath.Join(dir, "job.json"))
	if err != nil {
		return nil, err
	}
	var job Job
	if err := json.Unmarshal(data, &job); err != nil {
		return nil, fmt.Errorf("%s/job.json is damaged: %v", dir, err)
	}
	job.dir = dir
	return &job, nil
}

func (js *Jobs) state(job *Job) JobState {
	s := JobState{Job: job}
	if strings.Contains(command(job.PID), job.dir) {
		s.State = "running"
		return s
	}
	if info, err := os.Stat(filepath.Join(job.dir, "stopped")); err == nil {
		s.State, s.Ended = "stopped", info.ModTime()
		return s
	}
	if data, err := os.ReadFile(filepath.Join(job.dir, "exit")); err == nil {
		s.ExitCode, _ = strconv.Atoi(strings.TrimSpace(string(data)))
		if info, err := os.Stat(filepath.Join(job.dir, "exit")); err == nil {
			s.Ended = info.ModTime()
		}
		if s.ExitCode == 0 {
			s.State = "done"
		} else {
			s.State = fmt.Sprintf("failed (exit %d)", s.ExitCode)
		}
		return s
	}
	// Killed outright, or the Mac restarted while it ran.
	s.State = "lost"
	if info, err := os.Stat(filepath.Join(job.dir, "output")); err == nil {
		s.Ended = info.ModTime()
	}
	return s
}

// list is every job, newest first; js.mu must be held, or the caller must
// accept a list that is a moment old.
func (js *Jobs) list() []JobState {
	dirs, _ := filepath.Glob(filepath.Join(js.root, "*", "job.json"))
	sort.Sort(sort.Reverse(sort.StringSlice(dirs)))
	var states []JobState
	for _, path := range dirs {
		if job, err := js.load(filepath.Dir(path)); err == nil {
			states = append(states, js.state(job))
		}
	}
	return states
}

func (js *Jobs) List() []JobState { return js.list() }

func (js *Jobs) Get(id string) (JobState, error) {
	if !validID(id) {
		return JobState{}, fmt.Errorf("no job %q", id)
	}
	job, err := js.load(filepath.Join(js.root, id))
	if err != nil {
		return JobState{}, fmt.Errorf("no job %q", id)
	}
	return js.state(job), nil
}

func validID(id string) bool {
	if id == "" || len(id) > 64 {
		return false
	}
	return strings.Trim(id, "0123456789abcdef-") == ""
}

// Output is the end of what a job printed.
func (js *Jobs) Output(job *Job, limit int64) string {
	return tail(filepath.Join(job.dir, "output"), limit)
}

// Stop ends a running job and everything it started, which share its session.
func (js *Jobs) Stop(id string) error {
	s, err := js.Get(id)
	if err != nil {
		return err
	}
	if !s.Running() {
		return &CommandError{fmt.Sprintf("%q is not running; it is %s", s.Title, s.State)}
	}
	if err := os.WriteFile(filepath.Join(s.dir, "stopped"), nil, 0o644); err != nil {
		return err
	}
	syscall.Kill(-s.PID, syscall.SIGTERM)
	deadline := time.Now().Add(15 * time.Second)
	for time.Now().Before(deadline) {
		if !alive(s.PID) {
			return nil
		}
		time.Sleep(200 * time.Millisecond)
	}
	syscall.Kill(-s.PID, syscall.SIGKILL)
	return nil
}
