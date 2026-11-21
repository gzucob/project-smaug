package main

import (
	"archive/zip"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"io"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func snapshotFixture(t *testing.T, m *Manager, s State) Pending {
	t.Helper()
	release, err := m.manifest(s)
	if err != nil {
		t.Fatal(err)
	}
	imageHash := strings.Repeat("b", 64)
	compat := SnapshotContract{
		DataRevision: strings.Repeat("a", 64), CodeSHA256: strings.Repeat("c", 64),
		Calculation: "analysis", SourceParser: "sources", MongoMajor: 8, PostgresMajor: 17,
		MongoImage: "mongo@sha256:" + imageHash, PostgresImage: "postgres@sha256:" + imageHash,
	}
	release.Snapshot = &compat
	var meta snapshotMetadata
	meta.Format = 1
	meta.Revision = compat.DataRevision
	meta.Compatibility.SnapshotContract = compat
	meta.Compatibility.Schema = release.Schema
	meta.Validation.Verified = true
	meta.Validation.Equivalent = true
	meta.Validation.Doctor = new(int)
	meta.Validation.RestoredDoctor = new(int)
	meta.Validation.DatabaseImages = map[string]string{"mongo": "sha256:" + imageHash, "postgres": "sha256:" + imageHash}
	meta.Coverage = map[string]int64{}
	for _, name := range append(append([]string{}, mongoSnapshotCollections...), postgresSnapshotTables...) {
		meta.Coverage[name] = 1
	}
	if err := os.Mkdir(m.file("snapshot-import"), 0700); err != nil {
		t.Fatal(err)
	}
	for _, name := range []string{mongoPayload, postgresPayload} {
		data := []byte("fixture")
		if err := os.WriteFile(m.file("snapshot-import", name), data, 0600); err != nil {
			t.Fatal(err)
		}
		sum := sha256.Sum256(data)
		meta.Payloads = append(meta.Payloads, snapshotPayload{name, int64(len(data)), hex.EncodeToString(sum[:])})
	}
	if err := writeJSON(m.file("snapshot-import", snapshotManifest), meta); err != nil {
		t.Fatal(err)
	}
	compat.ManifestSHA256, err = hashFile(m.file("snapshot-import", snapshotManifest))
	if err != nil {
		t.Fatal(err)
	}
	if err := writeJSON(m.file("releases", s.Version, "manifest.json"), release); err != nil {
		t.Fatal(err)
	}
	m.Config.MongoImage, m.Config.PostgresImage = compat.MongoImage, compat.PostgresImage
	return Pending{Target: s, Import: &InitialImport{Config: m.Config, Revision: compat.DataRevision}}
}

func TestEmbeddedArchiveReadsOnlyVerifiedEntries(t *testing.T) {
	path := filepath.Join(t.TempDir(), "native")
	file, err := os.Create(path)
	if err != nil {
		t.Fatal(err)
	}
	prefix := "launcher"
	_, _ = io.WriteString(file, prefix)
	archive := zip.NewWriter(file)
	archive.SetOffset(int64(len(prefix)))
	entry, err := archive.Create(mongoPayload)
	if err != nil {
		t.Fatal(err)
	}
	const dump = "mongo fixture"
	_, _ = io.WriteString(entry, dump)
	if err := archive.Close(); err != nil {
		t.Fatal(err)
	}
	_, _ = io.WriteString(file, embeddedFooter)
	file.Close()

	in, reader, err := openEmbedded(path)
	if err != nil {
		t.Fatal(err)
	}
	defer in.Close()
	entries, err := embeddedEntries(reader)
	if err != nil {
		t.Fatal(err)
	}
	if err := extractVerified(entries[mongoPayload], filepath.Join(t.TempDir(), mongoPayload), strings.Repeat("0", 64), int64(len(dump))); err == nil {
		t.Fatal("bad embedded checksum accepted")
	}
	sum := sha256.Sum256([]byte(dump))
	if err := extractVerified(entries[mongoPayload], filepath.Join(t.TempDir(), mongoPayload), hex.EncodeToString(sum[:]), int64(len(dump))); err != nil {
		t.Fatal(err)
	}
}

func TestSnapshotCorruptionAndWrongSchemaFailBeforeDocker(t *testing.T) {
	log := mockDocker(t)
	m, _ := managerFixture(t)
	p := snapshotFixture(t, m, m.State)
	if err := writeJSON(m.file("config.json"), m.Config); err != nil {
		t.Fatal(err)
	}
	release, _ := m.manifest(p.Target)
	release.Schema = "other-version"
	data, _ := os.ReadFile(m.file("snapshot-import", snapshotManifest))
	if _, err := readSnapshotMetadata(data, release); err == nil {
		t.Fatal("incompatible schema accepted")
	}
	if err := os.WriteFile(m.file("snapshot-import", postgresPayload), []byte("corrupt"), 0600); err != nil {
		t.Fatal(err)
	}
	p.Import.Started = true
	if err := m.finishInitialImport(p); err == nil || !strings.Contains(err.Error(), "checksum mismatch") {
		t.Fatalf("want checksum error, got %v", err)
	}
	if exists(log) || exists(m.file("prepared.json")) {
		t.Fatal("corrupt payload reached Docker or was marked prepared")
	}
}

func TestInterruptedSnapshotRestartsOnlyItsFreshProject(t *testing.T) {
	log := mockDocker(t)
	t.Setenv("SMAUG_SNAPSHOT_TEST_SCHEMA", "schema-a")
	m, _ := managerFixture(t)
	p := snapshotFixture(t, m, m.State)
	if err := os.Remove(m.file("config.json")); err != nil {
		t.Fatal(err)
	}
	if err := os.Remove(m.file("current.json")); err != nil {
		t.Fatal(err)
	}
	t.Setenv("SMAUG_DOCKER_FAIL", "pg_restore")
	if err := m.finishInitialImport(p); err == nil || exists(m.file("prepared.json")) {
		t.Fatal("partial restore was accepted")
	}
	first, _ := os.ReadFile(log)
	if strings.Contains(string(first), "down --volumes") || strings.Contains(string(first), "--wait-timeout") {
		t.Fatalf("unsafe first attempt: %s", first)
	}
	if err := readJSON(m.file("pending.json"), &p); err != nil || !p.Import.Started {
		t.Fatal("missing recoverable pending import", err)
	}
	t.Setenv("SMAUG_DOCKER_FAIL", "")
	if err := m.finishInitialImport(p); err != nil {
		t.Fatal(err)
	}
	if exists(m.file("snapshot-import")) || exists(m.file("pending.json")) {
		t.Fatal("temporary dumps or pending marker survived successful import")
	}
	commands, _ := os.ReadFile(log)
	resume := string(commands[len(first):])
	if !strings.Contains(resume, "down --volumes") || !strings.Contains(resume, "run --rm --no-deps -T snapshot-decoder") ||
		strings.Contains(resume, "cli prepare") ||
		strings.Contains(resume, "migrate upgrade") || !strings.Contains(resume, "--wait-timeout") {
		t.Fatalf("incorrect resumed commands: %s", resume)
	}
	var receipt preparedSnapshot
	if err := readJSON(m.file("prepared.json"), &receipt); err != nil ||
		receipt.Project != m.Config.Project || receipt.Revision != p.Import.Revision {
		t.Fatal("missing installation-local receipt", err)
	}
	if exists(m.file("previous.json")) {
		t.Fatal("a fresh installation cannot have a previous release")
	}
}

func TestPreparedReleaseKeepsExistingDatabases(t *testing.T) {
	log := mockDocker(t)
	t.Setenv("SMAUG_SNAPSHOT_TEST_SCHEMA", "schema-b")
	m, next := managerFixture(t)
	before, _ := json.Marshal(m.Config)
	existing := m.Config
	snapshotFixture(t, m, next)
	m.Config = existing
	if err := m.transition(next); err != nil {
		t.Fatal(err)
	}
	commands, _ := os.ReadFile(log)
	for _, unwanted := range []string{"down --volumes", "mongorestore", "pg_restore", "cli prepare"} {
		if strings.Contains(string(commands), unwanted) {
			t.Fatalf("existing database received %q", unwanted)
		}
	}
	after, _ := json.Marshal(m.Config)
	if string(before) != string(after) {
		t.Fatal("upgrade replaced the existing configuration")
	}
}
