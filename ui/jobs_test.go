package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// fakeRepo is a folder with bin/NAME scripts, as the jobs run them.
func fakeRepo(t *testing.T, scripts map[string]string) string {
	t.Helper()
	repo := t.TempDir()
	if err := os.MkdirAll(filepath.Join(repo, "bin"), 0o755); err != nil {
		t.Fatal(err)
	}
	for name, body := range scripts {
		if err := os.WriteFile(filepath.Join(repo, "bin", name), []byte("#!/bin/sh\n"+body), 0o755); err != nil {
			t.Fatal(err)
		}
	}
	return repo
}

func waitFor(t *testing.T, js *Jobs, id, state string) JobState {
	t.Helper()
	deadline := time.Now().Add(10 * time.Second)
	for time.Now().Before(deadline) {
		s, err := js.Get(id)
		if err != nil {
			t.Fatal(err)
		}
		if s.State == state {
			return s
		}
		time.Sleep(50 * time.Millisecond)
	}
	s, _ := js.Get(id)
	t.Fatalf("job %s is %q, not %q", id, s.State, state)
	return s
}

func TestJobRecordsOutputAndExit(t *testing.T) {
	repo := fakeRepo(t, map[string]string{
		"evals":        `echo "label $1, thinking $EVAL_THINKING"; exit 0`,
		"bench-agents": `echo "nothing to run" >&2; exit 3`,
	})
	js := NewJobs(repo)
	job, err := js.Start("evals", "Model evals x", "evals", []string{"x"}, map[string]string{"EVAL_THINKING": "off"}, "")
	if err != nil {
		t.Fatal(err)
	}
	done := waitFor(t, js, job.ID, "done")
	out := js.Output(done.Job, 4096)
	if !strings.Contains(out, "$ EVAL_THINKING=off ./bin/evals x\n") || !strings.Contains(out, "label x, thinking off") {
		t.Fatalf("output: %q", out)
	}
	if done.Ended.IsZero() || done.Duration() < 0 {
		t.Fatalf("no end time: %+v", done)
	}

	failed, err := js.Start("bench", "Agent benchmark", "bench-agents", []string{"y"}, nil, "")
	if err != nil {
		t.Fatal(err)
	}
	s := waitFor(t, js, failed.ID, "failed (exit 3)")
	if !strings.Contains(js.Output(s.Job, 4096), "nothing to run") {
		t.Fatal("the error output is missing")
	}
	if list := js.List(); len(list) != 2 || list[0].ID != failed.ID {
		t.Fatalf("the newest job is not first: %+v", list)
	}
}

func TestJobsSharingTheServerRunOneAtATime(t *testing.T) {
	repo := fakeRepo(t, map[string]string{"bench-agents": "sleep 30", "evals": "exit 0", "models": "exit 0"})
	js := NewJobs(repo)
	slow, err := js.Start("bench", "Agent benchmark kat", "bench-agents", []string{"kat"}, nil, "")
	if err != nil {
		t.Fatal(err)
	}
	waitFor(t, js, slow.ID, "running")
	if _, err := js.Start("evals", "Model evals kat", "evals", []string{"kat"}, nil, ""); err == nil ||
		!strings.Contains(err.Error(), "share the model server") {
		t.Fatalf("a second model job started: %v", err)
	}
	// A download does not use the server, so it may run meanwhile.
	if _, err := js.Start("download", "Download org/m", "models", []string{"download"}, nil, ""); err != nil {
		t.Fatal(err)
	}
	if err := js.Stop(slow.ID); err != nil {
		t.Fatal(err)
	}
	waitFor(t, js, slow.ID, "stopped")
	if err := js.Stop(slow.ID); err == nil {
		t.Fatal("stopping a stopped job should fail")
	}
	if _, err := js.Start("evals", "Model evals kat", "evals", []string{"kat"}, nil, ""); err != nil {
		t.Fatalf("the server is free again: %v", err)
	}
}

func TestStopEndsTheWholeSession(t *testing.T) {
	marker := filepath.Join(t.TempDir(), "child-still-ran")
	// The child sleeps, then writes the marker; Stop must end it too.
	repo := fakeRepo(t, map[string]string{"smoke-test": "(sleep 2; touch " + marker + ") & wait"})
	js := NewJobs(repo)
	job, err := js.Start("smoke", "Smoke test", "smoke-test", nil, nil, "")
	if err != nil {
		t.Fatal(err)
	}
	waitFor(t, js, job.ID, "running")
	if err := js.Stop(job.ID); err != nil {
		t.Fatal(err)
	}
	time.Sleep(3 * time.Second)
	if fileExists(marker) {
		t.Fatal("the job's child kept running after Stop")
	}
}

func TestJobsFoundAgainByANewUI(t *testing.T) {
	repo := fakeRepo(t, map[string]string{"evals": "exit 0"})
	job, err := NewJobs(repo).Start("evals", "Model evals a", "evals", []string{"a"}, nil, "")
	if err != nil {
		t.Fatal(err)
	}
	waitFor(t, NewJobs(repo), job.ID, "done")
	// A job whose process vanished without an exit status is lost, not running.
	os.Remove(filepath.Join(repo, "state", "jobs", job.ID, "exit"))
	if s, _ := NewJobs(repo).Get(job.ID); s.State != "lost" {
		t.Fatalf("got %q", s.State)
	}
	for _, id := range []string{"", "../x", "a/b", strings.Repeat("a", 65)} {
		if _, err := NewJobs(repo).Get(id); err == nil {
			t.Errorf("Get(%q) should fail", id)
		}
	}
}

func TestCommandLineQuotes(t *testing.T) {
	job := &Job{Command: "bin/team-solve", Args: []string{"/a b/it's"}, Env: map[string]string{"B": "x y", "A": "1"}}
	if got, want := job.CommandLine(), `A=1 B='x y' ./bin/team-solve '/a b/it'\''s'`; got != want {
		t.Fatalf("got %s, want %s", got, want)
	}
}
