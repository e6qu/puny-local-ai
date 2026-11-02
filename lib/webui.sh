# shellcheck shell=bash
# The web UI's pinned build tools. Source after common.sh.
#
# Nothing here is vendored. ensure_web_ui downloads the Go toolchain and HTMX
# at the versions below, checks each against its published hash, builds ui/
# into state/web-ui/ and records what it built from, so a later run rebuilds
# only when ui/ or a pin changes.
#
#   Go 1.27.3, released 2026-10-19 (https://go.dev/doc/devel/release#go1.27.3);
#     the hash is the one https://go.dev/dl/?mode=json lists for the file.
#   HTMX 2.0.11, the npm "latest" tag since 2026-09-22; the tarball's hash is
#     the "integrity" https://registry.npmjs.org/htmx.org/2.0.11 lists, and
#     dist/htmx.min.js inside it matches the copies on cdn.jsdelivr.net and
#     unpkg.com.

GO_VERSION="1.27.3"
GO_ARCHIVE="go$GO_VERSION.darwin-arm64.tar.gz"
GO_URL="https://go.dev/dl/$GO_ARCHIVE"
GO_SHA256="dd3908cf20959d798c1e06ebe5b72213f0505ce01373b40ce3110975a6cd99c2"
HTMX_VERSION="2.0.11"
HTMX_URL="https://registry.npmjs.org/htmx.org/-/htmx.org-$HTMX_VERSION.tgz"
HTMX_SHA512_BASE64="Thx/WtpeOQqSrqBCw/A1cwGJGg4UrVa3+sW0GmrM3p4gJgO89ecH4qtbnyzDDWFvBTqjnIMCgELTNt636dtamA=="
HTMX_JS_SHA256="d6fdc75f204e6bdefa99b69bf1e6d4ac69b8a364f77929f45c13476b4000f717"

GO_ROOT="$PUNY_ROOT/state/go/$GO_VERSION/go"
WEB_UI_DIR="$PUNY_ROOT/state/web-ui"
WEB_UI_BIN="$WEB_UI_DIR/puny-ui"
WEB_UI_HTMX="$WEB_UI_DIR/htmx.min.js"

# download URL FILE: fetches URL into FILE, failing on any HTTP error.
download() {
  curl -fsSL --retry 2 -o "$2" "$1" || die "could not download $1"
}

# The Go toolchain in state/go, downloaded and checked once.
ensure_go() {
  [[ "$(uname -sm)" == "Darwin arm64" ]] || die "the web UI is built for Macs with Apple Silicon; this is $(uname -sm)"
  [[ -x "$GO_ROOT/bin/go" ]] && return 0
  require_free_disk 2 "$PUNY_ROOT" "Installing Go $GO_VERSION"
  local cache="$PUNY_ROOT/state/cache" partial
  mkdir -p "$cache" "$PUNY_ROOT/state/go"
  echo "Downloading Go $GO_VERSION from $GO_URL"
  download "$GO_URL" "$cache/$GO_ARCHIVE.part"
  [[ "$(shasum -a 256 "$cache/$GO_ARCHIVE.part" | cut -d' ' -f1)" == "$GO_SHA256" ]] \
    || { rm -f "$cache/$GO_ARCHIVE.part"; die "$GO_URL does not match its published SHA-256 $GO_SHA256"; }
  partial="$PUNY_ROOT/state/go/.$GO_VERSION.partial"
  rm -rf "$partial"
  mkdir -p "$partial"
  tar -xzf "$cache/$GO_ARCHIVE.part" -C "$partial"
  rm -f "$cache/$GO_ARCHIVE.part"
  rm -rf "$PUNY_ROOT/state/go/$GO_VERSION"
  mv "$partial" "$PUNY_ROOT/state/go/$GO_VERSION"
}

# HTMX in state/web-ui, from the npm tarball, checked twice: the tarball
# against npm's integrity hash, the file against its own SHA-256.
ensure_htmx() {
  if [[ -f "$WEB_UI_HTMX" && "$(shasum -a 256 "$WEB_UI_HTMX" | cut -d' ' -f1)" == "$HTMX_JS_SHA256" ]]; then
    return 0
  fi
  local work
  mkdir -p "$WEB_UI_DIR"
  work="$(mktemp -d "$WEB_UI_DIR/.htmx.XXXXXX")"
  echo "Downloading HTMX $HTMX_VERSION from $HTMX_URL"
  download "$HTMX_URL" "$work/htmx.tgz"
  [[ "$(openssl dgst -sha512 -binary "$work/htmx.tgz" | base64)" == "$HTMX_SHA512_BASE64" ]] \
    || { rm -rf "$work"; die "$HTMX_URL does not match npm's integrity hash sha512-$HTMX_SHA512_BASE64"; }
  tar -xzf "$work/htmx.tgz" -C "$work" package/dist/htmx.min.js package/LICENSE
  [[ "$(shasum -a 256 "$work/package/dist/htmx.min.js" | cut -d' ' -f1)" == "$HTMX_JS_SHA256" ]] \
    || { rm -rf "$work"; die "dist/htmx.min.js in $HTMX_URL does not match SHA-256 $HTMX_JS_SHA256"; }
  mv "$work/package/LICENSE" "$WEB_UI_DIR/htmx-LICENSE"
  mv "$work/package/dist/htmx.min.js" "$WEB_UI_HTMX"
  rm -rf "$work"
}

# What a build is made from: every file in ui/, and the Go version.
web_ui_stamp() {
  [[ -d "$PUNY_ROOT/ui" ]] || die "$PUNY_ROOT/ui is missing: it holds the web UI's source"
  (cd "$PUNY_ROOT/ui" && find . -type f ! -name '.*' | LC_ALL=C sort | xargs shasum -a 256 | shasum -a 256 | cut -d' ' -f1)
  echo "go $GO_VERSION"
}

# go_in_ui ARGS... runs the pinned go in ui/. Go keeps its caches and
# settings in state/cache/go, not in ~/, and may download nothing: the UI
# uses the standard library only.
go_in_ui() {
  local go_home="$PUNY_ROOT/state/cache/go"
  mkdir -p "$go_home"
  (cd "$PUNY_ROOT/ui" && env HOME="$go_home" GOROOT="$GO_ROOT" GOPATH="$go_home/path" GOCACHE="$go_home/build" \
    GOTOOLCHAIN=local GOPROXY=off GOFLAGS=-mod=mod GOTELEMETRY=off CGO_ENABLED=0 "$GO_ROOT/bin/go" "$@")
}

# Builds ui/ into state/web-ui/puny-ui unless that exact source is built.
ensure_web_ui() {
  ensure_go
  ensure_htmx
  local stamp
  stamp="$(web_ui_stamp)"
  if [[ -x "$WEB_UI_BIN" && -f "$WEB_UI_DIR/.built" && "$(<"$WEB_UI_DIR/.built")" == "$stamp" ]]; then
    return 0
  fi
  echo "Building the web UI from $PUNY_ROOT/ui with Go $GO_VERSION"
  go_in_ui build -trimpath -o "$WEB_UI_BIN.part" . || die "the web UI did not build"
  mv "$WEB_UI_BIN.part" "$WEB_UI_BIN"
  printf '%s\n' "$stamp" > "$WEB_UI_DIR/.built"
}
