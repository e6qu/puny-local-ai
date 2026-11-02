package main

import (
	"bytes"
	"embed"
	"encoding/json"
	"fmt"
	"html/template"
	"io/fs"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"time"
)

//go:embed templates static
var assets embed.FS

type Options struct {
	Repo        string
	Origin      string
	HTMX        []byte
	HTMXVersion string
	HTMXSHA256  string
}

type App struct {
	Options
	repo   string
	host   string
	server *ModelServer
	jobs   *Jobs
	base   *template.Template
	pages  map[string]*template.Template
	mux    *http.ServeMux
}

func NewApp(o Options) (*App, error) {
	parts, err := url.Parse(o.Origin)
	if err != nil {
		return nil, err
	}
	a := &App{Options: o, repo: o.Repo, host: parts.Host, server: NewModelServer(o.Repo), jobs: NewJobs(o.Repo),
		pages: map[string]*template.Template{}, mux: http.NewServeMux()}
	a.base, err = template.New("").Funcs(templateFuncs).ParseFS(assets, "templates/*.html")
	if err != nil {
		return nil, err
	}
	pages, err := fs.Glob(assets, "templates/pages/*.html")
	if err != nil {
		return nil, err
	}
	for _, page := range pages {
		t, err := template.Must(a.base.Clone()).ParseFS(assets, page)
		if err != nil {
			return nil, err
		}
		a.pages[strings.TrimSuffix(filepath.Base(page), ".html")] = t
	}
	a.routes()
	return a, nil
}

// ServeHTTP refuses requests that may come from another web site: a page on
// another site could otherwise reach this UI through the browser, by DNS
// rebinding (a wrong Host) or a cross-site POST (a wrong Origin).
func (a *App) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	if r.Host != a.host {
		httpError(w, http.StatusForbidden, "this UI answers only at "+a.Origin)
		return
	}
	if r.Method != http.MethodGet && r.Method != http.MethodHead {
		if origin := r.Header.Get("Origin"); origin != "" && origin != a.Origin {
			httpError(w, http.StatusForbidden, "requests must come from this UI's own pages")
			return
		}
		if site := r.Header.Get("Sec-Fetch-Site"); site != "" && site != "same-origin" && site != "none" {
			httpError(w, http.StatusForbidden, "requests must come from this UI's own pages")
			return
		}
	}
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("X-Content-Type-Options", "nosniff")
	w.Header().Set("X-Frame-Options", "DENY")
	w.Header().Set("Content-Security-Policy",
		"default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'")
	a.mux.ServeHTTP(w, r)
}

func httpError(w http.ResponseWriter, status int, message string) {
	writeJSON(w, status, map[string]string{"error": message})
}

func writeJSON(w http.ResponseWriter, status int, value any) {
	data, _ := json.Marshal(value)
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	w.Write(data)
}

// Page is what every page's layout needs, plus the page's own data.
type Page struct {
	Title  string
	Nav    string // which sidebar entry is current
	Status ServerStatus
	Bar    StatusBar
	Data   any
	Error  string
}

// StatusBar is the strip along the bottom of every page.
type StatusBar struct {
	Server      ServerStatus
	Memory      Memory
	DiskBytes   int64
	RunningJobs int
	Problem     string
}

func (a *App) statusBar(r *http.Request) StatusBar {
	bar := StatusBar{Memory: readMemory()}
	settings, err := a.settings(r.Context())
	if err != nil {
		bar.Problem = err.Error()
		bar.Server = ServerStatus{State: "unknown"}
	} else {
		bar.Server = a.server.Status(settings.Get("MLX_BASE_URL"))
	}
	bar.DiskBytes, _ = freeDiskBytes(a.repo)
	for _, job := range a.jobs.List() {
		if job.Running() {
			bar.RunningJobs++
		}
	}
	return bar
}

// render writes a whole page.
func (a *App) render(w http.ResponseWriter, r *http.Request, name, title, nav string, data any) {
	page := Page{Title: title, Nav: nav, Bar: a.statusBar(r), Data: data}
	page.Status = page.Bar.Server
	t, ok := a.pages[name]
	if !ok {
		http.Error(w, "no page "+name, http.StatusInternalServerError)
		return
	}
	var out bytes.Buffer
	if err := t.ExecuteTemplate(&out, "layout", page); err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	w.Write(out.Bytes())
}

// fragment writes one of the shared templates, for HTMX to swap in.
func (a *App) fragment(w http.ResponseWriter, name string, data any) {
	var out bytes.Buffer
	if err := a.base.ExecuteTemplate(&out, name, data); err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	w.Write(out.Bytes())
}

// Notice is a message shown after an action: good, bad or plain.
type Notice struct {
	Kind string
	Text string
}

func good(text string) *Notice { return &Notice{"good", strings.TrimSpace(text)} }
func bad(err error) *Notice    { return &Notice{"bad", strings.TrimSpace(err.Error())} }

func (a *App) notice(w http.ResponseWriter, n *Notice) { a.fragment(w, "notice", n) }

// redirect sends HTMX, or a plain browser, to another page.
func redirect(w http.ResponseWriter, r *http.Request, to string) {
	if r.Header.Get("HX-Request") == "true" {
		w.Header().Set("HX-Redirect", to)
		w.WriteHeader(http.StatusNoContent)
		return
	}
	http.Redirect(w, r, to, http.StatusSeeOther)
}

var templateFuncs = template.FuncMap{
	"gib": func(b int64) string {
		if b <= 0 {
			return "?"
		}
		return fmt.Sprintf("%.2f GiB", float64(b)/(1<<30))
	},
	"tokens": func(n int) string {
		if n <= 0 {
			return "?"
		}
		return grouped(int64(n))
	},
	"count": grouped,
	"short": func(commit string) string {
		if len(commit) > 10 {
			return commit[:10]
		}
		return commit
	},
	"when": func(t time.Time) string {
		if t.IsZero() {
			return ""
		}
		return t.Format("2006-01-02 15:04")
	},
	"percent": func(part, whole int64) int64 {
		if whole <= 0 {
			return 0
		}
		return min(100, max(0, part*100/whole))
	},
	"stateClass": func(state string) string {
		switch {
		case state == "running" || state == "done":
			return "good"
		case state == "starting":
			return "warn"
		case state == "stopped":
			return "off"
		default:
			return "bad"
		}
	},
	"lastLines": func(text string, n int) string {
		lines := strings.Split(strings.TrimRight(text, "\n"), "\n")
		if len(lines) > n {
			lines = lines[len(lines)-n:]
		}
		return strings.Join(lines, "\n")
	},
	"dict": func(pairs ...any) map[string]any {
		m := map[string]any{}
		for i := 0; i+1 < len(pairs); i += 2 {
			m[pairs[i].(string)] = pairs[i+1]
		}
		return m
	},
	"has": func(list []string, value string) bool {
		for _, v := range list {
			if v == value {
				return true
			}
		}
		return false
	},
	"trim": strings.TrimSpace,
	"inc":  func(i int) int { return i + 1 },
}

func (a *App) static(w http.ResponseWriter, r *http.Request) {
	name := strings.TrimPrefix(r.URL.Path, "/static/")
	if name == "htmx.min.js" {
		w.Header().Set("Content-Type", "text/javascript; charset=utf-8")
		w.Write(a.HTMX)
		return
	}
	data, err := assets.ReadFile("static/" + name)
	if err != nil || strings.Contains(name, "/") {
		http.NotFound(w, r)
		return
	}
	switch filepath.Ext(name) {
	case ".css":
		w.Header().Set("Content-Type", "text/css; charset=utf-8")
	case ".js":
		w.Header().Set("Content-Type", "text/javascript; charset=utf-8")
	case ".svg":
		w.Header().Set("Content-Type", "image/svg+xml")
	}
	w.Write(data)
}

// grouped writes a whole number with commas between thousands.
func grouped(n int64) string {
	s := fmt.Sprint(n)
	var out []string
	for len(s) > 3 {
		out = append([]string{s[len(s)-3:]}, out...)
		s = s[:len(s)-3]
	}
	return strings.Join(append([]string{s}, out...), ",")
}

// readable reports whether path is a regular file small enough to show.
func readable(path string) bool {
	info, err := os.Stat(path)
	return err == nil && info.Mode().IsRegular() && info.Size() < 1<<20
}
