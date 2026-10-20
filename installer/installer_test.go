package main

import (
	"archive/zip"
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestNativeInstallerDownloadsVerifiedEditionWithoutManualExtraction(t *testing.T) {
	version := "2026.10.18-" + strings.Repeat("c", 12)
	bundle := fixtureBundle(t, t.TempDir(), version, strings.Repeat("c", 40), "head")
	var content bytes.Buffer
	zipped := zip.NewWriter(&content)
	entries, _ := os.ReadDir(bundle)
	for _, entry := range entries {
		file, _ := zipped.Create(entry.Name())
		data, _ := os.ReadFile(filepath.Join(bundle, entry.Name()))
		file.Write(data)
	}
	zipped.Close()
	sum := sha256.Sum256(content.Bytes())
	name := "smaug-backend-" + version + ".zip"
	prefix := "https://github.com/" + repository + "/releases/download/" + version + "/"
	previous := http.DefaultTransport
	http.DefaultTransport = mcpReleaseTransport(func(request *http.Request) (*http.Response, error) {
		var body []byte
		switch request.URL.String() {
		case "https://api.github.com/repos/" + repository + "/releases/tags/" + version:
			body, _ = json.Marshal(map[string]any{"tag_name": version, "assets": []map[string]string{
				{"name": name, "browser_download_url": prefix + name},
				{"name": name + ".sha256", "browser_download_url": prefix + name + ".sha256"},
			}})
		case prefix + name + ".sha256":
			body = []byte(hex.EncodeToString(sum[:]))
		case prefix + name:
			body = content.Bytes()
		default:
			t.Fatalf("unexpected bootstrap request: %s", request.URL)
		}
		return &http.Response{StatusCode: 200, Header: make(http.Header), Body: io.NopCloser(bytes.NewReader(body)), Request: request}, nil
	})
	t.Cleanup(func() { http.DefaultTransport = previous })
	m := &Manager{Root: t.TempDir()}
	release, err := m.downloadRelease("backend", "", "tags/"+version)
	if err != nil {
		t.Fatal(err)
	}
	if release.Version != version || !exists(m.file("releases", version, "compose.yaml")) {
		t.Fatal("bootstrap did not stage its edition")
	}
}

func TestOperationGuardExcludesConcurrentInstallerAndReleasesAfterClose(t *testing.T) {
	path := filepath.Join(t.TempDir(), "guard")
	unlock, err := lockOperation(path)
	if err != nil {
		t.Fatal(err)
	}
	if second, err := lockOperation(path); err == nil {
		second()
		t.Fatal("concurrent writer admitted")
	}
	unlock()
	unlock, err = lockOperation(path)
	if err != nil {
		t.Fatal(err)
	}
	unlock()
}

func TestFriendlyCommandAliasesInstallAndUpgrade(t *testing.T) {
	if os.PathSeparator == '\\' {
		t.Skip("POSIX command registration; Windows bootstrap has separate coverage")
	}
	log := mockDocker(t)
	root, home := t.TempDir(), t.TempDir()
	previous := userHomeDir
	userHomeDir = func() (string, error) { return home, nil }
	t.Cleanup(func() { userHomeDir = previous })
	version := "2026.10.17-" + strings.Repeat("a", 12)
	source := fixtureBundle(t, t.TempDir(), version, strings.Repeat("a", 40), "schema-a")
	if err := run([]string{"--dir", root, "installer", "--backend", "--source", source}); err != nil {
		t.Fatal(err)
	}
	var config Config
	readJSON(filepath.Join(root, "config.json"), &config)
	if config.Edition != "backend" {
		t.Fatal("--backend ignored")
	}
	next := fixtureBundle(t, t.TempDir(), "2026.10.18-"+strings.Repeat("b", 12), strings.Repeat("b", 40), "schema-b")
	if err := run([]string{"--dir", root, "upgrade", "--source", next}); err != nil {
		t.Fatal(err)
	}
	commands, _ := os.ReadFile(log)
	if strings.Count(string(commands), "cli prepare") != 2 {
		t.Fatal("installer and upgrade must both prepare data")
	}
	if !exists(filepath.Join(home, ".local", "bin", "smaug")) {
		t.Fatal("command not registered")
	}
}

func TestPreparationFailureRemainsResumableAndDoesNotStartAPI(t *testing.T) {
	log := mockDocker(t)
	m, _ := managerFixture(t)
	t.Setenv("SMAUG_DOCKER_FAIL", "cli prepare")
	if err := m.prepare(m.State); err == nil {
		t.Fatal("failed preparation accepted")
	}
	if !exists(m.file("preparing.json")) || exists(m.file("prepared.json")) {
		t.Fatal("missing resume marker")
	}
	commands, _ := os.ReadFile(log)
	if strings.Contains(string(commands), "--wait-timeout") {
		t.Fatal("API started on incomplete data")
	}
	t.Setenv("SMAUG_DOCKER_FAIL", "")
	if err := m.prepare(m.State); err != nil {
		t.Fatal(err)
	}
	if exists(m.file("preparing.json")) || !exists(m.file("prepared.json")) {
		t.Fatal("resume did not finish")
	}
	commands, _ = os.ReadFile(log)
	before := strings.Count(string(commands), "cli prepare")
	if err := m.prepare(m.State); err != nil {
		t.Fatal(err)
	}
	commands, _ = os.ReadFile(log)
	if strings.Count(string(commands), "cli prepare") != before {
		t.Fatal("reinstall repeated completed preparation")
	}
}

func TestTransitionPreparesDataBeforeStartingServices(t *testing.T) {
	log := mockDocker(t)
	m, next := managerFixture(t)
	if err := m.transition(next); err != nil {
		t.Fatal(err)
	}
	commands, _ := os.ReadFile(log)
	s := string(commands)
	migration, preparation, start := strings.Index(s, "migrate upgrade head"), strings.Index(s, "cli prepare"), strings.LastIndex(s, "--wait-timeout")
	if migration < 0 || preparation < migration || start < preparation {
		t.Fatalf("wrong order: %s", s)
	}
}

func TestUpgradeNoticeIsCachedAndOfflineUseStillWorks(t *testing.T) {
	m, _ := managerFixture(t)
	calls := 0
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls++
		io.WriteString(w, `{"tag_name":"2026.10.18-bbbbbbbbbbbb"}`)
	}))
	previous := releaseAPI
	releaseAPI = server.URL
	t.Cleanup(func() { releaseAPI = previous; server.Close() })
	m.notifyUpgrade()
	m.notifyUpgrade()
	if calls != 1 || !exists(m.file("upgrade-check.json")) {
		t.Fatal("notice was not cached")
	}
	server.Close()
	os.Remove(m.file("upgrade-check.json"))
	before := time.Now()
	m.notifyUpgrade()
	if time.Since(before) > 3*time.Second {
		t.Fatal("offline check blocked local use")
	}
}

func TestInstallRegistersCommandWithoutOverwritingOtherPrograms(t *testing.T) {
	if os.PathSeparator == '\\' {
		t.Skip("POSIX shell registration")
	}
	m, _ := managerFixture(t)
	home := t.TempDir()
	old := userHomeDir
	userHomeDir = func() (string, error) { return home, nil }
	t.Cleanup(func() { userHomeDir = old })
	t.Setenv("SHELL", "/bin/bash")
	if err := m.installCommand(); err != nil {
		t.Fatal(err)
	}
	if err := m.installCommand(); err != nil {
		t.Fatal(err)
	}
	profile, _ := os.ReadFile(filepath.Join(home, ".bashrc"))
	if strings.Count(string(profile), "# Smaug command") != 1 {
		t.Fatal("duplicate shell registration")
	}
	os.WriteFile(filepath.Join(home, ".local", "bin", "smaug"), []byte("another program"), 0700)
	if err := m.installCommand(); err == nil {
		t.Fatal("overwrote another program")
	}
}

func TestAbandonedLockCanBeResumedButActiveOperationCannot(t *testing.T) {
	mockDocker(t)
	m, _ := managerFixture(t)
	lock := m.file("operation.lock")
	os.Mkdir(lock, 0700)
	writeJSON(filepath.Join(lock, "owner.json"), map[string]int{"pid": os.Getpid()})
	if err := m.acquireLock(); err == nil {
		t.Fatal("active writer displaced")
	}
	// No supported OS can assign a process this ID.
	writeJSON(filepath.Join(lock, "owner.json"), map[string]int{"pid": 2147483647})
	if err := m.acquireLock(); err != nil {
		t.Fatal(err)
	}
}

func TestNewInstallerRecoversFailedInitialReleaseBeforeRetryingOldCode(t *testing.T) {
	if os.PathSeparator == '\\' {
		t.Skip("POSIX command registration; recovery state checks run on every platform")
	}
	log := mockDocker(t)
	m, next := managerFixture(t)
	previousHome := userHomeDir
	home := t.TempDir()
	userHomeDir = func() (string, error) { return home, nil }
	t.Cleanup(func() { userHomeDir = previousHome })
	if err := writeJSON(m.file("pending.json"), Pending{Target: m.State}); err != nil {
		t.Fatal(err)
	}
	t.Setenv("SMAUG_DOCKER_FAIL", m.State.Version)
	if err := m.install([]string{"--source", m.file("releases", next.Version), "--edition", next.Edition}); err != nil {
		t.Fatal(err)
	}
	commands, _ := os.ReadFile(log)
	if strings.Contains(string(commands), "--project-directory "+m.file("releases", "2026.10.17-"+strings.Repeat("a", 12))) {
		t.Fatal("retried the broken release before selecting its replacement")
	}
	if m.State != next || exists(m.file("pending.json")) || !exists(m.file("prepared.json")) {
		t.Fatal("replacement did not finish initial preparation")
	}
}

func TestInitialRecoveryRejectsUnverifiedReplacementWithoutChangingPendingState(t *testing.T) {
	m, next := managerFixture(t)
	pending := Pending{Target: m.State}
	if err := writeJSON(m.file("pending.json"), pending); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(m.file("releases", next.Version, "compose.yaml"), []byte("changed"), 0600); err != nil {
		t.Fatal(err)
	}
	if _, err := m.recoverInitialInstall(m.file("releases", next.Version), next.Edition); err == nil {
		t.Fatal("unverified replacement accepted")
	}
	var after Pending
	if err := readJSON(m.file("pending.json"), &after); err != nil || after != pending {
		t.Fatal("failed verification changed pending recovery")
	}
}

func TestInitialRecoveryPreservesPendingUpgradeAndOriginalBackup(t *testing.T) {
	m, next := managerFixture(t)
	pending := Pending{Previous: m.State, Target: next, Backup: "original-backup"}
	if err := writeJSON(m.file("pending.json"), pending); err != nil {
		t.Fatal(err)
	}
	if _, err := m.recoverInitialInstall(m.file("releases", next.Version), next.Edition); err == nil {
		t.Fatal("replaced an interrupted upgrade")
	}
	var after Pending
	if err := readJSON(m.file("pending.json"), &after); err != nil || after != pending {
		t.Fatal("original upgrade recovery or backup changed")
	}
}

func TestInitialRecoveryMigrationFailureKeepsReplacementResumable(t *testing.T) {
	mockDocker(t)
	m, next := managerFixture(t)
	old := m.State
	if err := writeJSON(m.file("pending.json"), Pending{Target: old}); err != nil {
		t.Fatal(err)
	}
	t.Setenv("SMAUG_DOCKER_FAIL", "migrate upgrade head")
	if _, err := m.recoverInitialInstall(m.file("releases", next.Version), next.Edition); err == nil {
		t.Fatal("failed migration accepted")
	}
	var pending Pending
	if err := readJSON(m.file("pending.json"), &pending); err != nil || pending.Target != next || pending.Previous.Version != "" {
		t.Fatal("replacement is not recoverable")
	}
	var current State
	if err := readJSON(m.file("current.json"), &current); err != nil || current != old {
		t.Fatal("committed failed replacement")
	}
	t.Setenv("SMAUG_DOCKER_FAIL", "")
	if _, err := m.recoverInitialInstall(m.file("releases", next.Version), next.Edition); err != nil {
		t.Fatal(err)
	}
	if m.State != next || exists(m.file("pending.json")) {
		t.Fatal("replacement retry did not commit")
	}
}

func TestInstallerKeepsEditionOfRecoveredUpgradeWhenNoEditionWasRequested(t *testing.T) {
	if os.PathSeparator == '\\' {
		t.Skip("POSIX command registration")
	}
	mockDocker(t)
	m, next := managerFixture(t)
	next.Edition = "full"
	previousHome := userHomeDir
	home := t.TempDir()
	userHomeDir = func() (string, error) { return home, nil }
	t.Cleanup(func() { userHomeDir = previousHome })
	if err := writeJSON(m.file("pending.json"), Pending{Previous: m.State, Target: next, Backup: t.TempDir()}); err != nil {
		t.Fatal(err)
	}
	if err := m.install([]string{"--source", m.file("releases", next.Version)}); err != nil {
		t.Fatal(err)
	}
	if m.State != next || m.Config.Edition != "full" {
		t.Fatal("recovery reverted the requested full edition")
	}
}

func TestDownloadedInstallerRecoversInitialFailureUsingLatestVerifiedRelease(t *testing.T) {
	if os.PathSeparator == '\\' {
		t.Skip("POSIX command registration")
	}
	mockDocker(t)
	m, next := managerFixture(t)
	next.Edition = "full"
	oldVersion := releaseVersion
	releaseVersion = next.Version
	oldHome := userHomeDir
	home := t.TempDir()
	userHomeDir = func() (string, error) { return home, nil }
	t.Cleanup(func() { releaseVersion, userHomeDir = oldVersion, oldHome })
	if err := writeJSON(m.file("pending.json"), Pending{Target: m.State}); err != nil {
		t.Fatal(err)
	}
	var content bytes.Buffer
	archive := zip.NewWriter(&content)
	entries, err := os.ReadDir(m.file("releases", next.Version))
	if err != nil {
		t.Fatal(err)
	}
	for _, entry := range entries {
		file, err := archive.Create(entry.Name())
		if err != nil {
			t.Fatal(err)
		}
		data, err := os.ReadFile(m.file("releases", next.Version, entry.Name()))
		if err != nil {
			t.Fatal(err)
		}
		if _, err := file.Write(data); err != nil {
			t.Fatal(err)
		}
	}
	if err := archive.Close(); err != nil {
		t.Fatal(err)
	}
	checksum := sha256.Sum256(content.Bytes())
	name := "smaug-full-" + next.Version + ".zip"
	prefix := "https://github.com/" + repository + "/releases/download/" + next.Version + "/"
	previousTransport := http.DefaultTransport
	http.DefaultTransport = mcpReleaseTransport(func(request *http.Request) (*http.Response, error) {
		var body []byte
		switch request.URL.String() {
		case "https://api.github.com/repos/" + repository + "/releases/latest":
			body, _ = json.Marshal(map[string]any{"tag_name": next.Version, "assets": []map[string]string{
				{"name": name, "browser_download_url": prefix + name},
				{"name": name + ".sha256", "browser_download_url": prefix + name + ".sha256"},
			}})
		case prefix + name:
			body = content.Bytes()
		case prefix + name + ".sha256":
			body = []byte(hex.EncodeToString(checksum[:]))
		default:
			t.Fatalf("unexpected recovery request: %s", request.URL)
		}
		return &http.Response{StatusCode: 200, Header: make(http.Header), Body: io.NopCloser(bytes.NewReader(body)), Request: request}, nil
	})
	t.Cleanup(func() { http.DefaultTransport = previousTransport })
	t.Setenv("SMAUG_DOCKER_FAIL", m.State.Version)
	if err := m.install(nil); err != nil {
		t.Fatal(err)
	}
	if m.State != next || exists(m.file("pending.json")) {
		t.Fatal("new downloaded installer did not recover the failed initial release")
	}
}
