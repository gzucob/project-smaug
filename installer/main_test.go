package main

import (
	"archive/zip"
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

func TestDockerProcess(t *testing.T) {
	if os.Getenv("SMAUG_FAKE_DOCKER") != "1" {
		return
	}
	args := strings.Join(os.Args, " ")
	log, _ := os.OpenFile(os.Getenv("SMAUG_DOCKER_LOG"), os.O_APPEND|os.O_WRONLY|os.O_CREATE, 0600)
	fmt.Fprintln(log, args)
	log.Close()
	if failed := os.Getenv("SMAUG_DOCKER_FAIL"); failed != "" && strings.Contains(args, failed) {
		os.Exit(7)
	}
	if strings.Contains(args, "smaug-mcp") || strings.HasSuffix(args, "-T mcp") {
		io.Copy(os.Stdout, os.Stdin)
	}
	if strings.Contains(args, "pg_dump") {
		fmt.Print("binary-sql-backup")
	}
	if strings.Contains(args, "--status running") {
		fmt.Print("running-service")
	}
	if strings.Contains(args, "label=io.smaug.operation=cli") && os.Getenv("SMAUG_ACTIVE_CLI") == "1" {
		fmt.Print("active-cli")
	}
	os.Exit(0)
}
func mockDocker(t *testing.T) string {
	t.Helper()
	log := filepath.Join(t.TempDir(), "commands.log")
	t.Setenv("SMAUG_DOCKER_LOG", log)
	old := dockerCommand
	dockerCommand = func(_ string, args ...string) *exec.Cmd {
		cmd := exec.Command(os.Args[0], append([]string{"-test.run=TestDockerProcess", "--"}, args...)...)
		cmd.Env = append(os.Environ(), "SMAUG_FAKE_DOCKER=1")
		return cmd
	}
	t.Cleanup(func() { dockerCommand = old })
	return log
}
func fixtureBundle(t *testing.T, root, version, commit, schema string) string {
	t.Helper()
	path := filepath.Join(root, version)
	if err := os.MkdirAll(path, 0700); err != nil {
		t.Fatal(err)
	}
	images := map[string]string{}
	for _, name := range []string{"backend", "frontend", "mongo", "postgres"} {
		images[name] = "example/" + name + "@sha256:" + strings.Repeat("a", 64)
	}
	v := Manifest{Version: version, Commit: commit, Schema: schema, Images: images, Files: map[string]string{}}
	for _, name := range []string{"compose.yaml", binaryName()} {
		if err := os.WriteFile(filepath.Join(path, name), []byte("fixture"), 0600); err != nil {
			t.Fatal(err)
		}
		hash, _ := hashFile(filepath.Join(path, name))
		v.Files[name] = hash
	}
	if err := writeJSON(filepath.Join(path, "manifest.json"), v); err != nil {
		t.Fatal(err)
	}
	return path
}
func managerFixture(t *testing.T) (*Manager, State) {
	t.Helper()
	m := &Manager{Root: filepath.Join(t.TempDir(), "path with spaces")}
	m.Config = Config{"smaug-abcdef012345", "backend", 18000, 13000, false, 21600}
	m.State = State{"2026.10.17-" + strings.Repeat("a", 12), "backend"}
	source := fixtureBundle(t, t.TempDir(), m.State.Version, strings.Repeat("a", 40), "schema-a")
	if _, err := m.stage(source); err != nil {
		t.Fatal(err)
	}
	next := State{"2026.10.17-" + strings.Repeat("b", 12), "backend"}
	source = fixtureBundle(t, t.TempDir(), next.Version, strings.Repeat("b", 40), "schema-b")
	if _, err := m.stage(source); err != nil {
		t.Fatal(err)
	}
	if err := writeJSON(m.file("config.json"), m.Config); err != nil {
		t.Fatal(err)
	}
	if err := writeJSON(m.file("current.json"), m.State); err != nil {
		t.Fatal(err)
	}
	return m, next
}
func TestTransitionBacksUpBeforeMigrationAndCommitsAfterHealth(t *testing.T) {
	log := mockDocker(t)
	m, next := managerFixture(t)
	if err := m.transition(next); err != nil {
		t.Fatal(err)
	}
	commands, _ := os.ReadFile(log)
	s := string(commands)
	stop := strings.Index(s, "stop api frontend")
	backup := strings.Index(s, "pg_dump")
	migration := strings.Index(s, "migrate upgrade head")
	ready := strings.LastIndex(s, "--wait-timeout")
	if !(stop >= 0 && backup > stop && migration > backup && ready > migration) {
		t.Fatalf("unsafe order: %s", s)
	}
	var current State
	readJSON(m.file("current.json"), &current)
	if current != next || exists(m.file("pending.json")) {
		t.Fatal("transition did not commit cleanly")
	}
	entries, _ := os.ReadDir(m.file("backups"))
	if len(entries) != 1 {
		t.Fatal("missing backup")
	}
	if !exists(m.file("backups", entries[0].Name(), "backup.json")) {
		t.Fatal("backup not complete")
	}
}
func TestBackupFailureNeverRunsMigrationOrChangesCurrent(t *testing.T) {
	log := mockDocker(t)
	t.Setenv("SMAUG_DOCKER_FAIL", "pg_dump")
	m, next := managerFixture(t)
	if err := m.transition(next); err == nil {
		t.Fatal("expected failure")
	}
	commands, _ := os.ReadFile(log)
	if strings.Contains(string(commands), "migrate upgrade head") {
		t.Fatal("migration after failed backup")
	}
	var current State
	readJSON(m.file("current.json"), &current)
	if current != m.State {
		t.Fatal("current changed")
	}
}
func TestMigrationFailureRetainsOriginalBackupForRetry(t *testing.T) {
	mockDocker(t)
	t.Setenv("SMAUG_DOCKER_FAIL", "migrate upgrade head")
	m, next := managerFixture(t)
	if err := m.transition(next); err == nil {
		t.Fatal("expected failure")
	}
	var pending Pending
	if err := readJSON(m.file("pending.json"), &pending); err != nil {
		t.Fatal(err)
	}
	if pending.Backup == "" || pending.Target != next {
		t.Fatal("missing recovery state")
	}
	t.Setenv("SMAUG_DOCKER_FAIL", "")
	if err := m.transition(next); err != nil {
		t.Fatal(err)
	}
	entries, _ := os.ReadDir(m.file("backups"))
	if len(entries) != 1 {
		t.Fatal("retry replaced original backup")
	}
}
func TestUnhealthyReleaseDoesNotCommit(t *testing.T) {
	mockDocker(t)
	t.Setenv("SMAUG_DOCKER_FAIL", "--wait-timeout")
	m, next := managerFixture(t)
	if err := m.transition(next); err == nil {
		t.Fatal("expected failure")
	}
	var state State
	readJSON(m.file("current.json"), &state)
	if state != m.State || !exists(m.file("pending.json")) {
		t.Fatal("unhealthy release was committed")
	}
}
func TestActiveCLIBlocksServiceChanges(t *testing.T) {
	log := mockDocker(t)
	t.Setenv("SMAUG_ACTIVE_CLI", "1")
	m, next := managerFixture(t)
	if err := m.transition(next); err == nil {
		t.Fatal("expected active CLI rejection")
	}
	b, _ := os.ReadFile(log)
	if strings.Contains(string(b), "stop api") {
		t.Fatal("stopped an active CLI")
	}
}
func TestDatabaseImageReplacementIsExplicitlyBlocked(t *testing.T) {
	mockDocker(t)
	m, next := managerFixture(t)
	v, _ := m.manifest(next)
	v.Images["mongo"] = "example/mongo@sha256:" + strings.Repeat("c", 64)
	writeJSON(m.file("releases", next.Version, "manifest.json"), v)
	if err := m.transition(next); err == nil {
		t.Fatal("database replaced implicitly")
	}
}
func TestStageRejectsTamperingAndImmutableReplacement(t *testing.T) {
	m, _ := managerFixture(t)
	source := fixtureBundle(t, t.TempDir(), m.State.Version, strings.Repeat("a", 40), "schema-a")
	os.WriteFile(filepath.Join(source, "compose.yaml"), []byte("tampered"), 0600)
	if _, err := m.stage(source); err == nil {
		t.Fatal("tampered bundle accepted")
	}
	source = fixtureBundle(t, t.TempDir(), m.State.Version, strings.Repeat("a", 40), "changed-schema")
	if _, err := m.stage(source); err == nil {
		t.Fatal("immutable release replaced")
	}
}
func TestZipRejectsTraversalAndDuplicates(t *testing.T) {
	for _, names := range [][]string{{"../escape"}, {"C:/escape"}, {"safe", "safe"}} {
		var b bytes.Buffer
		w := zip.NewWriter(&b)
		for _, name := range names {
			f, _ := w.Create(name)
			f.Write([]byte("bad"))
		}
		w.Close()
		if err := unpackZip(b.Bytes(), t.TempDir()); err == nil {
			t.Fatalf("accepted unsafe archive: %v", names)
		}
	}
}
func TestRestoreRejectsDamagedAndForeignBackupsBeforeStopping(t *testing.T) {
	log := mockDocker(t)
	m, _ := managerFixture(t)
	path := t.TempDir()
	info := map[string]any{"project": "another-installation", "files": map[string]string{}, "state": m.State}
	writeJSON(filepath.Join(path, "backup.json"), info)
	if err := m.restore(path); err == nil {
		t.Fatal("foreign backup accepted")
	}
	info["project"] = m.Config.Project
	info["files"] = map[string]string{"postgres.dump": "incorrect"}
	writeJSON(filepath.Join(path, "backup.json"), info)
	if err := m.restore(path); err == nil {
		t.Fatal("damaged backup accepted")
	}
	b, _ := os.ReadFile(log)
	if strings.Contains(string(b), "stop api") {
		t.Fatal("services stopped for invalid backup")
	}
}
func TestValidateRejectsMutableImagesAndInvalidState(t *testing.T) {
	m, _ := managerFixture(t)
	v, _ := m.manifest(m.State)
	v.Images["backend"] = "backend:latest"
	if err := validateManifest(v); err == nil {
		t.Fatal("mutable image accepted")
	}
	c := m.Config
	c.Project = "smaug-mongo"
	if err := validateConfig(c); err == nil {
		t.Fatal("external project accepted")
	}
	if _, err := json.Marshal(m.State); err != nil {
		t.Fatal(err)
	}
}
