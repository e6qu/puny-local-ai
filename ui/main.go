// Command puny-ui serves the puny-local-ai web UI on this Mac: the model
// server, the models, chat, the workflows and benchmarks, the coding agents'
// configs and the settings in puny.conf.
//
// Usage: puny-ui --listen http://127.0.0.1:PORT --repo DIR --htmx FILE
//
// bin/web-ui builds and starts it; see that script. Every change goes through
// the repo's own commands, the same ones the terminal uses:
//   - settings through bin/config, which checks a change before it saves
//     puny.conf, so the file stays the one source of the settings;
//   - the model server through bin/serve-qwen, with its memory guards;
//   - models through bin/models and bin/setup;
//   - workflows and benchmarks through bin/team-solve, bin/bench-agents,
//     bin/evals and bin/smoke-test, run as jobs whose output is kept in
//     state/jobs/.
//
// The settings are read again for each request, so a change made in the
// terminal shows up here too.
//
// It listens on one address, 127.0.0.1 by default, and answers only requests
// whose Host header names that address, and changes only for requests from
// its own pages, so other web sites cannot use it through the browser.
package main

import (
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"flag"
	"fmt"
	"net"
	"net/http"
	"net/url"
	"os"
	"os/signal"
	"path/filepath"
	"syscall"
	"time"
)

func main() {
	listen := flag.String("listen", "", "http://HOST:PORT to serve the UI at")
	repo := flag.String("repo", "", "the puny-local-ai folder")
	htmx := flag.String("htmx", "", "the htmx.min.js to serve, checked by bin/web-ui")
	htmxVersion := flag.String("htmx-version", "", "the version of that file, for the About page")
	flag.Parse()
	if err := serve(*listen, *repo, *htmx, *htmxVersion); err != nil {
		fmt.Fprintf(os.Stderr, "error: %v\n", err)
		os.Exit(1)
	}
}

func serve(listen, repo, htmxPath, htmxVersion string) error {
	if listen == "" || repo == "" || htmxPath == "" {
		return errors.New("usage: puny-ui --listen http://127.0.0.1:PORT --repo DIR --htmx FILE")
	}
	parts, err := url.Parse(listen)
	if err != nil || parts.Scheme != "http" || parts.Port() == "" || parts.Path != "" {
		return fmt.Errorf("--listen must look like http://127.0.0.1:PORT, not %q", listen)
	}
	repo, err = filepath.Abs(repo)
	if err != nil {
		return err
	}
	if _, err := os.Stat(filepath.Join(repo, "bin", "config")); err != nil {
		return fmt.Errorf("%s is not the puny-local-ai folder: %v", repo, err)
	}
	htmx, err := os.ReadFile(htmxPath)
	if err != nil {
		return err
	}
	sum := sha256.Sum256(htmx)
	app, err := NewApp(Options{
		Repo:        repo,
		Origin:      "http://" + parts.Host,
		HTMX:        htmx,
		HTMXVersion: htmxVersion,
		HTMXSHA256:  hex.EncodeToString(sum[:]),
	})
	if err != nil {
		return err
	}
	listener, err := net.Listen("tcp", parts.Host)
	if err != nil {
		return err
	}
	httpd := &http.Server{Handler: app, ReadHeaderTimeout: 10 * time.Second}
	stop := make(chan os.Signal, 1)
	signal.Notify(stop, os.Interrupt, syscall.SIGTERM)
	go func() {
		<-stop
		httpd.Close()
	}()
	fmt.Printf("The web UI is at http://%s/ (Ctrl-C stops it; a server or job started from it keeps running).\n", parts.Host)
	if err := httpd.Serve(listener); !errors.Is(err, http.ErrServerClosed) {
		return err
	}
	return nil
}
