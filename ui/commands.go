package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os/exec"
	"path/filepath"
	"strings"
	"time"
)

// CommandError is a repo command that failed, with what it printed.
type CommandError struct{ Message string }

func (e *CommandError) Error() string { return e.Message }

// run runs bin/NAME with args and returns its standard output, or a
// CommandError with what it printed when it fails. stdin is its input; an
// empty one reads as an empty file.
func (a *App) run(ctx context.Context, timeout time.Duration, stdin string, name string, args ...string) (string, error) {
	ctx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	cmd := exec.CommandContext(ctx, filepath.Join(a.repo, "bin", name), args...)
	cmd.Stdin = strings.NewReader(stdin)
	cmd.WaitDelay = 5 * time.Second
	var stdout, stderr bytes.Buffer
	cmd.Stdout, cmd.Stderr = &stdout, &stderr
	err := cmd.Run()
	if errors.Is(ctx.Err(), context.DeadlineExceeded) {
		return "", &CommandError{fmt.Sprintf("bin/%s did not finish within %s", name, timeout)}
	}
	if err != nil {
		message := strings.TrimSpace(stderr.String())
		if message == "" {
			message = strings.TrimSpace(stdout.String())
		}
		if message == "" {
			message = fmt.Sprintf("bin/%s failed: %v", name, err)
		}
		return "", &CommandError{message}
	}
	return stdout.String(), nil
}

// runJSON runs a command that prints JSON and decodes it into out.
func (a *App) runJSON(ctx context.Context, timeout time.Duration, out any, name string, args ...string) error {
	text, err := a.run(ctx, timeout, "", name, args...)
	if err != nil {
		return err
	}
	if err := json.Unmarshal([]byte(text), out); err != nil {
		return fmt.Errorf("bin/%s %s printed something that is not the JSON expected: %v", name, strings.Join(args, " "), err)
	}
	return nil
}

// Setting is one row of bin/config show --json.
type Setting struct {
	Key    string `json:"key"`
	Source string `json:"source"`
	Value  string `json:"value"`
	Group  string `json:"group"`
	Help   string `json:"help"`
}

// Settings is every setting, with the file they are saved in.
type Settings struct {
	File     string    `json:"file"`
	Settings []Setting `json:"settings"`
}

// Get is the value in effect of one setting.
func (s *Settings) Get(key string) string {
	for _, setting := range s.Settings {
		if setting.Key == key {
			return setting.Value
		}
	}
	return ""
}

func (a *App) settings(ctx context.Context) (*Settings, error) {
	var s Settings
	if err := a.runJSON(ctx, 30*time.Second, &s, "config", "show", "--json"); err != nil {
		return nil, err
	}
	return &s, nil
}

// Preset is one row of bin/models presets --json.
type Preset struct {
	Name          string `json:"name"`
	Repo          string `json:"repo"`
	Revision      string `json:"revision"`
	ContextTokens int    `json:"context_tokens"`
	Temp          string `json:"temp"`
	Downloaded    bool   `json:"downloaded"`
	About         string `json:"about"`
}

// LocalModel is one row of bin/models list --json.
type LocalModel struct {
	Name      string `json:"name"`
	Commit    string `json:"commit"`
	Folder    string `json:"folder"`
	Verified  bool   `json:"verified"`
	SizeBytes int64  `json:"size_bytes"`
}

// SearchResult is one row of bin/models search --json.
type SearchResult struct {
	Name      string `json:"name"`
	Source    string `json:"source"`
	URL       string `json:"url"`
	Downloads int64  `json:"downloads"`
	SizeBytes int64  `json:"size_bytes"`
	Updated   string `json:"updated"`
	Task      string `json:"task"`
}

// ModelInfo is bin/models info --json.
type ModelInfo struct {
	Name                  string `json:"name"`
	Commit                string `json:"commit"`
	URL                   string `json:"url"`
	DownloadBytes         int64  `json:"download_bytes"`
	WeightsBytes          int64  `json:"weights_bytes"`
	ModelType             string `json:"model_type"`
	ArchitectureSupported bool   `json:"architecture_supported"`
	ToolParser            string `json:"tool_parser"`
	ConfigContextTokens   int    `json:"config_context_tokens"`
	GPULimitBytes         int64  `json:"gpu_limit_bytes"`
	WindowTokens          int    `json:"window_tokens"`
	Fits                  bool   `json:"fits"`
	EstimateProblem       string `json:"estimate_problem"`
	Downloaded            bool   `json:"downloaded"`
	ServeCommand          string `json:"serve_command"`
}
