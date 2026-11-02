package main

import (
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

const origin = "http://127.0.0.1:8090"

// The repo commands the pages run, faked: bin/config keeps puny.conf in the
// repo and logs each set; the others print fixed answers.
var fakeCommands = map[string]string{
	"config": `
conf="$(dirname "$0")/../puny.conf"
case "$1" in
  show) printf '{"file": "%s", "settings": [
    {"key": "MLX_BASE_URL", "source": "default", "value": "http://127.0.0.1:9/v1", "group": "server", "help": "Where it listens."},
    {"key": "QWEN_TEMP", "source": "puny.conf", "value": "0.7", "group": "server", "help": "Temperature."},
    {"key": "QWEN_REPO", "source": "default", "value": "org/m", "group": "model", "help": "The model."},
    {"key": "TEAM_SOLVE_AGENT", "source": "environment", "value": "pi", "group": "tools", "help": "The agent."}]}' "$conf" ;;
  set) shift; echo "$@" >> "$(dirname "$0")/../set.log"; echo "Saved." ;;
  raw) [ ! -f "$conf" ] || cat "$conf" ;;
  replace) text="$(cat)"; case "$text" in *BOGUS*) echo "error: BOGUS is not a setting" >&2; exit 1 ;; esac
    printf '%s\n' "$text" > "$conf"; echo "Saved." ;;
esac`,
	"models": `
case "$1" in
  presets) echo '[{"name": "small", "repo": "org/m", "revision": "` + strings.Repeat("a", 40) + `", "context_tokens": 4096, "temp": "0.6", "downloaded": true, "about": "a small one"}]' ;;
  list) echo '[{"name": "org/m", "commit": "` + strings.Repeat("a", 40) + `", "folder": "/x", "verified": true, "size_bytes": 1073741824}]' ;;
  search) echo '{"results": [{"name": "org/found", "source": "huggingface", "url": "https://huggingface.co/org/found", "downloads": 12345, "updated": "2026-10-01T00:00:00"}], "problems": []}' ;;
  *) exit 0 ;;
esac`,
	"integrate":    `echo "opencode: not installed"`,
	"bench-agents": `echo "label  task  agent  passed"`,
	"evals":        `echo "no results under state/evals yet" >&2; exit 1`,
	"team-solve":   `echo "team-solve in $1"; cat "$1/task.md"`,
}

func newTestApp(t *testing.T) (*App, string) {
	t.Helper()
	repo := fakeRepo(t, fakeCommands)
	for _, task := range []string{"billing", "todo"} {
		os.MkdirAll(filepath.Join(repo, "bench", task), 0o755)
		os.WriteFile(filepath.Join(repo, "bench", task, "prompt.txt"), []byte("x"), 0o644)
	}
	example := filepath.Join(repo, "workflows", "team-solve", "example", "tests")
	os.MkdirAll(example, 0o755)
	os.WriteFile(filepath.Join(example, "../task.md"), []byte("# Example task\n"), 0o644)
	os.WriteFile(filepath.Join(example, "1.in"), []byte("1 2\n"), 0o644)
	os.WriteFile(filepath.Join(example, "1.out"), []byte("3\n"), 0o644)
	a, err := NewApp(Options{Repo: repo, Origin: origin, HTMX: []byte("/* htmx */"), HTMXVersion: "2.0.11"})
	if err != nil {
		t.Fatal(err)
	}
	return a, repo
}

func call(a *App, method, path string, form url.Values, headers map[string]string) *httptest.ResponseRecorder {
	var body *strings.Reader
	if form != nil {
		body = strings.NewReader(form.Encode())
	} else {
		body = strings.NewReader("")
	}
	r := httptest.NewRequest(method, origin+path, body)
	if form != nil {
		r.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	}
	r.Header.Set("HX-Request", "true")
	for k, v := range headers {
		if k == "Host" {
			r.Host = v
		} else {
			r.Header.Set(k, v)
		}
	}
	w := httptest.NewRecorder()
	a.ServeHTTP(w, r)
	return w
}

func TestRefusesOtherSites(t *testing.T) {
	a, _ := newTestApp(t)
	if w := call(a, "GET", "/api/status", nil, map[string]string{"Host": "evil.example"}); w.Code != http.StatusForbidden {
		t.Errorf("a foreign Host got %d", w.Code)
	}
	if w := call(a, "GET", "/api/status", nil, map[string]string{"Host": "127.0.0.1:8090"}); w.Code == http.StatusForbidden {
		t.Errorf("its own Host was refused")
	}
	form := url.Values{"model": {"org/m@" + strings.Repeat("a", 40)}}
	if w := call(a, "POST", "/jobs/download", form, map[string]string{"Origin": "http://evil.example"}); w.Code != http.StatusForbidden {
		t.Errorf("a foreign Origin got %d", w.Code)
	}
	if w := call(a, "POST", "/jobs/download", form, map[string]string{"Sec-Fetch-Site": "cross-site"}); w.Code != http.StatusForbidden {
		t.Errorf("a cross-site POST got %d", w.Code)
	}
}

func TestEveryPageRenders(t *testing.T) {
	a, _ := newTestApp(t)
	for path, want := range map[string]string{
		"/":                    "Server",
		"/models":              "a small one",
		"/discover":            "Discover",
		"/discover?q=x":        "org/found",
		"/chat":                `id="prompt"`,
		"/workflows":           "New problem",
		"/workflows?example=1": "# Example task",
		"/benchmarks":          "no results under state/evals yet",
		"/agents":              "opencode: not installed",
		"/jobs":                "No jobs yet.",
		"/settings":            "TEAM_SOLVE_AGENT",
		"/settings/file":       "There is no puny.conf yet",
		"/settings/about":      "2.0.11",
		"/fragments/statusbar": "memory",
	} {
		w := call(a, "GET", path, nil, nil)
		if w.Code != http.StatusOK || !strings.Contains(w.Body.String(), want) {
			t.Errorf("%s: %d, missing %q", path, w.Code, want)
		}
		if !strings.Contains(w.Header().Get("Content-Security-Policy"), "script-src 'self'") {
			t.Errorf("%s has no Content-Security-Policy", path)
		}
	}
	if w := call(a, "GET", "/static/htmx.min.js", nil, nil); w.Body.String() != "/* htmx */" {
		t.Errorf("htmx: %q", w.Body.String())
	}
	if w := call(a, "GET", "/static/../main.go", nil, nil); w.Code == http.StatusOK {
		t.Error("a file outside static/ was served")
	}
}

func TestSettingsSaveOnlyWhatChanged(t *testing.T) {
	a, repo := newTestApp(t)
	form := url.Values{
		"set.MLX_BASE_URL": {""}, "loaded.MLX_BASE_URL": {""},
		"set.QWEN_TEMP": {"0.4"}, "loaded.QWEN_TEMP": {"0.7"},
		"set.QWEN_REPO": {""}, "loaded.QWEN_REPO": {""},
		// The environment's setting is not editable, even if sent.
		"set.TEAM_SOLVE_AGENT": {"codex"}, "loaded.TEAM_SOLVE_AGENT": {""},
	}
	w := call(a, "POST", "/settings", form, nil)
	if w.Code != http.StatusOK || !strings.Contains(w.Body.String(), "Saved.") {
		t.Fatalf("%d %s", w.Code, w.Body.String())
	}
	log, _ := os.ReadFile(filepath.Join(repo, "set.log"))
	if string(log) != "QWEN_TEMP=0.4\n" {
		t.Fatalf("bin/config set got %q", log)
	}
	form.Set("set.QWEN_TEMP", "0.7")
	if w := call(a, "POST", "/settings", form, nil); !strings.Contains(w.Body.String(), "Nothing changed.") {
		t.Fatalf("%s", w.Body.String())
	}
}

func TestFileEditorChecksForChangesMadeMeanwhile(t *testing.T) {
	a, repo := newTestApp(t)
	conf := filepath.Join(repo, "puny.conf")
	empty := hashText("")
	w := call(a, "POST", "/settings/file", url.Values{"text": {"QWEN_TEMP=0.5"}, "hash": {empty}}, nil)
	if w.Code != http.StatusOK {
		t.Fatalf("%d %s", w.Code, w.Body.String())
	}
	if data, _ := os.ReadFile(conf); string(data) != "QWEN_TEMP=0.5\n" {
		t.Fatalf("puny.conf is %q", data)
	}
	// The editor still holds the empty file it loaded: refused, file kept.
	w = call(a, "POST", "/settings/file", url.Values{"text": {"QWEN_TEMP=0.9"}, "hash": {empty}}, nil)
	if w.Code != http.StatusConflict || !strings.Contains(w.Body.String(), "changed since this page loaded it") {
		t.Fatalf("%d %s", w.Code, w.Body.String())
	}
	if data, _ := os.ReadFile(conf); string(data) != "QWEN_TEMP=0.5\n" {
		t.Fatalf("puny.conf is %q", data)
	}
	// A file bin/config refuses is not saved, and the editor keeps the text.
	w = call(a, "POST", "/settings/file", url.Values{"text": {"BOGUS=1"}, "hash": {hashText("QWEN_TEMP=0.5\n")}}, nil)
	if w.Code != http.StatusBadRequest || !strings.Contains(w.Body.String(), "BOGUS is not a setting") || !strings.Contains(w.Body.String(), ">BOGUS=1") {
		t.Fatalf("%d %s", w.Code, w.Body.String())
	}
}

func TestTeamSolveWritesTheProblemAndRunsIt(t *testing.T) {
	a, repo := newTestApp(t)
	form := url.Values{"name": {"sum"}, "task": {"Add two numbers.\r\n"}, "in1": {"1 2"}, "out1": {"3"},
		"in2": {""}, "out2": {""}, "in3": {"5 5"}, "out3": {"10"}, "agent": {"opencode"}}
	w := call(a, "POST", "/workflows/team-solve", form, nil)
	if w.Code != http.StatusNoContent || !strings.HasPrefix(w.Header().Get("HX-Redirect"), "/jobs/") {
		t.Fatalf("%d %s", w.Code, w.Body.String())
	}
	dir := filepath.Join(repo, "state", "team-solve", "sum")
	for name, want := range map[string]string{"task.md": "Add two numbers.\n", "tests/1.in": "1 2\n", "tests/1.out": "3\n",
		"tests/2.in": "5 5\n", "tests/2.out": "10\n"} {
		if data, _ := os.ReadFile(filepath.Join(dir, name)); string(data) != want {
			t.Errorf("%s is %q, want %q", name, data, want)
		}
	}
	id := strings.TrimPrefix(w.Header().Get("HX-Redirect"), "/jobs/")
	s := waitFor(t, a.jobs, id, "done")
	if s.Env["TEAM_SOLVE_AGENT"] != "opencode" || s.Folder != dir {
		t.Fatalf("job: %+v", s.Job)
	}
	page := call(a, "GET", "/jobs/"+id, nil, nil).Body.String()
	if !strings.Contains(page, "team-solve in "+dir) || !strings.Contains(page, "task.md") {
		t.Fatalf("the job page lacks the output or the problem's files: %s", page)
	}

	// The same name again is refused, and the existing problem runs again.
	if w := call(a, "POST", "/workflows/team-solve", form, nil); w.Code != http.StatusBadRequest || !strings.Contains(w.Body.String(), "exists already") {
		t.Fatalf("%d %s", w.Code, w.Body.String())
	}
	for _, bad := range []url.Values{
		{"name": {"../up"}, "task": {"x"}, "in1": {"1"}, "out1": {"1"}},
		{"name": {"half"}, "task": {"x"}, "in1": {"1"}},
		{"name": {"none"}, "task": {"x"}},
		{"existing": {"../../etc"}},
	} {
		if w := call(a, "POST", "/workflows/team-solve", bad, nil); w.Code != http.StatusBadRequest {
			t.Errorf("%v: %d", bad, w.Code)
		}
	}
}

func TestBenchmarkFormsAreChecked(t *testing.T) {
	a, _ := newTestApp(t)
	for _, form := range []url.Values{
		{"label": {"bad label"}, "agent": {"pi"}, "task": {"todo"}},
		{"label": {"kat"}, "task": {"todo"}},
		{"label": {"kat"}, "agent": {"pi"}, "task": {"nope"}},
		{"label": {"kat"}, "agent": {"pi"}, "task": {"todo"}, "runs": {"0"}},
	} {
		if w := call(a, "POST", "/benchmarks/agents", form, nil); w.Code != http.StatusBadRequest {
			t.Errorf("%v: %d", form, w.Code)
		}
	}
	w := call(a, "POST", "/benchmarks/agents", url.Values{"label": {"kat"}, "agent": {"codex", "pi", "evil"}, "task": {"todo"}, "runs": {"2"}}, nil)
	if w.Code != http.StatusNoContent {
		t.Fatalf("%d %s", w.Code, w.Body.String())
	}
	s, _ := a.jobs.Get(strings.TrimPrefix(w.Header().Get("HX-Redirect"), "/jobs/"))
	if s.Env["BENCH_AGENTS"] != "pi codex" || s.Env["BENCH_TASKS"] != "todo" || s.Env["BENCH_RUNS"] != "2" {
		t.Fatalf("env: %v", s.Env)
	}
	if w := call(a, "POST", "/benchmarks/evals", url.Values{"label": {"kat"}, "suite": {"speed"}, "thinking": {"maybe"}}, nil); w.Code != http.StatusBadRequest {
		t.Errorf("thinking=maybe: %d", w.Code)
	}
}

func TestVerifyFormIsChecked(t *testing.T) {
	a, _ := newTestApp(t)
	page := call(a, "GET", "/benchmarks", nil, nil).Body.String()
	if !strings.Contains(page, `<option value="small">small: a small one</option>`) {
		t.Fatalf("the presets are missing: %s", page)
	}
	for _, form := range []url.Values{
		{"preset": {"nope"}, "step": {"smoke"}, "agent": {"pi"}},
		{"preset": {"small"}, "agent": {"pi"}},
		{"preset": {"small"}, "step": {"bench"}},
		{"preset": {"small"}, "step": {"lint"}, "agent": {"pi"}},
	} {
		if w := call(a, "POST", "/benchmarks/verify", form, nil); w.Code != http.StatusBadRequest {
			t.Errorf("%v: %d", form, w.Code)
		}
	}
	w := call(a, "POST", "/benchmarks/verify", url.Values{"preset": {"small"}, "step": {"bench", "smoke"}, "agent": {"codex", "pi"}}, nil)
	if w.Code != http.StatusNoContent {
		t.Fatalf("%d %s", w.Code, w.Body.String())
	}
	s, _ := a.jobs.Get(strings.TrimPrefix(w.Header().Get("HX-Redirect"), "/jobs/"))
	if s.Command != "bin/verify" || strings.Join(s.Args, " ") != "small" ||
		s.Env["VERIFY_STEPS"] != "smoke bench" || s.Env["VERIFY_AGENTS"] != "pi codex" {
		t.Fatalf("job: %+v", s.Job)
	}
	// The fake repo has no bin/verify, so the job fails at once and the next may start.
	waitFor(t, a.jobs, s.ID, "failed (exit 127)")
	// The evals alone need no agent.
	w = call(a, "POST", "/benchmarks/verify", url.Values{"preset": {"small"}, "step": {"evals"}}, nil)
	if w.Code != http.StatusNoContent {
		t.Fatalf("%d %s", w.Code, w.Body.String())
	}
	s, _ = a.jobs.Get(strings.TrimPrefix(w.Header().Get("HX-Redirect"), "/jobs/"))
	if _, set := s.Env["VERIFY_AGENTS"]; set || s.Env["VERIFY_STEPS"] != "evals" {
		t.Fatalf("env: %v", s.Env)
	}
}

func TestDownloadsNeedAPinnedModel(t *testing.T) {
	a, _ := newTestApp(t)
	for _, spec := range []string{"org/m", "org/m@main", "bad name@" + strings.Repeat("a", 40), "../x@" + strings.Repeat("a", 40)} {
		if w := call(a, "POST", "/jobs/download", url.Values{"model": {spec}}, nil); w.Code != http.StatusBadRequest {
			t.Errorf("%s: %d", spec, w.Code)
		}
	}
}

func TestChatWithoutAServer(t *testing.T) {
	a, _ := newTestApp(t)
	r := httptest.NewRequest("POST", origin+"/api/chat", strings.NewReader(`{"messages": []}`))
	w := httptest.NewRecorder()
	a.ServeHTTP(w, r)
	if w.Code != http.StatusBadGateway || !strings.Contains(w.Body.String(), "Start it on the Server page") {
		t.Fatalf("%d %s", w.Code, w.Body.String())
	}
}
