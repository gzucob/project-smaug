package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"
)

func guiFixture(t *testing.T) *guiServer {
	t.Helper()
	return &guiServer{root: t.TempDir(), origin: "http://127.0.0.1:12345", token: strings.Repeat("a", 64), state: guiSnapshot{Edition: "full"}, close: make(chan struct{}, 1), open: func(string) error { return nil }}
}
func guiRequest(g *guiServer, method, path, body, origin string) *httptest.ResponseRecorder {
	req := httptest.NewRequest(method, g.origin+"/"+g.token+"/"+path, strings.NewReader(body))
	if method == http.MethodPost {
		req.Header.Set("Content-Type", "application/json")
		req.Header.Set("Origin", origin)
	}
	response := httptest.NewRecorder()
	g.ServeHTTP(response, req)
	return response
}
func TestGUIRejectsForeignHostsOriginsAndUnknownActions(t *testing.T) {
	g := guiFixture(t)
	for _, origin := range []string{"", "https://example.com", "null"} {
		if r := guiRequest(g, "POST", "action", `{"action":"install","edition":"full"}`, origin); r.Code != 403 {
			t.Fatalf("accepted origin %q: %d", origin, r.Code)
		}
	}
	request := httptest.NewRequest("GET", "http://example.com/"+g.token+"/status", nil)
	response := httptest.NewRecorder()
	g.ServeHTTP(response, request)
	if response.Code != 403 {
		t.Fatal("DNS rebinding host accepted")
	}
	request = httptest.NewRequest("GET", g.origin+"/wrong/status", nil)
	response = httptest.NewRecorder()
	g.ServeHTTP(response, request)
	if response.Code != 404 {
		t.Fatal("missing session secret accepted")
	}
	if r := guiRequest(g, "POST", "action", `{"action":"cli","edition":"full"}`, g.origin); r.Code != 400 {
		t.Fatal("arbitrary CLI accepted")
	}
	if r := guiRequest(g, "POST", "action", `{"action":"install","edition":"../other"}`, g.origin); r.Code != 400 {
		t.Fatal("arbitrary edition accepted")
	}
}
func TestGUIEmbedsAllAssetsAndStatusWithoutExecutingCommands(t *testing.T) {
	g := guiFixture(t)
	for _, path := range []string{"", "app.js", "style.css", "icon.svg", "status"} {
		r := guiRequest(g, "GET", path, "", "")
		if r.Code != 200 {
			t.Fatalf("missing asset %s", path)
		}
		if r.Header().Get("Cache-Control") != "no-store" || r.Header().Get("Content-Security-Policy") == "" {
			t.Fatal("missing local UI protections")
		}
	}
	if r := guiRequest(g, "GET", "../../config.json", "", ""); r.Code != 404 {
		t.Fatal("exposed filesystem")
	}
}
func TestGUIAdmitsOneOperationAndSurfacesRecoverableFailure(t *testing.T) {
	g := guiFixture(t)
	started, finish := make(chan []string, 1), make(chan struct{})
	g.execute = func(args []string, out io.Writer) error {
		started <- args
		io.WriteString(out, "Dados CVM: 12/50\n")
		<-finish
		return fmt.Errorf("network offline")
	}
	r := guiRequest(g, "POST", "action", `{"action":"install","edition":"backend"}`, g.origin)
	if r.Code != 202 {
		t.Fatal(r.Body.String())
	}
	args := <-started
	if strings.Join(args, " ") != "installer --edition backend" {
		t.Fatal(args)
	}
	if r := guiRequest(g, "POST", "action", `{"action":"install","edition":"full"}`, g.origin); r.Code != 409 {
		t.Fatal("concurrent operation admitted")
	}
	if r := guiRequest(g, "POST", "action", `{"action":"close"}`, g.origin); r.Code != 409 {
		t.Fatal("closed during active ingestion")
	}
	close(finish)
	deadline := time.Now().Add(time.Second)
	for time.Now().Before(deadline) {
		r = guiRequest(g, "GET", "status", "", "")
		var snapshot guiSnapshot
		json.Unmarshal(r.Body.Bytes(), &snapshot)
		if !snapshot.Busy {
			if snapshot.Error == "" || !strings.Contains(snapshot.Log, "network offline") || !strings.Contains(snapshot.Log, "12/50") {
				t.Fatalf("lost error or progress: %+v", snapshot)
			}
			return
		}
		time.Sleep(time.Millisecond)
	}
	t.Fatal("operation did not finish")
}
func TestGUILogIsBoundedAndInstallationRequiresPreparedState(t *testing.T) {
	g := guiFixture(t)
	guiLogWriter{g}.Write(bytes.Repeat([]byte("x"), guiLogLimit+100))
	if len(g.state.Log) != guiLogLimit {
		t.Fatal("unbounded browser log")
	}
	m, _ := managerFixture(t)
	g.root = m.Root
	g.refreshInstallation()
	if !g.state.Installed || g.state.Prepared {
		t.Fatal("installation considered prepared before ingestion")
	}
	if err := writeJSON(m.file("prepared.json"), m.State); err != nil {
		t.Fatal(err)
	}
	g.refreshInstallation()
	if !g.state.Prepared || g.state.URL != "http://localhost:18000/docs" {
		t.Fatalf("wrong backend endpoint: %+v", g.state)
	}
	writeJSON(m.file("preparing.json"), m.State)
	g.refreshInstallation()
	if g.state.Prepared {
		t.Fatal("interrupted preparation ignored")
	}
}
func TestDesktopShortcutIsManagedAndSurvivesSpaces(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("Windows shortcut creation requires a graphical user session")
	}
	m, _ := managerFixture(t)
	home := t.TempDir()
	if runtime.GOOS == "linux" {
		t.Setenv("XDG_DATA_HOME", filepath.Join(home, "data"))
	}
	if err := m.installDesktopShortcut(home); err != nil {
		t.Fatal(err)
	}
	if err := m.installDesktopShortcut(home); err != nil {
		t.Fatal(err)
	}
	if runtime.GOOS == "linux" {
		path := filepath.Join(home, "data", "applications", "smaug.desktop")
		data, _ := os.ReadFile(path)
		if !strings.Contains(string(data), "Terminal=false") || !strings.Contains(string(data), `path with spaces/smaugctl" gui`) {
			t.Fatalf("bad shortcut: %s", data)
		}
		os.WriteFile(path, []byte("another application"), 0600)
		if err := m.installDesktopShortcut(home); err == nil {
			t.Fatal("overwrote another app's shortcut")
		}
	}
}
func TestGUISelectionLeavesExplicitCLINonInteractive(t *testing.T) {
	for _, args := range [][]string{nil, {"gui"}, {"--dir", "/tmp/path with spaces", "gui"}} {
		if _, gui := guiArguments(args); !gui {
			t.Fatalf("not graphical: %v", args)
		}
	}
	for _, args := range [][]string{{"installer"}, {"upgrade"}, {"mcp", "run"}, {"status"}, {"--dir", "/tmp/example", "installer"}} {
		if _, gui := guiArguments(args); gui {
			t.Fatalf("CLI hijacked: %v", args)
		}
	}
}

func TestDownloadedGUIInstallerDoesNotDelegateSetupToAnOlderManager(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("POSIX subprocess fixture")
	}
	m, _ := managerFixture(t)
	executable := filepath.Join(t.TempDir(), "new installer")
	if err := os.WriteFile(executable, []byte("#!/bin/sh\nprintf '%s\\n' \"$@\"\n"), 0700); err != nil {
		t.Fatal(err)
	}
	g := guiFixture(t)
	g.root, g.executable = m.Root, executable
	var output bytes.Buffer
	if err := g.executeCommand([]string{"installer", "--edition", "backend"}, &output); err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(output.String(), "installer\n--edition\nbackend") {
		t.Fatal("the downloaded installer was not used")
	}
}

func TestDesktopEnvironmentPreservesWindowsPathCasingAndExistingPrograms(t *testing.T) {
	env := desktopEnvironment([]string{"Path=/existing/programs", "OTHER=kept"})
	if !strings.HasPrefix(env[0], "PATH=/existing/programs") || env[1] != "OTHER=kept" {
		t.Fatal("desktop PATH discarded installed programs", env)
	}
	first := strings.Join(env, "\n")
	if strings.Join(desktopEnvironment(env), "\n") != first {
		t.Fatal("desktop search paths duplicated")
	}
}
