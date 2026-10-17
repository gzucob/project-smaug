// smaugctl manages one isolated local Compose installation. No runtime other
// than Docker is required on the host; this executable uses the Go stdlib only.
package main

import (
	"archive/zip"
	"bytes"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"runtime"
	"strconv"
	"strings"
	"time"
)

const repository = "gzucob/project-smaug"

var releasePattern = regexp.MustCompile(`^\d{4}\.\d{2}\.\d{2}-[a-f0-9]{12}$`)
var digestPattern = regexp.MustCompile(`^[a-z0-9./:_-]+@sha256:[a-f0-9]{64}$`)
var exitCode int
var dockerCommand = exec.Command

type Manifest struct {
	Version string            `json:"version"`
	Commit  string            `json:"commit"`
	Schema  string            `json:"schema"`
	Images  map[string]string `json:"images"`
	Files   map[string]string `json:"files"`
}
type Config struct {
	Project              string `json:"project"`
	Edition              string `json:"edition"`
	APIPort              int    `json:"api_port"`
	WebPort              int    `json:"web_port"`
	AutoPriceRefresh     bool   `json:"auto_price_refresh"`
	PriceRefreshInterval int    `json:"price_refresh_interval_seconds"`
}
type State struct {
	Version string `json:"version"`
	Edition string `json:"edition"`
}
type Pending struct {
	Previous State  `json:"previous"`
	Target   State  `json:"target"`
	Backup   string `json:"backup"`
}
type Manager struct {
	Root   string
	Config Config
	State  State
}

func main() {
	if err := run(os.Args[1:]); err != nil {
		fmt.Fprintln(os.Stderr, "smaugctl:", err)
		var commandError *exec.ExitError
		if errors.As(err, &commandError) {
			exitCode = commandError.ExitCode()
		}
		if exitCode == 0 {
			exitCode = 1
		}
	}
	os.Exit(exitCode)
}
func run(args []string) error {
	home, err := os.UserConfigDir()
	if err != nil {
		return err
	}
	root := filepath.Join(home, "smaug")
	if len(args) >= 2 && args[0] == "--dir" {
		root, args = args[1], args[2:]
	}
	if len(args) == 0 || args[0] == "--help" || args[0] == "help" {
		fmt.Println("smaugctl [--dir DIRECTORY] install --source BUNDLE [--edition backend|full] [--api-port 8000] [--web-port 3000]\nCommands: start, stop, status, logs [service], check, update, edition backend|full, backup, restore --backup DIRECTORY, cli COMMAND [ARGS]")
		return nil
	}
	root, err = filepath.Abs(root)
	if err != nil {
		return err
	}
	m := &Manager{Root: root}
	command := args[0]
	args = args[1:]
	if err = prerequisites(); err != nil {
		return err
	}
	if command != "install" {
		if err = readJSON(m.file("config.json"), &m.Config); err != nil {
			return fmt.Errorf("installation not found; run install: %w", err)
		}
		if err = readJSON(m.file("current.json"), &m.State); err != nil {
			return err
		}
		if err = validateConfig(m.Config); err != nil {
			return err
		}
	}
	// CLI owns this lock for its entire lifetime. A crashed Docker client may
	// leave its container behind, so writer checks also inspect Docker labels.
	if command != "status" && command != "logs" && command != "check" {
		if err = os.MkdirAll(root, 0700); err != nil {
			return err
		}
		lock := m.file("operation.lock")
		if err = os.Mkdir(lock, 0700); err != nil {
			return fmt.Errorf("another operation is active (or was interrupted); inspect Docker processes before removing %s", lock)
		}
		defer os.Remove(lock)
	}
	if exists(m.file("pending.json")) && command != "restore" && command != "update" && command != "status" && command != "logs" && command != "check" {
		return errors.New("an interrupted update needs recovery; use update to retry or restore --backup PATH; see pending.json")
	}
	switch command {
	case "install":
		return m.install(args)
	case "start":
		return m.start(m.State)
	case "stop":
		if err = m.noCLI(); err != nil {
			return err
		}
		return m.compose(m.State, nil, nil, "stop")
	case "status":
		fmt.Printf("Version: %s\nEdition: %s\nDirectory: %s\n", m.State.Version, m.State.Edition, m.Root)
		if exists(m.file("pending.json")) {
			fmt.Println("RECOVERY REQUIRED: pending.json")
		}
		return m.compose(m.State, nil, nil, "ps", "--all")
	case "logs":
		return m.compose(m.State, nil, nil, append([]string{"logs", "--tail", "200", "--follow"}, args...)...)
	case "check":
		return m.check()
	case "cli":
		if len(args) == 0 {
			return errors.New("usage: smaugctl cli COMMAND [ARGS]")
		}
		return m.compose(m.State, nil, nil, append([]string{"run", "--rm", "--no-deps", "-T", "--label", "io.smaug.operation=cli", "cli"}, args...)...)
	case "edition":
		if len(args) != 1 || !validEdition(args[0]) {
			return errors.New("edition must be backend or full")
		}
		next := m.State
		next.Edition = args[0]
		return m.transition(next)
	case "backup":
		if err = m.noCLI(); err != nil {
			return err
		}
		if err = m.compose(m.State, nil, nil, "stop", "api", "frontend"); err != nil {
			return err
		}
		path, backupErr := m.backup(m.State, true)
		if backupErr != nil {
			return fmt.Errorf("backup failed; services remain stopped: %w", backupErr)
		}
		fmt.Println("Backup:", path)
		return m.start(m.State)
	case "restore":
		if len(args) != 2 || args[0] != "--backup" {
			return errors.New("usage: restore --backup DIRECTORY")
		}
		return m.restore(args[1])
	case "update":
		if len(args) == 2 && args[0] == "--source" {
			if exists(m.file("pending.json")) {
				return errors.New("recover pending update with plain update before selecting another bundle")
			}
			v, err := m.stage(args[1])
			if err != nil {
				return err
			}
			return m.transition(State{v.Version, m.State.Edition})
		}
		if len(args) != 0 {
			return errors.New("usage: update [--source BUNDLE]")
		}
		return m.update()
	default:
		return fmt.Errorf("unknown command %q", command)
	}
}
func (m *Manager) file(parts ...string) string {
	return filepath.Join(append([]string{m.Root}, parts...)...)
}
func exists(path string) bool    { _, e := os.Stat(path); return e == nil }
func validEdition(s string) bool { return s == "backend" || s == "full" }
func validateConfig(c Config) error {
	if !regexp.MustCompile(`^smaug-[a-f0-9]{12}$`).MatchString(c.Project) || !validEdition(c.Edition) || c.APIPort < 1 || c.APIPort > 65535 || c.WebPort < 1 || c.WebPort > 65535 || c.APIPort == c.WebPort || c.PriceRefreshInterval < 60 {
		return errors.New("invalid config.json")
	}
	return nil
}
func readJSON(path string, v any) error {
	b, e := os.ReadFile(path)
	if e != nil {
		return e
	}
	return json.Unmarshal(b, v)
}
func writeJSON(path string, v any) error {
	b, e := json.MarshalIndent(v, "", "  ")
	if e != nil {
		return e
	}
	b = append(b, '\n')
	tmp := path + ".tmp"
	if e = os.WriteFile(tmp, b, 0600); e != nil {
		return e
	}
	// On Windows os.Rename replaces a file but is not guaranteed atomic. Keep
	// the prior state separately before committing a transition.
	return os.Rename(tmp, path)
}
func prerequisites() error {
	for _, args := range [][]string{{"version", "--format", "{{.Server.Version}}"}, {"compose", "version", "--short"}} {
		if b, e := dockerCommand("docker", args...).CombinedOutput(); e != nil {
			return fmt.Errorf("Docker with Compose v2 must be installed and running: %s (%w)", strings.TrimSpace(string(b)), e)
		}
	}
	return nil
}
func (m *Manager) manifest(s State) (Manifest, error) {
	var v Manifest
	if !releasePattern.MatchString(s.Version) || !validEdition(s.Edition) {
		return v, errors.New("invalid installation state")
	}
	err := readJSON(m.file("releases", s.Version, "manifest.json"), &v)
	if err != nil {
		return v, err
	}
	if err = validateManifest(v); err != nil {
		return v, err
	}
	if v.Version != s.Version {
		return v, errors.New("manifest version mismatch")
	}
	got, err := hashFile(m.file("releases", s.Version, "compose.yaml"))
	if err != nil || got != v.Files["compose.yaml"] {
		return v, errors.New("installed Compose file failed its checksum")
	}
	return v, nil
}
func validateManifest(v Manifest) error {
	if !releasePattern.MatchString(v.Version) || !regexp.MustCompile(`^[a-f0-9]{40}$`).MatchString(v.Commit) || !strings.HasSuffix(v.Version, v.Commit[:12]) || v.Schema == "" {
		return errors.New("invalid release manifest")
	}
	for _, name := range []string{"backend", "frontend", "mongo", "postgres"} {
		if !digestPattern.MatchString(v.Images[name]) {
			return fmt.Errorf("image %s must be pinned by digest", name)
		}
	}
	for _, name := range []string{"compose.yaml", binaryName()} {
		if _, ok := v.Files[name]; !ok {
			return fmt.Errorf("manifest missing %s", name)
		}
	}
	return nil
}
func binaryName() string {
	ext := ""
	if runtime.GOOS == "windows" {
		ext = ".exe"
	}
	return "smaugctl-" + runtime.GOOS + "-" + runtime.GOARCH + ext
}
func (m *Manager) compose(s State, input io.Reader, output io.Writer, args ...string) error {
	v, err := m.manifest(s)
	if err != nil {
		return err
	}
	base := []string{"compose", "--project-name", m.Config.Project, "--project-directory", m.file("releases", s.Version), "--file", m.file("releases", s.Version, "compose.yaml")}
	if s.Edition == "full" {
		base = append(base, "--profile", "full")
	}
	cmd := dockerCommand("docker", append(base, args...)...)
	// Never let a development checkout's Compose variables select this instance.
	for _, e := range os.Environ() {
		if !strings.HasPrefix(e, "COMPOSE_") && !strings.HasPrefix(e, "SMAUG_") && !strings.HasPrefix(e, "API_PORT=") && !strings.HasPrefix(e, "WEB_PORT=") && !strings.HasPrefix(e, "AUTO_PRICE_REFRESH=") && !strings.HasPrefix(e, "PRICE_REFRESH_INTERVAL_SECONDS=") {
			cmd.Env = append(cmd.Env, e)
		}
	}
	cmd.Env = append(cmd.Env, "COMPOSE_DISABLE_ENV_FILE=1", "API_PORT="+strconv.Itoa(m.Config.APIPort), "WEB_PORT="+strconv.Itoa(m.Config.WebPort), "AUTO_PRICE_REFRESH="+strconv.FormatBool(m.Config.AutoPriceRefresh), "PRICE_REFRESH_INTERVAL_SECONDS="+strconv.Itoa(m.Config.PriceRefreshInterval))
	for name, image := range v.Images {
		cmd.Env = append(cmd.Env, "SMAUG_"+strings.ToUpper(name)+"_IMAGE="+image)
	}
	cmd.Stdin = input
	cmd.Stdout = output
	if output == nil {
		cmd.Stdout = os.Stdout
	}
	cmd.Stderr = os.Stderr
	return cmd.Run()
}
func (m *Manager) capture(s State, args ...string) (string, error) {
	var b strings.Builder
	e := m.compose(s, nil, &b, args...)
	return strings.TrimSpace(b.String()), e
}
func (m *Manager) noCLI() error {
	b, e := dockerCommand("docker", "ps", "-q", "--filter", "label=com.docker.compose.project="+m.Config.Project, "--filter", "label=io.smaug.operation=cli").Output()
	if e != nil {
		return e
	}
	if len(strings.TrimSpace(string(b))) != 0 {
		return errors.New("a CLI container is still running; wait for it before changing services")
	}
	return nil
}
func (m *Manager) pull(s State) error {
	services := []string{"pull", "mongo", "postgres", "api", "migrate", "cli"}
	if s.Edition == "full" {
		services = append(services, "frontend")
	}
	return m.compose(s, nil, nil, services...)
}
func (m *Manager) start(s State) error {
	if err := m.noCLI(); err != nil {
		return err
	}
	// A running service already owns its published port; Compose handles it.
	for service, port := range map[string]int{"api": m.Config.APIPort, "frontend": m.Config.WebPort} {
		if service == "frontend" && s.Edition != "full" {
			continue
		}
		id, err := m.capture(s, "ps", "--status", "running", "--quiet", service)
		if err != nil {
			return err
		}
		if id == "" {
			l, err := net.Listen("tcp", fmt.Sprintf("127.0.0.1:%d", port))
			if err != nil {
				return fmt.Errorf("port %d is occupied; change config.json: %w", port, err)
			}
			l.Close()
		}
	}
	services := []string{"up", "--detach", "--wait", "--wait-timeout", "240", "mongo", "postgres", "api"}
	if s.Edition == "full" {
		services = append(services, "frontend")
	}
	return m.compose(s, nil, nil, services...)
}
func (m *Manager) check() error {
	if err := m.compose(m.State, nil, nil, "ps", "--all"); err != nil {
		return err
	}
	client := http.Client{Timeout: 20 * time.Second}
	response, err := client.Get(fmt.Sprintf("http://127.0.0.1:%d/ready", m.Config.APIPort))
	if err != nil {
		return err
	}
	defer response.Body.Close()
	if _, err = io.Copy(os.Stdout, response.Body); err != nil {
		return err
	}
	if response.StatusCode != 200 {
		return errors.New("installation is not ready")
	}
	if exists(m.file("pending.json")) {
		return errors.New("interrupted update requires recovery")
	}
	if m.State.Edition == "full" {
		response, err = client.Get(fmt.Sprintf("http://127.0.0.1:%d", m.Config.WebPort))
		if err != nil {
			return err
		}
		defer response.Body.Close()
		if response.StatusCode != 200 {
			return errors.New("frontend is not ready")
		}
	}
	return nil
}
func (m *Manager) install(args []string) error {
	flags := flag.NewFlagSet("install", flag.ContinueOnError)
	source := flags.String("source", "", "directory extracted from the release")
	edition := flags.String("edition", "", "backend or full")
	api := flags.Int("api-port", 8000, "local API port")
	web := flags.Int("web-port", 3000, "local frontend port")
	if err := flags.Parse(args); err != nil {
		return err
	}
	if exists(m.file("config.json")) {
		if err := readJSON(m.file("config.json"), &m.Config); err != nil {
			return err
		}
		if err := validateConfig(m.Config); err != nil {
			return err
		}
		if err := readJSON(m.file("current.json"), &m.State); err != nil {
			return errors.New("installation incomplete; retry with its original bundle or recover pending.json")
		}
		return m.start(m.State)
	}
	if *source == "" {
		return errors.New("install requires --source BUNDLE")
	}
	if *edition == "" {
		b, e := os.ReadFile(filepath.Join(*source, "edition.txt"))
		if e != nil {
			return e
		}
		*edition = strings.TrimSpace(string(b))
	}
	id := make([]byte, 6)
	if _, err := rand.Read(id); err != nil {
		return err
	}
	m.Config = Config{Project: "smaug-" + hex.EncodeToString(id), Edition: *edition, APIPort: *api, WebPort: *web, AutoPriceRefresh: true, PriceRefreshInterval: 21600}
	if err := validateConfig(m.Config); err != nil {
		return err
	}
	v, err := m.stage(*source)
	if err != nil {
		return err
	}
	target := State{v.Version, *edition}
	if err = m.pull(target); err != nil {
		return err
	}
	if err = writeJSON(m.file("config.json"), m.Config); err != nil {
		return err
	}
	// Record an incomplete install so start can never bypass a failed migration.
	if err = writeJSON(m.file("current.json"), target); err != nil {
		return err
	}
	if err = writeJSON(m.file("pending.json"), Pending{Target: target}); err != nil {
		return err
	}
	if err = m.launchers(); err != nil {
		return err
	}
	if err = m.compose(target, nil, nil, "up", "-d", "--wait", "mongo", "postgres"); err != nil {
		return err
	}
	if err = m.migrate(target); err != nil {
		return err
	}
	if err = m.start(target); err != nil {
		return err
	}
	if err = m.commit(target); err != nil {
		return err
	}
	launcher := "smaugctl"
	if runtime.GOOS == "windows" {
		launcher += ".ps1"
	}
	fmt.Printf("Installed %s (%s).\nManager: %s\nAPI: http://127.0.0.1:%d/docs\n", v.Version, *edition, m.file(launcher), m.Config.APIPort)
	if *edition == "full" {
		fmt.Printf("Frontend: http://127.0.0.1:%d\n", m.Config.WebPort)
	}
	fmt.Println("No data collected. Next: smaugctl cli ingest --ticker PETR4; smaugctl cli analyze --ticker PETR4")
	return nil
}
func safePath(name string) bool {
	return name != "" && filepath.IsLocal(name) && !strings.ContainsAny(name, "\\:")
}
func hashFile(path string) (string, error) {
	f, e := os.Open(path)
	if e != nil {
		return "", e
	}
	defer f.Close()
	h := sha256.New()
	if _, e = io.Copy(h, f); e != nil {
		return "", e
	}
	return hex.EncodeToString(h.Sum(nil)), nil
}
func (m *Manager) stage(source string) (Manifest, error) {
	var v Manifest
	if err := readJSON(filepath.Join(source, "manifest.json"), &v); err != nil {
		return v, err
	}
	if err := validateManifest(v); err != nil {
		return v, err
	}
	for name, want := range v.Files {
		if !safePath(name) {
			return v, errors.New("unsafe manifest filename")
		}
		info, err := os.Lstat(filepath.Join(source, name))
		if err != nil || !info.Mode().IsRegular() {
			return v, fmt.Errorf("missing regular release file: %s", name)
		}
		got, err := hashFile(filepath.Join(source, name))
		if err != nil || got != want {
			return v, fmt.Errorf("checksum mismatch: %s", name)
		}
	}
	dest := m.file("releases", v.Version)
	if exists(dest) {
		var previous Manifest
		if err := readJSON(filepath.Join(dest, "manifest.json"), &previous); err != nil {
			return v, err
		}
		a, _ := json.Marshal(previous)
		b, _ := json.Marshal(v)
		if string(a) != string(b) {
			return v, errors.New("immutable release already exists with different content")
		}
		return v, nil
	}
	if err := os.MkdirAll(m.file("releases"), 0700); err != nil {
		return v, err
	}
	temp, err := os.MkdirTemp(m.file("releases"), ".staging-")
	if err != nil {
		return v, err
	}
	defer os.RemoveAll(temp)
	for name := range v.Files {
		if err = copyFile(filepath.Join(source, name), filepath.Join(temp, name)); err != nil {
			return v, err
		}
	}
	if err = copyFile(filepath.Join(source, "manifest.json"), filepath.Join(temp, "manifest.json")); err != nil {
		return v, err
	}
	if err = os.Chmod(filepath.Join(temp, binaryName()), 0700); err != nil {
		return v, err
	}
	return v, os.Rename(temp, dest)
}
func copyFile(source, dest string) error {
	if err := os.MkdirAll(filepath.Dir(dest), 0700); err != nil {
		return err
	}
	in, err := os.Open(source)
	if err != nil {
		return err
	}
	defer in.Close()
	out, err := os.OpenFile(dest, os.O_CREATE|os.O_TRUNC|os.O_WRONLY, 0600)
	if err != nil {
		return err
	}
	_, err = io.Copy(out, in)
	closeErr := out.Close()
	if err != nil {
		return err
	}
	return closeErr
}
func (m *Manager) migrate(s State) error {
	return m.compose(s, nil, nil, "run", "--rm", "--no-deps", "-T", "migrate", "upgrade", "head")
}
func (m *Manager) commit(s State) error {
	if exists(m.file("current.json")) {
		if err := copyFile(m.file("current.json"), m.file("previous.json")); err != nil {
			return err
		}
	}
	if err := writeJSON(m.file("current.json"), s); err != nil {
		return err
	}
	m.Config.Edition = s.Edition
	if err := writeJSON(m.file("config.json"), m.Config); err != nil {
		return err
	}
	if err := m.launchers(); err != nil {
		return err
	}
	if exists(m.file("pending.json")) {
		return os.Remove(m.file("pending.json"))
	}
	return nil
}
func (m *Manager) launchers() error {
	// Launch versioned binaries so update never replaces a running Windows exe.
	sh := `#!/bin/sh
set -eu
base=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
version=$(sed -n 's/.*"version": "\([^"]*\)".*/\1/p' "$base/current.json")
case "$(uname -s)" in Linux) system=linux;; Darwin) system=darwin;; *) exit 1;; esac
case "$(uname -m)" in x86_64|amd64) arch=amd64;; arm64|aarch64) arch=arm64;; *) exit 1;; esac
exec "$base/releases/$version/smaugctl-$system-$arch" --dir "$base" "$@"
`
	ps := `$ErrorActionPreference = 'Stop'
$version = (Get-Content -LiteralPath (Join-Path $PSScriptRoot 'current.json') -Raw | ConvertFrom-Json).version
$arch = if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64' -or $env:PROCESSOR_ARCHITEW6432 -eq 'ARM64') { 'arm64' } else { 'amd64' }
& (Join-Path $PSScriptRoot "releases/$version/smaugctl-windows-$arch.exe") --dir $PSScriptRoot @args
exit $LASTEXITCODE
`
	if err := os.WriteFile(m.file("smaugctl"), []byte(sh), 0700); err != nil {
		return err
	}
	return os.WriteFile(m.file("smaugctl.ps1"), []byte(ps), 0600)
}
func (m *Manager) transition(target State) error {
	if err := m.noCLI(); err != nil {
		return err
	}
	old := m.State
	next, err := m.manifest(target)
	if err != nil {
		return err
	}
	prev, err := m.manifest(old)
	if err != nil {
		return err
	}
	// A database major upgrade needs a dedicated export/import procedure.
	if next.Images["mongo"] != prev.Images["mongo"] || next.Images["postgres"] != prev.Images["postgres"] {
		return errors.New("database image changes require a dedicated upgrade; automatic database replacement is disabled")
	}
	if err = m.pull(target); err != nil {
		return err
	}
	if err = m.compose(old, nil, nil, "stop", "api", "frontend"); err != nil {
		return err
	}
	backup := ""
	// Retry interrupted migrations against the original backup, never overwrite it.
	var pending Pending
	if exists(m.file("pending.json")) {
		if err = readJSON(m.file("pending.json"), &pending); err != nil {
			return err
		}
		if pending.Target != target {
			return errors.New("retry the pending release before selecting a newer one")
		}
		backup = pending.Backup
	} else if next.Schema != prev.Schema {
		backup, err = m.backup(old, false)
		if err != nil {
			return fmt.Errorf("backup failed; migration was not started, services remain stopped: %w", err)
		}
	}
	if err = writeJSON(m.file("pending.json"), Pending{Previous: old, Target: target, Backup: backup}); err != nil {
		return err
	}
	if err = m.migrate(target); err != nil {
		return fmt.Errorf("migration failed; services remain stopped, backup %s: %w", backup, err)
	}
	if err = m.start(target); err != nil {
		return fmt.Errorf("new services failed; retry update or restore --backup %s: %w", backup, err)
	}
	return m.commit(target)
}
func (m *Manager) backup(s State, full bool) (string, error) {
	path := m.file("backups", time.Now().UTC().Format("20060102T150405.000000000Z"))
	if err := os.MkdirAll(path, 0700); err != nil {
		return "", err
	}
	// An incomplete directory has no backup.json and cannot be restored.
	if err := m.compose(s, nil, nil, "up", "-d", "--wait", "mongo", "postgres"); err != nil {
		return "", err
	}
	output, err := os.OpenFile(filepath.Join(path, "postgres.dump"), os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
	if err != nil {
		return "", err
	}
	err = m.compose(s, nil, output, "exec", "-T", "postgres", "pg_dump", "-U", "smaug", "-d", "smaug", "-Fc")
	closeErr := output.Close()
	if err != nil {
		return "", err
	}
	if closeErr != nil {
		return "", closeErr
	}
	if full {
		// Use the Mongo image's own tools; stop API/CLI writers before this call.
		if err = m.compose(s, nil, nil, "exec", "-T", "mongo", "mongodump", "--db", "smaug", "--archive=/data/db/smaug-backup.archive", "--gzip"); err != nil {
			return "", err
		}
		if err = m.compose(s, nil, nil, "cp", "mongo:/data/db/smaug-backup.archive", filepath.Join(path, "mongo.archive")); err != nil {
			return "", err
		}
		// Archive only durable Bronze artifacts. Cache is deliberately excluded.
		// Compose cp works with a named volume and Windows paths without socket mounts.
		if err = m.compose(s, nil, nil, "run", "--rm", "--no-deps", "-T", "--entrypoint", "python", "cli", "-c", "import tarfile; t=tarfile.open('/data/cache/sources-backup.tar.gz','w:gz'); t.add('/data/sources',arcname='sources'); t.close()"); err != nil {
			return "", err
		}
		if err = m.compose(s, nil, nil, "create", "cli"); err != nil {
			return "", err
		}
		if err = m.compose(s, nil, nil, "cp", "cli:/data/cache/sources-backup.tar.gz", filepath.Join(path, "sources.tar.gz")); err != nil {
			return "", err
		}
	}
	if err = copyFile(m.file("config.json"), filepath.Join(path, "config.json")); err != nil {
		return "", err
	}
	info := struct {
		State   State             `json:"state"`
		Project string            `json:"project"`
		Full    bool              `json:"full"`
		Files   map[string]string `json:"files"`
	}{s, m.Config.Project, full, map[string]string{}}
	names := []string{"postgres.dump", "config.json"}
	if full {
		names = append(names, "mongo.archive", "sources.tar.gz")
	}
	for _, name := range names {
		hash, e := hashFile(filepath.Join(path, name))
		if e != nil {
			return "", e
		}
		info.Files[name] = hash
	}
	if err = writeJSON(filepath.Join(path, "backup.json"), info); err != nil {
		return "", err
	}
	return path, nil
}
func (m *Manager) restore(path string) error {
	if err := m.noCLI(); err != nil {
		return err
	}
	path, err := filepath.Abs(path)
	if err != nil {
		return err
	}
	var info struct {
		State   State             `json:"state"`
		Project string            `json:"project"`
		Full    bool              `json:"full"`
		Files   map[string]string `json:"files"`
	}
	if err = readJSON(filepath.Join(path, "backup.json"), &info); err != nil {
		return err
	}
	if info.Project != m.Config.Project {
		return errors.New("backup belongs to a different installation")
	}
	for name, want := range info.Files {
		if !safePath(name) {
			return errors.New("unsafe backup filename")
		}
		got, e := hashFile(filepath.Join(path, name))
		if e != nil || got != want {
			return fmt.Errorf("damaged backup: %s", name)
		}
	}
	for _, name := range []string{"postgres.dump", "config.json"} {
		if info.Files[name] == "" {
			return errors.New("incomplete backup")
		}
	}
	if info.Full && (info.Files["mongo.archive"] == "" || info.Files["sources.tar.gz"] == "") {
		return errors.New("incomplete full backup")
	}
	if _, err = m.manifest(info.State); err != nil {
		return err
	}
	var restored Config
	if err = readJSON(filepath.Join(path, "config.json"), &restored); err != nil {
		return err
	}
	if err = validateConfig(restored); err != nil {
		return err
	}
	if restored.Project != m.Config.Project || restored.Edition != info.State.Edition {
		return errors.New("backup configuration does not match its installation state")
	}
	fmt.Println("Restoring the explicitly selected backup; this replaces this installation's saved database state.")
	if err = m.compose(m.State, nil, nil, "stop", "api", "frontend"); err != nil {
		return err
	}
	// Mark recovery before any destructive restore. A failure cannot start an
	// incompatible application until restore is retried successfully.
	if err = writeJSON(m.file("pending.json"), Pending{Previous: m.State, Target: info.State, Backup: path}); err != nil {
		return err
	}
	if err = m.compose(info.State, nil, nil, "up", "-d", "--wait", "mongo", "postgres"); err != nil {
		return err
	}
	// Recreate SQL so objects introduced by a newer schema cannot survive restore.
	if err = m.compose(info.State, nil, nil, "exec", "-T", "postgres", "dropdb", "--if-exists", "--force", "-U", "smaug", "smaug"); err != nil {
		return err
	}
	if err = m.compose(info.State, nil, nil, "exec", "-T", "postgres", "createdb", "-U", "smaug", "smaug"); err != nil {
		return err
	}
	in, err := os.Open(filepath.Join(path, "postgres.dump"))
	if err != nil {
		return err
	}
	defer in.Close()
	if err = m.compose(info.State, in, nil, "exec", "-T", "postgres", "pg_restore", "--exit-on-error", "-U", "smaug", "-d", "smaug"); err != nil {
		return err
	}
	if info.Full {
		in, err := os.Open(filepath.Join(path, "mongo.archive"))
		if err != nil {
			return err
		}
		defer in.Close()
		if err = m.compose(info.State, in, nil, "exec", "-T", "mongo", "mongorestore", "--drop", "--nsInclude=smaug.*", "--archive", "--gzip"); err != nil {
			return err
		}
		if err = m.compose(info.State, nil, nil, "create", "cli"); err != nil {
			return err
		}
		if err = m.compose(info.State, nil, nil, "cp", filepath.Join(path, "sources.tar.gz"), "cli:/data/cache/sources-restore.tar.gz"); err != nil {
			return err
		}
		// Bronze is append-only: restoring adds archived artifacts, never deletes
		// later source evidence. tarfile's data filter rejects traversal and links.
		if err = m.compose(info.State, nil, nil, "run", "--rm", "--no-deps", "-T", "--entrypoint", "python", "cli", "-c", "import tarfile; t=tarfile.open('/data/cache/sources-restore.tar.gz'); t.extractall('/data',filter='data'); t.close()"); err != nil {
			return err
		}
	}
	m.Config = restored
	if err = m.start(info.State); err != nil {
		return err
	}
	return m.commit(info.State)
}
func getURL(url string) ([]byte, error) {
	client := http.Client{Timeout: 5 * time.Minute}
	req, err := http.NewRequest("GET", url, nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("User-Agent", "smaugctl")
	r, err := client.Do(req)
	if err != nil {
		return nil, err
	}
	defer r.Body.Close()
	if r.StatusCode != 200 {
		return nil, fmt.Errorf("download %s: HTTP %d", url, r.StatusCode)
	}
	return io.ReadAll(io.LimitReader(r.Body, 256<<20))
}
func unpackZip(data []byte, dest string) error {
	reader, err := zip.NewReader(bytes.NewReader(data), int64(len(data)))
	if err != nil {
		return err
	}
	var total uint64
	for _, f := range reader.File {
		if !safePath(f.Name) || f.Mode()&os.ModeSymlink != 0 {
			return errors.New("unsafe release archive path")
		}
		if f.FileInfo().IsDir() {
			continue
		}
		total += f.UncompressedSize64
		if total > 512<<20 {
			return errors.New("release archive too large")
		}
		in, e := f.Open()
		if e != nil {
			return e
		}
		path := filepath.Join(dest, f.Name)
		if e = os.MkdirAll(filepath.Dir(path), 0700); e != nil {
			in.Close()
			return e
		}
		out, e := os.OpenFile(path, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
		if e != nil {
			in.Close()
			return e
		}
		_, e = io.Copy(out, in)
		closeErr := out.Close()
		in.Close()
		if e != nil {
			return e
		}
		if closeErr != nil {
			return closeErr
		}
	}
	return nil
}
func (m *Manager) update() error {
	if exists(m.file("pending.json")) {
		var p Pending
		if err := readJSON(m.file("pending.json"), &p); err != nil {
			return err
		}
		if p.Previous.Version == "" {
			if err := m.compose(p.Target, nil, nil, "up", "-d", "--wait", "mongo", "postgres"); err != nil {
				return err
			}
			if err := m.migrate(p.Target); err != nil {
				return err
			}
			if err := m.start(p.Target); err != nil {
				return err
			}
			return m.commit(p.Target)
		}
		return m.transition(p.Target)
	}
	data, err := getURL("https://api.github.com/repos/" + repository + "/releases/latest")
	if err != nil {
		return err
	}
	var release struct {
		Tag    string `json:"tag_name"`
		Assets []struct {
			Name string `json:"name"`
			URL  string `json:"browser_download_url"`
		} `json:"assets"`
	}
	if err = json.Unmarshal(data, &release); err != nil {
		return err
	}
	if !releasePattern.MatchString(release.Tag) {
		return errors.New("invalid release tag")
	}
	if release.Tag == m.State.Version {
		fmt.Println("Already up to date.")
		return nil
	}
	name := "smaug-" + m.State.Edition + "-" + release.Tag + ".zip"
	urls := map[string]string{}
	for _, a := range release.Assets {
		urls[a.Name] = a.URL
	}
	// Only accept downloads from this repository's HTTPS release assets.
	prefix := "https://github.com/" + repository + "/releases/download/" + release.Tag + "/"
	for _, key := range []string{name, name + ".sha256"} {
		if !strings.HasPrefix(urls[key], prefix) {
			return errors.New("incomplete release assets")
		}
	}
	checksum, err := getURL(urls[name+".sha256"])
	if err != nil {
		return err
	}
	parts := strings.Fields(string(checksum))
	if len(parts) == 0 {
		return errors.New("empty checksum")
	}
	archive, err := getURL(urls[name])
	if err != nil {
		return err
	}
	sum := sha256.Sum256(archive)
	if hex.EncodeToString(sum[:]) != parts[0] {
		return errors.New("release archive checksum mismatch")
	}
	temp, err := os.MkdirTemp(m.Root, "download-")
	if err != nil {
		return err
	}
	defer os.RemoveAll(temp)
	if err = unpackZip(archive, temp); err != nil {
		return err
	}
	v, err := m.stage(temp)
	if err != nil {
		return err
	}
	if v.Version != release.Tag {
		return errors.New("downloaded release version mismatch")
	}
	return m.transition(State{v.Version, m.State.Edition})
}
