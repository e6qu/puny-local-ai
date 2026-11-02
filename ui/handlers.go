package main

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"runtime"
	"sort"
	"strconv"
	"strings"
	"time"
)

var (
	labelPattern  = regexp.MustCompile(`^[A-Za-z0-9_-][A-Za-z0-9._-]*$`)
	modelPattern  = regexp.MustCompile(`^(modelscope:)?[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$`) // lib/sources.py's REPO
	commitPattern = regexp.MustCompile(`^[0-9a-f]{40}$`)
	agents        = []string{"opencode", "pi", "codex"}
	evalSuites    = []string{"tool-calls", "coding", "long-context", "speed"}
	serverActions = map[string]bool{"start": true, "stop": true, "restart": true}
)

const (
	quick = 2 * time.Minute  // a command that answers at once, or nearly
	slow  = 10 * time.Minute // one that reads model files over the network
)

func (a *App) routes() {
	m := a.mux
	m.HandleFunc("GET /static/", a.static)
	m.HandleFunc("GET /{$}", a.dashboard)
	m.HandleFunc("GET /api/status", a.apiStatus)
	m.HandleFunc("POST /api/chat", a.chat)
	m.HandleFunc("GET /fragments/statusbar", func(w http.ResponseWriter, r *http.Request) {
		a.fragment(w, "statusbar", a.statusBar(r))
	})
	m.HandleFunc("GET /fragments/server", a.serverCard)
	m.HandleFunc("POST /server/{action}", a.controlServer)

	m.HandleFunc("GET /models", a.modelsPage)
	m.HandleFunc("POST /models/preset", a.usePreset)
	m.HandleFunc("POST /models/use", a.useModel)
	m.HandleFunc("POST /models/remove", a.removeModel)
	m.HandleFunc("GET /discover", a.discoverPage)
	m.HandleFunc("GET /discover/info", a.modelInfo)

	m.HandleFunc("GET /chat", func(w http.ResponseWriter, r *http.Request) { a.render(w, r, "chat", "Chat", "chat", nil) })

	m.HandleFunc("GET /workflows", a.workflowsPage)
	m.HandleFunc("POST /workflows/team-solve", a.startTeamSolve)
	m.HandleFunc("GET /benchmarks", a.benchmarksPage)
	m.HandleFunc("POST /benchmarks/agents", a.startBench)
	m.HandleFunc("POST /benchmarks/evals", a.startEvals)
	m.HandleFunc("POST /benchmarks/verify", a.startVerify)

	m.HandleFunc("GET /agents", a.agentsPage)
	m.HandleFunc("POST /agents/integrate/{action}", a.integrate)
	m.HandleFunc("POST /agents/smoke-test", a.startSmoke)

	m.HandleFunc("POST /jobs/download", a.startDownload)
	m.HandleFunc("POST /jobs/setup", a.startSetup)
	m.HandleFunc("GET /jobs", func(w http.ResponseWriter, r *http.Request) {
		a.render(w, r, "jobs", "Jobs", "jobs", a.jobs.List())
	})
	m.HandleFunc("GET /jobs/{id}", a.jobPage)
	m.HandleFunc("GET /fragments/jobs/{id}", a.jobOutput)
	m.HandleFunc("POST /jobs/{id}/stop", a.stopJob)

	m.HandleFunc("GET /settings", a.settingsPage)
	m.HandleFunc("POST /settings", a.saveSettings)
	m.HandleFunc("GET /settings/file", a.filePage)
	m.HandleFunc("POST /settings/file", a.saveFile)
	m.HandleFunc("GET /settings/about", a.aboutPage)
}

// ------------------------------------------------------------ server

func (a *App) currentStatus(r *http.Request) (ServerStatus, error) {
	settings, err := a.settings(r.Context())
	if err != nil {
		return ServerStatus{}, err
	}
	return a.server.Status(settings.Get("MLX_BASE_URL")), nil
}

func (a *App) apiStatus(w http.ResponseWriter, r *http.Request) {
	status, err := a.currentStatus(r)
	if err != nil {
		httpError(w, http.StatusInternalServerError, err.Error())
		return
	}
	writeJSON(w, http.StatusOK, status)
}

// ServerCard is the server panel on the dashboard.
type ServerCard struct {
	Status   ServerStatus
	Settings *Settings
	Notice   *Notice
}

func (a *App) serverCardData(r *http.Request, n *Notice) (ServerCard, error) {
	settings, err := a.settings(r.Context())
	if err != nil {
		return ServerCard{}, err
	}
	return ServerCard{Status: a.server.Status(settings.Get("MLX_BASE_URL")), Settings: settings, Notice: n}, nil
}

func (a *App) dashboard(w http.ResponseWriter, r *http.Request) {
	card, err := a.serverCardData(r, nil)
	data := map[string]any{"Card": card, "Error": errText(err), "Jobs": firstJobs(a.jobs.List(), 5)}
	var local []LocalModel
	if err := a.runJSON(r.Context(), quick, &local, "models", "list", "--json"); err == nil {
		data["Models"] = len(local)
	}
	a.render(w, r, "dashboard", "Server", "server", data)
}

func firstJobs(jobs []JobState, n int) []JobState {
	if len(jobs) > n {
		return jobs[:n]
	}
	return jobs
}

func errText(err error) string {
	if err == nil {
		return ""
	}
	return err.Error()
}

func (a *App) serverCard(w http.ResponseWriter, r *http.Request) {
	card, err := a.serverCardData(r, nil)
	if err != nil {
		a.notice(w, bad(err))
		return
	}
	a.fragment(w, "server-card", card)
}

func (a *App) controlServer(w http.ResponseWriter, r *http.Request) {
	action := r.PathValue("action")
	var err error
	if !serverActions[action] {
		err = fmt.Errorf("the action must be start, stop or restart, not %q", action)
	}
	if err == nil && (action == "stop" || action == "restart") {
		err = a.server.Stop()
	}
	if err == nil && (action == "start" || action == "restart") {
		err = a.server.Start()
	}
	n := good(map[string]string{"start": "Starting: loading a model takes a minute; the state updates by itself.",
		"restart": "Restarting: loading a model takes a minute; the state updates by itself.",
		"stop":    "Stopped."}[action])
	if err != nil {
		n = bad(err)
		w.Header().Set("HX-Retarget", "#server-notice")
		w.WriteHeader(http.StatusBadRequest)
		a.notice(w, n)
		return
	}
	card, cardErr := a.serverCardData(r, n)
	if cardErr != nil {
		a.notice(w, bad(cardErr))
		return
	}
	a.fragment(w, "server-card", card)
}

func (a *App) integrate(w http.ResponseWriter, r *http.Request) {
	action := r.PathValue("action")
	if action != "install" && action != "uninstall" {
		w.WriteHeader(http.StatusBadRequest)
		a.notice(w, bad(fmt.Errorf("the action must be install or uninstall")))
		return
	}
	out, err := a.run(r.Context(), quick, "", "integrate", action)
	if err != nil {
		w.WriteHeader(http.StatusBadRequest)
		a.notice(w, bad(err))
		return
	}
	a.notice(w, good(out))
}

// chat passes a chat request to the model server and its streamed reply
// back, always for default_model, the model the server was started with.
func (a *App) chat(w http.ResponseWriter, r *http.Request) {
	settings, err := a.settings(r.Context())
	if err != nil {
		httpError(w, http.StatusInternalServerError, err.Error())
		return
	}
	var body map[string]any
	if err := decodeJSON(r, &body); err != nil {
		httpError(w, http.StatusBadRequest, "the body must be a JSON object")
		return
	}
	body["model"] = "default_model"
	baseURL := settings.Get("MLX_BASE_URL")
	upstream, err := postJSON(r.Context(), baseURL+"/chat/completions", body)
	if err != nil {
		httpError(w, http.StatusBadGateway, fmt.Sprintf("cannot reach the model server at %s: %v. Start it on the Server page.", baseURL, err))
		return
	}
	defer upstream.Body.Close()
	w.Header().Set("Content-Type", upstream.Header.Get("Content-Type"))
	w.WriteHeader(upstream.StatusCode)
	flusher, _ := w.(http.Flusher)
	buffer := make([]byte, 65536)
	for {
		n, err := upstream.Body.Read(buffer)
		if n > 0 {
			if _, werr := w.Write(buffer[:n]); werr != nil {
				return
			}
			if flusher != nil {
				flusher.Flush()
			}
		}
		if err != nil {
			return
		}
	}
}

// ------------------------------------------------------------ models

type ModelsPage struct {
	Presets  []Preset
	Local    []LocalModel
	Repo     string // the model the settings name
	Revision string
	Preset   string
	Notice   *Notice
	Problems []string
}

func (a *App) modelsData(r *http.Request, n *Notice) ModelsPage {
	data := ModelsPage{Notice: n}
	if err := a.runJSON(r.Context(), quick, &data.Presets, "models", "presets", "--json"); err != nil {
		data.Problems = append(data.Problems, err.Error())
	}
	if err := a.runJSON(r.Context(), quick, &data.Local, "models", "list", "--json"); err != nil {
		data.Problems = append(data.Problems, err.Error())
	}
	if settings, err := a.settings(r.Context()); err != nil {
		data.Problems = append(data.Problems, err.Error())
	} else {
		data.Repo, data.Revision, data.Preset = settings.Get("QWEN_REPO"), settings.Get("QWEN_REVISION"), settings.Get("QWEN_PRESET")
		if data.Preset == "" && settingSource(settings, "QWEN_REPO") == "default" {
			data.Preset = "kat"
		}
	}
	return data
}

func settingSource(s *Settings, key string) string {
	for _, setting := range s.Settings {
		if setting.Key == key {
			return setting.Source
		}
	}
	return ""
}

func (a *App) modelsPage(w http.ResponseWriter, r *http.Request) {
	a.render(w, r, "models", "Models", "models", a.modelsData(r, nil))
}

// setConfig saves changes through bin/config set, which checks the file as
// a whole before it saves anything.
func (a *App) setConfig(r *http.Request, values map[string]string) (string, error) {
	keys := make([]string, 0, len(values))
	for k := range values {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	args := []string{"set"}
	for _, k := range keys {
		args = append(args, k+"="+values[k])
	}
	return a.run(r.Context(), quick, "", "config", args...)
}

func (a *App) usePreset(w http.ResponseWriter, r *http.Request) {
	name := r.FormValue("name")
	out, err := a.setConfig(r, map[string]string{"QWEN_PRESET": name, "QWEN_REPO": "", "QWEN_REVISION": "", "QWEN_CONTEXT": ""})
	n := good(out)
	if err != nil {
		n = bad(err)
	}
	a.fragment(w, "models-body", a.modelsData(r, n))
}

func (a *App) useModel(w http.ResponseWriter, r *http.Request) {
	name, commit, context := r.FormValue("name"), r.FormValue("commit"), r.FormValue("context")
	var n *Notice
	if !modelPattern.MatchString(name) || !commitPattern.MatchString(commit) {
		n = bad(fmt.Errorf("give the model as NAME and its full 40-character commit"))
	} else if _, err := strconv.Atoi(context); err != nil {
		n = bad(fmt.Errorf("config.json states no context window; set QWEN_CONTEXT from the model card in Settings"))
	} else if out, err := a.setConfig(r, map[string]string{"QWEN_PRESET": "", "QWEN_REPO": name, "QWEN_REVISION": commit, "QWEN_CONTEXT": context}); err != nil {
		n = bad(err)
	} else {
		n = good(fmt.Sprintf("The server will run %s with a %s-token window, from its config.json; check that against the model card. Restart the server to switch.\n%s", name, context, out))
	}
	if r.FormValue("from") == "discover" {
		a.notice(w, n)
		return
	}
	a.fragment(w, "models-body", a.modelsData(r, n))
}

func (a *App) removeModel(w http.ResponseWriter, r *http.Request) {
	out, err := a.run(r.Context(), quick, "", "models", "remove", r.FormValue("spec"))
	n := good(out)
	if err != nil {
		n = bad(err)
	}
	a.fragment(w, "models-body", a.modelsData(r, n))
}

type DiscoverPage struct {
	Model    string // a model to check at once, from the Models page
	Query    string
	Source   string
	Results  []SearchResult
	Problems []string
	Error    string
	Searched bool
}

func (a *App) discoverPage(w http.ResponseWriter, r *http.Request) {
	data := DiscoverPage{Query: strings.TrimSpace(r.FormValue("q")), Source: r.FormValue("source"), Model: r.FormValue("model")}
	if data.Source == "" {
		data.Source = "all"
	}
	if data.Query != "" {
		data.Searched = true
		var found struct {
			Results  []SearchResult `json:"results"`
			Problems []string       `json:"problems"`
		}
		if err := a.runJSON(r.Context(), slow, &found, "models", "search", data.Query, "--source", data.Source, "--limit", "20", "--json"); err != nil {
			data.Error = err.Error()
		}
		data.Results, data.Problems = found.Results, found.Problems
	}
	if r.Header.Get("HX-Request") == "true" && r.Header.Get("HX-Target") == "results" {
		a.fragment(w, "discover-results", data)
		return
	}
	a.render(w, r, "discover", "Discover", "discover", data)
}

func (a *App) modelInfo(w http.ResponseWriter, r *http.Request) {
	var info ModelInfo
	if err := a.runJSON(r.Context(), slow, &info, "models", "info", r.FormValue("model"), "--json"); err != nil {
		a.notice(w, bad(err))
		return
	}
	a.fragment(w, "model-info", info)
}

// ------------------------------------------------------------ jobs

func (a *App) startJob(w http.ResponseWriter, r *http.Request, kind, title, command string, args []string, env map[string]string, folder string) {
	job, err := a.jobs.Start(kind, title, command, args, env, folder)
	if err != nil {
		w.WriteHeader(http.StatusConflict)
		a.notice(w, bad(err))
		return
	}
	redirect(w, r, "/jobs/"+job.ID)
}

func (a *App) startDownload(w http.ResponseWriter, r *http.Request) {
	spec := r.FormValue("model")
	name, commit, _ := strings.Cut(spec, "@")
	if !modelPattern.MatchString(name) || !commitPattern.MatchString(commit) {
		w.WriteHeader(http.StatusBadRequest)
		a.notice(w, bad(fmt.Errorf("give the model as NAME@COMMIT, with the full commit that Check shows")))
		return
	}
	a.startJob(w, r, "download", "Download "+name, "models", []string{"download", spec}, nil, "")
}

func (a *App) startSetup(w http.ResponseWriter, r *http.Request) {
	item := r.FormValue("item")
	if !labelPattern.MatchString(item) {
		w.WriteHeader(http.StatusBadRequest)
		a.notice(w, bad(fmt.Errorf("no setup item %q", item)))
		return
	}
	a.startJob(w, r, "setup", "Set up "+item, "setup", []string{item}, nil, "")
}

func (a *App) startSmoke(w http.ResponseWriter, r *http.Request) {
	a.startJob(w, r, "smoke", "Smoke test", "smoke-test", nil, nil, "")
}

type JobPage struct {
	Job    JobState
	Output string
	Files  []ProblemFile
	Done   bool
}

// ProblemFile is a file team-solve writes, shown on its job's page.
type ProblemFile struct {
	Name string
	Text string
}

var teamSolveFiles = []string{"task.md", "plan.md", "notes.md", "solution.py", "test-output.log", "review.md", "final-test-output.log"}

func (a *App) jobData(id string) (JobPage, error) {
	job, err := a.jobs.Get(id)
	if err != nil {
		return JobPage{}, err
	}
	page := JobPage{Job: job, Output: a.jobs.Output(job.Job, 256*1024), Done: !job.Running()}
	if job.Folder != "" {
		for _, name := range teamSolveFiles {
			path := filepath.Join(job.Folder, name)
			if readable(path) {
				data, _ := os.ReadFile(path)
				page.Files = append(page.Files, ProblemFile{name, string(data)})
			}
		}
	}
	return page, nil
}

func (a *App) jobPage(w http.ResponseWriter, r *http.Request) {
	data, err := a.jobData(r.PathValue("id"))
	if err != nil {
		w.WriteHeader(http.StatusNotFound)
		a.render(w, r, "jobs", "Jobs", "jobs", a.jobs.List())
		return
	}
	a.render(w, r, "job", data.Job.Title, "jobs", data)
}

func (a *App) jobOutput(w http.ResponseWriter, r *http.Request) {
	data, err := a.jobData(r.PathValue("id"))
	if err != nil {
		http.NotFound(w, r)
		return
	}
	if data.Done {
		// The page reloads once, to show the final state and files.
		w.Header().Set("HX-Refresh", "true")
	}
	a.fragment(w, "job-output", data)
}

func (a *App) stopJob(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("id")
	if err := a.jobs.Stop(id); err != nil {
		w.WriteHeader(http.StatusBadRequest)
		a.notice(w, bad(err))
		return
	}
	redirect(w, r, "/jobs/"+id)
}

// ------------------------------------------------------------ workflows

type WorkflowsPage struct {
	Problems []string
	Example  TeamSolveForm
	Runs     []JobState
	Settings *Settings
	Error    string
}

// TeamSolveForm is a new problem: its task and up to three samples.
type TeamSolveForm struct {
	Name    string
	Task    string
	Inputs  [3]string
	Outputs [3]string
}

func (a *App) problemsRoot() string { return filepath.Join(a.repo, "state", "team-solve") }

func (a *App) workflowsPage(w http.ResponseWriter, r *http.Request) {
	data := WorkflowsPage{}
	dirs, _ := filepath.Glob(filepath.Join(a.problemsRoot(), "*", "task.md"))
	for _, dir := range dirs {
		data.Problems = append(data.Problems, filepath.Base(filepath.Dir(dir)))
	}
	if r.FormValue("example") != "" {
		data.Example = a.exampleProblem()
	}
	for _, job := range a.jobs.List() {
		if job.Kind == "team-solve" {
			data.Runs = append(data.Runs, job)
		}
	}
	settings, err := a.settings(r.Context())
	data.Settings, data.Error = settings, errText(err)
	a.render(w, r, "workflows", "Workflows", "workflows", data)
}

// exampleProblem is the bundled workflows/team-solve/example, as a form.
func (a *App) exampleProblem() TeamSolveForm {
	dir := filepath.Join(a.repo, "workflows", "team-solve", "example")
	f := TeamSolveForm{Name: "example"}
	task, _ := os.ReadFile(filepath.Join(dir, "task.md"))
	f.Task = string(task)
	for i := range 3 {
		in, _ := os.ReadFile(filepath.Join(dir, "tests", fmt.Sprintf("%d.in", i+1)))
		out, _ := os.ReadFile(filepath.Join(dir, "tests", fmt.Sprintf("%d.out", i+1)))
		f.Inputs[i], f.Outputs[i] = string(in), string(out)
	}
	return f
}

// writeProblem makes state/team-solve/NAME with task.md and tests/N.in and
// N.out, refusing to replace a problem that is there.
func (a *App) writeProblem(f TeamSolveForm) (string, error) {
	if !labelPattern.MatchString(f.Name) {
		return "", fmt.Errorf("the name may contain only letters, digits, '.', '_' and '-'")
	}
	if strings.TrimSpace(f.Task) == "" {
		return "", fmt.Errorf("write the problem in the task")
	}
	dir := filepath.Join(a.problemsRoot(), f.Name)
	if fileExists(dir) {
		return "", fmt.Errorf("a problem called %s exists already in %s; pick it under Run again, or use another name", f.Name, dir)
	}
	samples := 0
	for i := range 3 {
		hasIn, hasOut := strings.TrimSpace(f.Inputs[i]) != "", strings.TrimSpace(f.Outputs[i]) != ""
		if hasIn != hasOut {
			return "", fmt.Errorf("sample %d needs both its input and its expected output", i+1)
		}
		if hasIn {
			samples++
		}
	}
	if samples == 0 {
		return "", fmt.Errorf("give at least one sample: an input and the output expected for it")
	}
	if err := os.MkdirAll(filepath.Join(dir, "tests"), 0o755); err != nil {
		return "", err
	}
	write := func(name, text string) error {
		text = strings.ReplaceAll(text, "\r\n", "\n")
		if !strings.HasSuffix(text, "\n") {
			text += "\n"
		}
		return os.WriteFile(filepath.Join(dir, name), []byte(text), 0o644)
	}
	if err := write("task.md", f.Task); err != nil {
		return "", err
	}
	n := 0
	for i := range 3 {
		if strings.TrimSpace(f.Inputs[i]) == "" {
			continue
		}
		n++
		if err := write(fmt.Sprintf("tests/%d.in", n), f.Inputs[i]); err != nil {
			return "", err
		}
		if err := write(fmt.Sprintf("tests/%d.out", n), f.Outputs[i]); err != nil {
			return "", err
		}
	}
	return dir, nil
}

func (a *App) startTeamSolve(w http.ResponseWriter, r *http.Request) {
	var dir string
	var err error
	if existing := r.FormValue("existing"); existing != "" {
		if !labelPattern.MatchString(existing) || !fileExists(filepath.Join(a.problemsRoot(), existing, "task.md")) {
			err = fmt.Errorf("no problem called %q", existing)
		}
		dir = filepath.Join(a.problemsRoot(), existing)
	} else {
		f := TeamSolveForm{Name: strings.TrimSpace(r.FormValue("name")), Task: r.FormValue("task")}
		for i := range 3 {
			f.Inputs[i], f.Outputs[i] = r.FormValue(fmt.Sprintf("in%d", i+1)), r.FormValue(fmt.Sprintf("out%d", i+1))
		}
		dir, err = a.writeProblem(f)
	}
	if err != nil {
		w.WriteHeader(http.StatusBadRequest)
		a.notice(w, bad(err))
		return
	}
	env := map[string]string{}
	if agent := r.FormValue("agent"); agent != "" {
		env["TEAM_SOLVE_AGENT"] = agent
	}
	if timeout := strings.TrimSpace(r.FormValue("timeout")); timeout != "" {
		env["TEAM_SOLVE_TIMEOUT"] = timeout
	}
	a.startJob(w, r, "team-solve", "team-solve "+filepath.Base(dir), "team-solve", []string{dir}, env, dir)
}

// ------------------------------------------------------------ benchmarks

type BenchmarksPage struct {
	Tasks       []string
	Agents      []string
	Suites      []string
	Presets     []Preset
	Steps       []string
	PresetError string
	BenchReport string
	EvalsReport string
	Runs        []JobState
}

func (a *App) benchmarksPage(w http.ResponseWriter, r *http.Request) {
	data := BenchmarksPage{Agents: agents, Suites: evalSuites, Steps: verifySteps}
	if err := a.runJSON(r.Context(), quick, &data.Presets, "models", "presets", "--json"); err != nil {
		data.PresetError = err.Error()
	}
	dirs, _ := filepath.Glob(filepath.Join(a.repo, "bench", "*", "prompt.txt"))
	for _, dir := range dirs {
		data.Tasks = append(data.Tasks, filepath.Base(filepath.Dir(dir)))
	}
	if out, err := a.run(r.Context(), quick, "", "bench-agents", "report"); err == nil {
		data.BenchReport = out
	} else {
		data.BenchReport = err.Error()
	}
	if out, err := a.run(r.Context(), quick, "", "evals", "report"); err == nil {
		data.EvalsReport = out
	} else {
		data.EvalsReport = err.Error()
	}
	for _, job := range a.jobs.List() {
		if job.Kind == "bench" || job.Kind == "evals" || job.Kind == "verify" {
			data.Runs = append(data.Runs, job)
		}
	}
	a.render(w, r, "benchmarks", "Benchmarks", "benchmarks", data)
}

// chosen is the form's checked values that are among allowed, in its order.
func chosen(r *http.Request, field string, allowed []string) []string {
	r.ParseForm()
	var picked []string
	for _, v := range allowed {
		if has(r.Form[field], v) {
			picked = append(picked, v)
		}
	}
	return picked
}

func has(list []string, value string) bool {
	for _, v := range list {
		if v == value {
			return true
		}
	}
	return false
}

// numbers copies the form's whole-number fields into env, under their
// settings' names; the commands check the values themselves.
func numbers(r *http.Request, env map[string]string, fields map[string]string) error {
	for field, key := range fields {
		value := strings.TrimSpace(r.FormValue(field))
		if value == "" {
			continue
		}
		if n, err := strconv.Atoi(value); err != nil || n <= 0 {
			return fmt.Errorf("%s must be a positive whole number", key)
		}
		env[key] = value
	}
	return nil
}

func (a *App) startBench(w http.ResponseWriter, r *http.Request) {
	label := strings.TrimSpace(r.FormValue("label"))
	var tasks []string
	dirs, _ := filepath.Glob(filepath.Join(a.repo, "bench", "*", "prompt.txt"))
	for _, dir := range dirs {
		tasks = append(tasks, filepath.Base(filepath.Dir(dir)))
	}
	env := map[string]string{}
	picked, pickedTasks := chosen(r, "agent", agents), chosen(r, "task", tasks)
	err := numbers(r, env, map[string]string{"runs": "BENCH_RUNS", "timeout": "BENCH_TIMEOUT"})
	switch {
	case !labelPattern.MatchString(label):
		err = fmt.Errorf("the label names the model, such as kat; it may contain only letters, digits, '.', '_' and '-'")
	case len(picked) == 0:
		err = fmt.Errorf("pick at least one agent")
	case len(pickedTasks) == 0:
		err = fmt.Errorf("pick at least one task")
	}
	if err != nil {
		w.WriteHeader(http.StatusBadRequest)
		a.notice(w, bad(err))
		return
	}
	env["BENCH_AGENTS"] = strings.Join(picked, " ")
	env["BENCH_TASKS"] = strings.Join(pickedTasks, " ")
	a.startJob(w, r, "bench", "Agent benchmark "+label, "bench-agents", []string{label}, env, "")
}

func (a *App) startEvals(w http.ResponseWriter, r *http.Request) {
	label := strings.TrimSpace(r.FormValue("label"))
	suites := chosen(r, "suite", evalSuites)
	env := map[string]string{}
	err := numbers(r, env, map[string]string{"samples": "EVAL_SAMPLES", "max_output": "EVAL_MAX_OUTPUT",
		"long_context": "EVAL_LONG_CONTEXT", "timeout": "EVAL_TIMEOUT"})
	thinking := r.FormValue("thinking")
	switch {
	case !labelPattern.MatchString(label):
		err = fmt.Errorf("the label names the model, such as kat; it may contain only letters, digits, '.', '_' and '-'")
	case len(suites) == 0:
		err = fmt.Errorf("pick at least one suite")
	case thinking != "" && thinking != "on" && thinking != "off":
		err = fmt.Errorf("thinking must be on or off")
	}
	if err != nil {
		w.WriteHeader(http.StatusBadRequest)
		a.notice(w, bad(err))
		return
	}
	if thinking != "" {
		env["EVAL_THINKING"] = thinking
	}
	a.startJob(w, r, "evals", "Model evals "+label, "evals", append([]string{label}, suites...), env, "")
}

// The steps of bin/verify, in the order it runs them.
var verifySteps = []string{"smoke", "evals", "bench"}

func (a *App) startVerify(w http.ResponseWriter, r *http.Request) {
	preset := r.FormValue("preset")
	var presets []Preset
	err := a.runJSON(r.Context(), quick, &presets, "models", "presets", "--json")
	known := false
	for _, p := range presets {
		known = known || p.Name == preset
	}
	steps, picked := chosen(r, "step", verifySteps), chosen(r, "agent", agents)
	switch {
	case err != nil:
	case !known:
		err = fmt.Errorf("no preset %q", preset)
	case len(steps) == 0:
		err = fmt.Errorf("pick at least one step")
	case len(picked) == 0 && (has(steps, "smoke") || has(steps, "bench")):
		err = fmt.Errorf("pick at least one agent for the smoke test and the benchmark")
	}
	if err != nil {
		w.WriteHeader(http.StatusBadRequest)
		a.notice(w, bad(err))
		return
	}
	env := map[string]string{"VERIFY_STEPS": strings.Join(steps, " ")}
	if len(picked) > 0 {
		env["VERIFY_AGENTS"] = strings.Join(picked, " ")
	}
	a.startJob(w, r, "verify", "Verify "+preset, "verify", []string{preset}, env, "")
}

// ------------------------------------------------------------ agents

type AgentsPage struct {
	Status string
	Error  string
}

func (a *App) agentsPage(w http.ResponseWriter, r *http.Request) {
	out, err := a.run(r.Context(), quick, "", "integrate", "status")
	a.render(w, r, "agents", "Agents", "agents", AgentsPage{Status: out, Error: errText(err)})
}

// ------------------------------------------------------------ settings

type SettingsPage struct {
	Settings *Settings
	Groups   []SettingsGroup
	Notice   *Notice
	Error    string
}

type SettingsGroup struct {
	Name     string
	Title    string
	Settings []Setting
}

var groupTitles = map[string]string{"server": "Server", "model": "Model", "tools": "Workflows and benchmarks"}

func (a *App) settingsData(r *http.Request, n *Notice) SettingsPage {
	data := SettingsPage{Notice: n}
	settings, err := a.settings(r.Context())
	if err != nil {
		data.Error = err.Error()
		return data
	}
	data.Settings = settings
	index := map[string]int{}
	for _, s := range settings.Settings {
		i, ok := index[s.Group]
		if !ok {
			i = len(data.Groups)
			index[s.Group] = i
			title := groupTitles[s.Group]
			if title == "" {
				title = s.Group
			}
			data.Groups = append(data.Groups, SettingsGroup{Name: s.Group, Title: title})
		}
		data.Groups[i].Settings = append(data.Groups[i].Settings, s)
	}
	return data
}

func (a *App) settingsPage(w http.ResponseWriter, r *http.Request) {
	a.render(w, r, "settings", "Settings", "settings", a.settingsData(r, nil))
}

// saveSettings saves the fields that changed since the form was loaded: each
// field carries the file's value it was loaded with, so a change made in the
// terminal meanwhile is kept unless the same field was changed here.
func (a *App) saveSettings(w http.ResponseWriter, r *http.Request) {
	settings, err := a.settings(r.Context())
	if err != nil {
		a.fragment(w, "settings-form", a.settingsData(r, bad(err)))
		return
	}
	r.ParseForm()
	changes := map[string]string{}
	for _, s := range settings.Settings {
		if s.Source == "environment" {
			continue
		}
		values, sent := r.PostForm["set."+s.Key]
		if !sent {
			continue
		}
		value, loaded := strings.TrimSpace(values[0]), r.PostForm.Get("loaded."+s.Key)
		if value != loaded {
			changes[s.Key] = value
		}
	}
	var n *Notice
	if len(changes) == 0 {
		n = good("Nothing changed.")
	} else if out, err := a.setConfig(r, changes); err != nil {
		n = bad(fmt.Errorf("%v\nNothing was saved.", err))
	} else {
		n = good(out)
		if r.FormValue("then") == "restart" {
			n = a.restartAfterSave(r, out)
		}
	}
	a.fragment(w, "settings-form", a.settingsData(r, n))
}

// restartAfterSave restarts a running server, so new server settings apply.
func (a *App) restartAfterSave(r *http.Request, saved string) *Notice {
	status, err := a.currentStatus(r)
	if err != nil {
		return bad(err)
	}
	if status.State != "running" && status.State != "starting" && status.Elsewhere == "" {
		return good(saved + "\nThe server is not running; start it on the Server page.")
	}
	if err := a.server.Stop(); err != nil {
		return bad(err)
	}
	if err := a.server.Start(); err != nil {
		return bad(err)
	}
	return good(saved + "\nRestarting the server with the new settings; the status bar shows when it is ready.")
}

type FilePage struct {
	Path   string
	Text   string
	Hash   string
	Notice *Notice
	Error  string
}

func hashText(text string) string {
	sum := sha256.Sum256([]byte(text))
	return hex.EncodeToString(sum[:])
}

func (a *App) fileData(r *http.Request, n *Notice) FilePage {
	data := FilePage{Notice: n}
	settings, err := a.settings(r.Context())
	if err != nil {
		data.Error = err.Error()
		return data
	}
	data.Path = settings.File
	text, err := a.run(r.Context(), quick, "", "config", "raw")
	if err != nil {
		data.Error = err.Error()
		return data
	}
	data.Text, data.Hash = text, hashText(text)
	return data
}

func (a *App) filePage(w http.ResponseWriter, r *http.Request) {
	a.render(w, r, "file", "puny.conf", "settings", a.fileData(r, nil))
}

// saveFile replaces puny.conf with the editor's text, through bin/config
// replace, unless the file changed since the editor loaded it.
func (a *App) saveFile(w http.ResponseWriter, r *http.Request) {
	text := strings.ReplaceAll(r.FormValue("text"), "\r\n", "\n")
	if text != "" && !strings.HasSuffix(text, "\n") {
		text += "\n"
	}
	current, err := a.run(r.Context(), quick, "", "config", "raw")
	var n *Notice
	switch {
	case err != nil:
		n = bad(err)
	case hashText(current) != r.FormValue("hash"):
		w.WriteHeader(http.StatusConflict)
		page := a.fileData(r, bad(fmt.Errorf("puny.conf changed since this page loaded it, perhaps in the terminal. Nothing was saved; the editor now shows the file as it is, so make the change again.")))
		a.fragment(w, "file-form", page)
		return
	default:
		out, err := a.run(r.Context(), quick, text, "config", "replace")
		if err != nil {
			// The editor keeps the text that was refused, so it can be fixed.
			w.WriteHeader(http.StatusBadRequest)
			a.fragment(w, "file-form", FilePage{Path: a.fileData(r, nil).Path, Text: text, Hash: hashText(current),
				Notice: bad(fmt.Errorf("%v", err))})
			return
		}
		n = good(out)
		if r.FormValue("then") == "restart" {
			n = a.restartAfterSave(r, out)
		}
	}
	a.fragment(w, "file-form", a.fileData(r, n))
}

type AboutPage struct {
	Repo        string
	GoVersion   string
	HTMXVersion string
	HTMXSHA256  string
	MLXLM       string
	Origin      string
}

func (a *App) aboutPage(w http.ResponseWriter, r *http.Request) {
	data := AboutPage{Repo: a.repo, GoVersion: runtime.Version(), HTMXVersion: a.HTMXVersion, HTMXSHA256: a.HTMXSHA256, Origin: a.Origin}
	if lock, err := os.ReadFile(filepath.Join(a.repo, "lib", "mlx-lm.lock")); err == nil {
		for _, line := range strings.Split(string(lock), "\n") {
			if strings.HasPrefix(line, "mlx-lm==") {
				data.MLXLM = strings.Fields(strings.TrimPrefix(line, "mlx-lm=="))[0]
			}
		}
	}
	a.render(w, r, "about", "About", "settings", data)
}

// readAll reads at most limit bytes of a request body.
func readAll(r io.Reader, limit int64) ([]byte, error) {
	data, err := io.ReadAll(io.LimitReader(r, limit+1))
	if err == nil && int64(len(data)) > limit {
		return nil, fmt.Errorf("the request is over %d bytes", limit)
	}
	return data, err
}
