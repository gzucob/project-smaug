package main

import (
	"archive/zip"
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"slices"
	"strings"
	"testing"
)

func captureMCP(t *testing.T, action func() error) (string, error) {
	t.Helper()
	input, err := os.CreateTemp(t.TempDir(), "stdin")
	if err != nil {
		t.Fatal(err)
	}
	defer input.Close()
	output, err := os.CreateTemp(t.TempDir(), "stdout")
	if err != nil {
		t.Fatal(err)
	}
	defer output.Close()
	input.WriteString("{\"jsonrpc\":\"2.0\",\"id\":1}\n")
	input.Seek(0, io.SeekStart)
	oldIn, oldOut := os.Stdin, os.Stdout
	os.Stdin, os.Stdout = input, output
	defer func() { os.Stdin, os.Stdout = oldIn, oldOut }()
	actionErr := action()
	output.Seek(0, io.SeekStart)
	data, _ := io.ReadAll(output)
	return string(data), actionErr
}

func TestIntegratedMCPUsesStdioWithoutStartingServicesOrTakingWriterLock(t *testing.T) {
	log := mockDocker(t)
	m, _ := managerFixture(t)
	if err := os.Mkdir(m.file("operation.lock"), 0700); err != nil {
		t.Fatal(err)
	}
	writeJSON(m.file("pending.json"), Pending{Target: m.State})
	output, err := captureMCP(t, func() error { return run([]string{"--dir", m.Root, "mcp", "run"}) })
	if err != nil {
		t.Fatal(err)
	}
	if output != "{\"jsonrpc\":\"2.0\",\"id\":1}\n" {
		t.Fatalf("non-protocol stdout: %q", output)
	}
	calls, _ := os.ReadFile(log)
	if !strings.Contains(string(calls), "run --rm --no-deps -T mcp") || strings.Contains(string(calls), " up ") || strings.Contains(string(calls), "migrate") {
		t.Fatalf("unexpected operations: %s", calls)
	}
	if !exists(m.file("operation.lock")) || !exists(m.file("pending.json")) {
		t.Fatal("changed installation locks/recovery state")
	}
}

func connectorBundle(t *testing.T, version string, commit string) string {
	t.Helper()
	source := fixtureBundle(t, t.TempDir(), version, commit, "schema-a")
	if err := os.WriteFile(filepath.Join(source, "edition.txt"), []byte("mcp\n"), 0600); err != nil {
		t.Fatal(err)
	}
	// Include the edition marker in the same checksummed manifest as real bundles.
	var manifest Manifest
	readJSON(filepath.Join(source, "manifest.json"), &manifest)
	hash, _ := hashFile(filepath.Join(source, "edition.txt"))
	manifest.Files["edition.txt"] = hash
	writeJSON(filepath.Join(source, "manifest.json"), manifest)
	return source
}

func TestStandaloneConnectorInstallsAndUpdatesWithoutDatabases(t *testing.T) {
	log := mockDocker(t)
	m := &Manager{Root: filepath.Join(t.TempDir(), "standalone with spaces")}
	first := connectorBundle(t, "2026.10.17-"+strings.Repeat("a", 12), strings.Repeat("a", 40))
	if err := run([]string{"--dir", m.Root, "mcp", "setup", "--source", first, "--api-base-url", "http://127.0.0.1:18000/prefix"}); err != nil {
		t.Fatal(err)
	}
	if err := m.install([]string{"--source", first}); err == nil {
		t.Fatal("backend installer reused standalone MCP directory")
	}
	if exists(m.file("config.json")) || exists(m.file("pending.json")) || exists(m.file("backups")) {
		t.Fatal("created application state in connector installation")
	}
	output, err := captureMCP(t, func() error { return run([]string{"--dir", m.Root, "mcp", "run"}) })
	if err != nil {
		t.Fatal(err)
	}
	if output != "{\"jsonrpc\":\"2.0\",\"id\":1}\n" {
		t.Fatalf("non-protocol stdout: %q", output)
	}
	next := connectorBundle(t, "2026.10.17-"+strings.Repeat("b", 12), strings.Repeat("b", 40))
	if err := run([]string{"--dir", m.Root, "mcp", "update", "--source", next}); err != nil {
		t.Fatal(err)
	}
	var config ConnectorConfig
	readJSON(m.file("mcp.json"), &config)
	if config.APIBaseURL != "http://127.0.0.1:18000/prefix" {
		t.Fatal("update replaced API URL")
	}
	var state State
	readJSON(m.file("current.json"), &state)
	if !strings.HasSuffix(state.Version, strings.Repeat("b", 12)) {
		t.Fatal("update did not commit")
	}
	t.Setenv("SMAUG_DOCKER_FAIL", "pull")
	if err := run([]string{"--dir", m.Root, "mcp", "update", "--source", first}); err == nil {
		t.Fatal("expected pull failure")
	}
	var after State
	readJSON(m.file("current.json"), &after)
	if after != state {
		t.Fatal("failed pull changed current version")
	}
	calls, _ := os.ReadFile(log)
	for _, forbidden := range []string{"compose", "pg_dump", "migrate", "--volume", "--publish"} {
		if strings.Contains(string(calls), forbidden) {
			t.Fatalf("connector invoked %s: %s", forbidden, calls)
		}
	}
	if !strings.Contains(string(calls), "--entrypoint smaug-mcp") {
		t.Fatal("MCP entrypoint missing")
	}
}

func TestConnectorCannotOverwriteBackendOrAcceptWrongBundle(t *testing.T) {
	mockDocker(t)
	m, _ := managerFixture(t)
	bundle := connectorBundle(t, "2026.10.17-"+strings.Repeat("c", 12), strings.Repeat("c", 40))
	if err := m.setupConnector([]string{"--source", bundle}); err == nil {
		t.Fatal("overwrote backend")
	}
	m.Root = t.TempDir()
	os.WriteFile(filepath.Join(bundle, "edition.txt"), []byte("full\n"), 0600)
	if err := m.setupConnector([]string{"--source", bundle}); err == nil {
		t.Fatal("accepted full bundle as standalone MCP")
	}
}

func TestConnectorURLAndNetworkSelection(t *testing.T) {
	for _, bad := range []string{"file:///tmp/api", "localhost:8000", "https://user:pass@api.test", "https://api.test/?token=example", "https://api.test/#fragment"} {
		if validateAPIBase(bad) == nil {
			t.Fatalf("accepted %q", bad)
		}
	}
	for _, test := range []struct {
		input, system, want string
		network             []string
	}{
		{"http://127.0.0.1:18000/prefix", "linux", "http://127.0.0.1:18000/prefix", []string{"--network", "host"}},
		{"http://127.0.0.1:18000/prefix", "windows", "http://host.docker.internal:18000/prefix", nil},
		{"http://[::1]:8000", "darwin", "http://host.docker.internal:8000", nil},
		{"https://api.test/prefix", "darwin", "https://api.test/prefix", nil},
	} {
		got, network, err := connectorNetwork(test.input, test.system)
		if err != nil || got != test.want || !slices.Equal(network, test.network) {
			t.Fatalf("network selection: %s %v %v", got, network, err)
		}
	}
}

func TestClaudeRegistrationPreservesOtherConfigAndBacksUpReplacement(t *testing.T) {
	path := filepath.Join(t.TempDir(), "Claude config.json")
	original := []byte(`{"preferences":{"zoom":1.234567890123456789},"mcpServers":{"other":{"command":"other"},"smaug":{"command":"old-checkout"}}}`)
	os.WriteFile(path, original, 0600)
	spec := MCPLaunch{Command: "/path with spaces/smaugctl", Args: []string{"mcp", "run"}, Env: map[string]string{"SMAUG_MCP_DOCKER": "/docker"}}
	if err := registerClaude(path, "smaug", spec, false); err == nil {
		t.Fatal("silently replaced existing server")
	}
	data, _ := os.ReadFile(path)
	if !bytes.Equal(data, original) {
		t.Fatal("changed rejected registration")
	}
	if err := registerClaude(path, "smaug", spec, true); err != nil {
		t.Fatal(err)
	}
	var result map[string]json.RawMessage
	data, _ = os.ReadFile(path)
	json.Unmarshal(data, &result)
	var preferences map[string]json.RawMessage
	json.Unmarshal(result["preferences"], &preferences)
	if string(preferences["zoom"]) != "1.234567890123456789" {
		t.Fatal("rounded unrelated value")
	}
	var servers map[string]json.RawMessage
	json.Unmarshal(result["mcpServers"], &servers)
	if !sameLaunch(servers["smaug"], spec) || !strings.Contains(string(servers["other"]), "other") {
		t.Fatal("changed unrelated server")
	}
	backups, _ := filepath.Glob(path + ".smaug-backup-*")
	if len(backups) != 1 {
		t.Fatal("missing backup")
	}
	backup, _ := os.ReadFile(backups[0])
	if !bytes.Equal(backup, original) {
		t.Fatal("incorrect backup")
	}
	if err := registerClaude(path, "smaug", spec, false); err != nil {
		t.Fatal(err)
	}
	after, _ := os.ReadFile(path)
	if !bytes.Equal(data, after) {
		t.Fatal("idempotent registration rewrote config")
	}
}

func TestInvalidClaudeConfigIsNeverReplaced(t *testing.T) {
	for _, original := range []string{"{broken", "null", `{"mcpServers":[]}`, `{"mcpServers":null}`} {
		path := filepath.Join(t.TempDir(), "config.json")
		os.WriteFile(path, []byte(original), 0600)
		if err := registerClaude(path, "smaug", MCPLaunch{}, true); err == nil {
			t.Fatal("overwrote invalid JSON")
		}
		got, _ := os.ReadFile(path)
		if string(got) != original {
			t.Fatal("invalid config changed")
		}
	}
}

func TestCodexProcess(t *testing.T) {
	if os.Getenv("SMAUG_FAKE_CODEX") != "1" {
		return
	}
	log, _ := os.OpenFile(os.Getenv("SMAUG_CODEX_LOG"), os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0600)
	data, _ := json.Marshal(os.Args)
	log.Write(append(data, '\n'))
	log.Close()
	if slices.Contains(os.Args, "list") {
		io.WriteString(os.Stdout, os.Getenv("SMAUG_CODEX_SERVERS"))
	}
	os.Exit(0)
}

func TestCodexRegistrationUsesOfficialCLIAndPreservesOriginalBackup(t *testing.T) {
	directory := t.TempDir()
	log := filepath.Join(directory, "commands.jsonl")
	config := filepath.Join(directory, "config.toml")
	original := []byte("model = \"existing-model\"\n[mcp_servers.other]\ncommand = \"other\"\n")
	os.WriteFile(config, original, 0600)
	t.Setenv("CODEX_HOME", directory)
	t.Setenv("SMAUG_CODEX_LOG", log)
	t.Setenv("SMAUG_CODEX_SERVERS", `[{"name":"other","transport":{"command":"other"}},{"name":"smaug","transport":{"command":"old"}}]`)
	previousFind, previousCommand := findProgram, clientCommand
	findProgram = func(name string) (string, error) { return os.Args[0], nil }
	clientCommand = func(name string, args ...string) (*exec.Cmd, error) {
		cmd := exec.Command(name, append([]string{"-test.run=TestCodexProcess", "--"}, args...)...)
		cmd.Env = append(os.Environ(), "SMAUG_FAKE_CODEX=1")
		return cmd, nil
	}
	t.Cleanup(func() { findProgram, clientCommand = previousFind, previousCommand })
	spec := MCPLaunch{Command: "path with spaces", Args: []string{"mcp", "run"}, Env: map[string]string{"SMAUG_MCP_DOCKER": "docker with spaces"}}
	if err := registerCodex("smaug", spec, false); err == nil {
		t.Fatal("replaced Codex registration without consent")
	}
	if err := registerCodex("smaug", spec, true); err != nil {
		t.Fatal(err)
	}
	backups, _ := filepath.Glob(config + ".smaug-backup-*")
	if len(backups) != 1 {
		t.Fatal("missing Codex backup")
	}
	saved, _ := os.ReadFile(backups[0])
	if !bytes.Equal(saved, original) {
		t.Fatal("backup changed original TOML")
	}
	commands, _ := os.ReadFile(log)
	last := strings.Split(strings.TrimSpace(string(commands)), "\n")
	var arguments []string
	json.Unmarshal([]byte(last[len(last)-1]), &arguments)
	expected := []string{"mcp", "add", "smaug", "--env", "SMAUG_MCP_DOCKER=docker with spaces", "--", "path with spaces", "mcp", "run"}
	if !slices.Equal(arguments[len(arguments)-len(expected):], expected) {
		t.Fatalf("incorrect official CLI arguments: %v", arguments)
	}
	transport, _ := json.Marshal(spec)
	t.Setenv("SMAUG_CODEX_SERVERS", `[{"name":"smaug","transport":`+string(transport)+`}]`)
	if err := registerCodex("smaug", spec, false); err != nil {
		t.Fatal(err)
	}
	backups, _ = filepath.Glob(config + ".smaug-backup-*")
	if len(backups) != 1 {
		t.Fatal("idempotent registration created backup")
	}
}

func TestClientBatchShimQuoting(t *testing.T) {
	if runtime.GOOS != "windows" {
		t.Skip("Windows batch shim")
	}
	path := filepath.Join(t.TempDir(), "Codex with spaces.cmd")
	os.WriteFile(path, []byte("@echo off\r\necho \"%~1\"\r\necho \"%~2\"\r\n"), 0600)
	command, err := newClientCommand(path, "argument with spaces", "a&b")
	if err != nil {
		t.Fatal(err)
	}
	data, err := command.CombinedOutput()
	if err != nil || strings.ReplaceAll(strings.TrimSpace(string(data)), "\r", "") != "\"argument with spaces\"\n\"a&b\"" {
		t.Fatalf("batch invocation: %s %v", data, err)
	}
	if _, err = newClientCommand(path, "%expanded%"); err == nil {
		t.Fatal("accepted batch expansion")
	}
}

func TestMCPConfigCanBeGeneratedWithDockerStopped(t *testing.T) {
	log := mockDocker(t)
	m, _ := managerFixture(t)
	old := findProgram
	findProgram = func(name string) (string, error) {
		if name == "docker" {
			return os.Args[0], nil
		}
		return "", errors.New("missing")
	}
	t.Cleanup(func() { findProgram = old })
	t.Setenv("SMAUG_DOCKER_FAIL", "version")
	_, err := captureMCP(t, func() error { return run([]string{"--dir", m.Root, "mcp", "config", "--client", "codex"}) })
	if err != nil {
		t.Fatal(err)
	}
	calls, _ := os.ReadFile(log)
	if len(calls) != 0 {
		t.Fatalf("config generation contacted Docker: %s", calls)
	}
}

type mcpReleaseTransport func(*http.Request) (*http.Response, error)

func (f mcpReleaseTransport) RoundTrip(request *http.Request) (*http.Response, error) {
	return f(request)
}

func TestStandaloneUpdateDownloadsChecksummedMCPAssetAndRetainsAPI(t *testing.T) {
	mockDocker(t)
	m := &Manager{Root: t.TempDir()}
	first := connectorBundle(t, "2026.10.17-"+strings.Repeat("a", 12), strings.Repeat("a", 40))
	if err := m.setupConnector([]string{"--source", first, "--api-base-url", "http://127.0.0.1:18000"}); err != nil {
		t.Fatal(err)
	}
	nextVersion := "2026.10.17-" + strings.Repeat("b", 12)
	next := connectorBundle(t, nextVersion, strings.Repeat("b", 40))
	var archive bytes.Buffer
	writer := zip.NewWriter(&archive)
	files, _ := os.ReadDir(next)
	for _, path := range files {
		entry, err := writer.Create(path.Name())
		if err != nil {
			t.Fatal(err)
		}
		content, _ := os.ReadFile(filepath.Join(next, path.Name()))
		entry.Write(content)
	}
	writer.Close()
	checksum := sha256.Sum256(archive.Bytes())
	tag := nextVersion
	previous := http.DefaultTransport
	http.DefaultTransport = mcpReleaseTransport(func(request *http.Request) (*http.Response, error) {
		if request.Method != "GET" {
			t.Fatal("update wrote over HTTP")
		}
		prefix := "https://github.com/" + repository + "/releases/download/" + tag + "/"
		name := "smaug-mcp-" + tag + ".zip"
		var body []byte
		switch request.URL.String() {
		case "https://api.github.com/repos/" + repository + "/releases/latest":
			body, _ = json.Marshal(map[string]any{"tag_name": tag, "assets": []map[string]string{{"name": name, "browser_download_url": prefix + name}, {"name": name + ".sha256", "browser_download_url": prefix + name + ".sha256"}}})
		case prefix + name + ".sha256":
			body = []byte(hex.EncodeToString(checksum[:]) + "  " + name + "\n")
		case prefix + name:
			body = archive.Bytes()
		default:
			t.Fatalf("unexpected release request: %s", request.URL)
		}
		return &http.Response{StatusCode: 200, Header: make(http.Header), Body: io.NopCloser(bytes.NewReader(body)), Request: request}, nil
	})
	t.Cleanup(func() { http.DefaultTransport = previous })
	if err := m.mcp([]string{"update"}); err != nil {
		t.Fatal(err)
	}
	var state State
	readJSON(m.file("current.json"), &state)
	if state.Version != nextVersion || state.Edition != "mcp" {
		t.Fatal("wrong release committed")
	}
	var config ConnectorConfig
	readJSON(m.file("mcp.json"), &config)
	if config.APIBaseURL != "http://127.0.0.1:18000" {
		t.Fatal("changed API on update")
	}
	tag = "2026.10.17-" + strings.Repeat("c", 12)
	if err := m.mcp([]string{"update"}); err == nil || !strings.Contains(err.Error(), "version mismatch") {
		t.Fatalf("accepted wrong release identity: %v", err)
	}
	var unchanged State
	readJSON(m.file("current.json"), &unchanged)
	if unchanged != state {
		t.Fatal("invalid download changed current release")
	}
}
