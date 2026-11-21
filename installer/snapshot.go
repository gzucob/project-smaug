package main

import (
	"archive/zip"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"runtime"
	"strconv"
	"strings"
)

const (
	embeddedFooter   = "SMAUG-PREPARED-1"
	snapshotManifest = "snapshot-manifest.json"
	mongoPayload     = "mongo.archive.xz"
	postgresPayload  = "postgres.dump"
	maxNativeSize    = 2 << 30
)

var embeddedRequired string
var sha256Pattern = regexp.MustCompile(`^[a-f0-9]{64}$`)

type SnapshotContract struct {
	ManifestSHA256 string `json:"manifest_sha256"`
	DataRevision   string `json:"data_revision"`
	CodeSHA256     string `json:"code_sha256"`
	Calculation    string `json:"calculation_contract"`
	SourceParser   string `json:"source_parser"`
	MongoMajor     int    `json:"mongo_major"`
	PostgresMajor  int    `json:"postgres_major"`
	MongoImage     string `json:"mongo_image"`
	PostgresImage  string `json:"postgres_image"`
}

type snapshotPayload struct {
	Name   string `json:"name"`
	Size   int64  `json:"size_bytes"`
	SHA256 string `json:"sha256"`
}

type snapshotMetadata struct {
	Format        int    `json:"format_version"`
	Revision      string `json:"data_revision"`
	Compatibility struct {
		SnapshotContract
		Schema string `json:"alembic_revision"`
	} `json:"compatibility"`
	Validation struct {
		Verified       bool              `json:"restore_verified"`
		Equivalent     bool              `json:"restored_offline_recomputation_equivalent"`
		Doctor         *int              `json:"doctor_exit_status"`
		RestoredDoctor *int              `json:"restored_offline_doctor_exit_status"`
		DatabaseImages map[string]string `json:"database_images"`
	} `json:"validation"`
	Coverage map[string]int64  `json:"coverage"`
	Payloads []snapshotPayload `json:"payloads"`
}

type InitialImport struct {
	Config   Config `json:"config"`
	Revision string `json:"revision"`
	Started  bool   `json:"started"`
}

type preparedSnapshot struct {
	State
	Project  string `json:"project,omitempty"`
	Revision string `json:"revision,omitempty"`
}

var mongoSnapshotCollections = []string{"ingestion_validations", "raw_ingestions", "source_input_archives", "source_input_rows"}
var postgresSnapshotTables = []string{"analysis_outcomes", "price_histories", "ticker_analysis"}

func snapshotImage(image, name string) bool {
	return strings.HasPrefix(image, name+"@sha256:") && digestPattern.MatchString(image)
}

func openEmbedded(path string) (*os.File, *zip.Reader, error) {
	file, err := os.Open(path)
	if err != nil {
		return nil, nil, err
	}
	closeOnError := func(err error) (*os.File, *zip.Reader, error) {
		file.Close()
		return nil, nil, err
	}
	info, err := file.Stat()
	if err != nil {
		return closeOnError(err)
	}
	size := info.Size() - int64(len(embeddedFooter))
	if size <= 0 || info.Size() >= maxNativeSize {
		return closeOnError(errors.New("invalid native installer size"))
	}
	footer := make([]byte, len(embeddedFooter))
	if _, err := file.ReadAt(footer, size); err != nil || string(footer) != embeddedFooter {
		return closeOnError(errors.New("missing embedded installer payload"))
	}
	reader, err := zip.NewReader(file, size)
	if err != nil {
		return closeOnError(err)
	}
	return file, reader, nil
}

func embeddedEntries(reader *zip.Reader) (map[string]*zip.File, error) {
	entries := make(map[string]*zip.File, len(reader.File))
	for _, entry := range reader.File {
		if !safePath(entry.Name) || filepath.Base(entry.Name) != entry.Name || !entry.Mode().IsRegular() || entries[entry.Name] != nil {
			return nil, fmt.Errorf("invalid embedded file: %s", entry.Name)
		}
		entries[entry.Name] = entry
	}
	return entries, nil
}

func embeddedMetadata(entry *zip.File) ([]byte, error) {
	const limit = 1 << 20
	if entry == nil || entry.UncompressedSize64 >= limit {
		return nil, errors.New("missing or oversized installer manifest")
	}
	in, err := entry.Open()
	if err != nil {
		return nil, err
	}
	defer in.Close()
	data, err := io.ReadAll(io.LimitReader(in, limit))
	if err == nil && uint64(len(data)) != entry.UncompressedSize64 {
		err = errors.New("invalid installer manifest size")
	}
	return data, err
}

func extractVerified(entry *zip.File, path, want string, size int64) error {
	if entry == nil || size <= 0 || size >= maxNativeSize || entry.UncompressedSize64 != uint64(size) {
		return fmt.Errorf("missing or invalid embedded size: %s", filepath.Base(path))
	}
	in, err := entry.Open()
	if err != nil {
		return err
	}
	defer in.Close()
	out, err := os.OpenFile(path, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
	if err != nil {
		return err
	}
	hash := sha256.New()
	n, copyErr := io.Copy(io.MultiWriter(out, hash), io.LimitReader(in, size+1))
	closeErr := out.Close()
	if err := errors.Join(copyErr, closeErr); err != nil {
		return err
	}
	if n != size || hex.EncodeToString(hash.Sum(nil)) != want {
		return fmt.Errorf("embedded checksum mismatch: %s", entry.Name)
	}
	return nil
}

func readSnapshotMetadata(data []byte, release Manifest) (snapshotMetadata, error) {
	var meta snapshotMetadata
	want := release.Snapshot
	sum := sha256.Sum256(data)
	if want == nil || hex.EncodeToString(sum[:]) != want.ManifestSHA256 {
		return meta, errors.New("snapshot manifest checksum mismatch")
	}
	if err := json.Unmarshal(data, &meta); err != nil {
		return meta, err
	}
	actual := meta.Compatibility
	if meta.Format != 1 || meta.Revision != want.DataRevision || actual.CodeSHA256 != want.CodeSHA256 ||
		actual.Schema != release.Schema || actual.Calculation != want.Calculation ||
		actual.SourceParser != want.SourceParser || actual.MongoMajor != want.MongoMajor ||
		actual.PostgresMajor != want.PostgresMajor {
		return meta, errors.New("snapshot incompatible with the application release")
	}
	status := meta.Validation
	if "mongo@"+status.DatabaseImages["mongo"] != want.MongoImage ||
		"postgres@"+status.DatabaseImages["postgres"] != want.PostgresImage {
		return meta, errors.New("snapshot database images differ from the certified restore")
	}
	if !status.Verified || !status.Equivalent || status.Doctor == nil || *status.Doctor != 0 ||
		status.RestoredDoctor == nil || *status.RestoredDoctor != 0 {
		return meta, errors.New("snapshot has not been validated")
	}
	if len(meta.Payloads) != 2 || meta.Payloads[0].Name != mongoPayload || meta.Payloads[1].Name != postgresPayload {
		return meta, errors.New("unexpected snapshot payloads")
	}
	for _, payload := range meta.Payloads {
		if payload.Size <= 0 || payload.Size >= maxNativeSize || !sha256Pattern.MatchString(payload.SHA256) {
			return meta, fmt.Errorf("invalid snapshot payload metadata: %s", payload.Name)
		}
	}
	for _, name := range append(append([]string{}, mongoSnapshotCollections...), postgresSnapshotTables...) {
		if meta.Coverage[name] <= 0 {
			return meta, fmt.Errorf("missing snapshot coverage: %s", name)
		}
	}
	return meta, nil
}

// Stage only the small runtime for an upgrade. The databases belong exclusively
// to a fresh installation, even when the same native package is run again.
func (m *Manager) stageNative(includeSnapshot bool) (Manifest, error) {
	var release Manifest
	path, err := os.Executable()
	if err != nil {
		return release, err
	}
	if runtime.GOOS == "darwin" {
		path = filepath.Join(filepath.Dir(path), "..", "Resources", "prepared.zip")
	}
	file, reader, err := openEmbedded(path)
	if err != nil {
		return release, err
	}
	defer file.Close()
	entries, err := embeddedEntries(reader)
	if err != nil {
		return release, err
	}
	data, err := embeddedMetadata(entries["manifest.json"])
	if err != nil {
		return release, err
	}
	if err := json.Unmarshal(data, &release); err != nil {
		return release, err
	}
	if err := validateManifest(release); err != nil {
		return release, err
	}
	if release.Version != releaseVersion || release.Snapshot == nil {
		return release, errors.New("embedded package does not match this installer")
	}
	temp := m.file(".native-staging")
	if err := os.RemoveAll(temp); err != nil {
		return release, err
	}
	if err := os.Mkdir(temp, 0700); err != nil {
		return release, err
	}
	defer os.RemoveAll(temp)
	if err := os.WriteFile(filepath.Join(temp, "manifest.json"), data, 0600); err != nil {
		return release, err
	}
	var runtimeSize uint64
	for name, hash := range release.Files {
		if !safePath(name) || filepath.Base(name) != name {
			return release, errors.New("unsafe embedded runtime filename")
		}
		entry := entries[name]
		if entry == nil || entry.UncompressedSize64 > 256<<20-runtimeSize {
			return release, fmt.Errorf("missing or oversized runtime file: %s", name)
		}
		runtimeSize += entry.UncompressedSize64
		if err := extractVerified(entry, filepath.Join(temp, name), hash, int64(entry.UncompressedSize64)); err != nil {
			return release, err
		}
	}
	if _, err := m.stage(temp); err != nil {
		return release, err
	}
	if !includeSnapshot {
		return release, nil
	}
	dir := m.file("snapshot-import")
	snapshot, err := embeddedMetadata(entries[snapshotManifest])
	if err != nil {
		return release, err
	}
	meta, err := readSnapshotMetadata(snapshot, release)
	if err != nil {
		return release, err
	}
	if err := os.RemoveAll(dir); err != nil {
		return release, err
	}
	dumps := filepath.Join(temp, "dumps")
	if err := os.Mkdir(dumps, 0700); err != nil {
		return release, err
	}
	fmt.Println("Verificando e extraindo a base de dados incluída no instalador.")
	for _, payload := range meta.Payloads {
		if err := extractVerified(entries[payload.Name], filepath.Join(dumps, payload.Name), payload.SHA256, payload.Size); err != nil {
			return release, err
		}
	}
	if err := os.WriteFile(filepath.Join(dumps, snapshotManifest), snapshot, 0600); err != nil {
		return release, err
	}
	return release, os.Rename(dumps, dir)
}

func (m *Manager) verifiedStagedSnapshot(release Manifest) (snapshotMetadata, error) {
	var meta snapshotMetadata
	data, err := os.ReadFile(m.file("snapshot-import", snapshotManifest))
	if err != nil {
		return meta, err
	}
	meta, err = readSnapshotMetadata(data, release)
	if err != nil {
		return meta, err
	}
	for _, payload := range meta.Payloads {
		path := m.file("snapshot-import", payload.Name)
		info, err := os.Lstat(path)
		if err != nil || !info.Mode().IsRegular() || info.Size() != payload.Size {
			return meta, fmt.Errorf("invalid staged snapshot file: %s", payload.Name)
		}
		hash, err := hashFile(path)
		if err != nil || hash != payload.SHA256 {
			return meta, fmt.Errorf("staged snapshot checksum mismatch: %s", payload.Name)
		}
	}
	return meta, nil
}

func (m *Manager) finishInitialImport(p Pending) error {
	if p.Import == nil || p.Previous.Version != "" || p.Backup != "" {
		return errors.New("invalid initial import state")
	}
	m.Config = p.Import.Config
	if err := validateConfig(m.Config); err != nil {
		return err
	}
	s := p.Target
	release, err := m.manifest(s)
	if err != nil {
		return err
	}
	if release.Snapshot == nil || p.Import.Revision != release.Snapshot.DataRevision || m.Config.Edition != s.Edition ||
		m.Config.MongoImage != release.Snapshot.MongoImage || m.Config.PostgresImage != release.Snapshot.PostgresImage {
		return errors.New("pending import does not match the installed package")
	}
	if exists(m.file("config.json")) {
		var config Config
		if err := readJSON(m.file("config.json"), &config); err != nil || config != m.Config {
			return errors.New("pending import does not match the local configuration")
		}
	}
	if exists(m.file("current.json")) {
		var current State
		if err := readJSON(m.file("current.json"), &current); err != nil || current != s {
			return errors.New("pending import does not match the local release")
		}
	}
	prepared := preparedSnapshot{State: s, Project: m.Config.Project, Revision: p.Import.Revision}
	if exists(m.file("prepared.json")) {
		var saved preparedSnapshot
		if err := readJSON(m.file("prepared.json"), &saved); err != nil || saved != prepared {
			return errors.New("existing prepared state does not belong to this import")
		}
		if err := m.compose(s, nil, nil, "up", "-d", "--wait", "--pull", "missing", "mongo", "postgres"); err != nil {
			return err
		}
		if err := m.checkPreparedSnapshot(s); err != nil {
			return err
		}
	} else {
		meta, err := m.verifiedStagedSnapshot(release)
		if err != nil && embeddedRequired != "" && releaseVersion == s.Version {
			if _, err = m.stageNative(true); err != nil {
				return err
			}
			meta, err = m.verifiedStagedSnapshot(release)
		}
		if err != nil {
			return fmt.Errorf("execute o instalador original para retomar a importação: %w", err)
		}
		if err = m.importSnapshot(p, release, meta); err != nil {
			return fmt.Errorf("importação incompleta; execute o instalador novamente para retomar: %w", err)
		}
		if err = writeJSON(m.file("prepared.json"), prepared); err != nil {
			return err
		}
	}
	if err := os.RemoveAll(m.file("snapshot-import")); err != nil {
		return err
	}
	if err := m.start(s); err != nil {
		return err
	}
	return m.commit(s)
}

func (m *Manager) importSnapshot(p Pending, release Manifest, meta snapshotMetadata) error {
	s := p.Target
	if p.Import.Started {
		// A pending fresh install owns this unpublished Compose project. No
		// service can expose the partial restore and no existing project is
		// eligible for this path.
		if err := m.compose(s, nil, nil, "down", "--volumes", "--remove-orphans"); err != nil {
			return err
		}
	}
	fmt.Println("Iniciando o Docker. Imagens ausentes serão baixadas automaticamente.")
	if err := m.compose(s, nil, nil, "up", "-d", "--wait", "--pull", "missing", "mongo", "postgres"); err != nil {
		return err
	}
	mongo, err := m.snapshotMongo(s, "print(JSON.stringify([db.version().split('.')[0], db.getCollectionNames().sort()]));")
	if err != nil {
		return err
	}
	postgres, err := m.snapshotSQL(s, "SELECT current_setting('server_version_num')::int / 10000; SELECT count(*) FROM pg_namespace WHERE nspname NOT IN ('pg_catalog','information_schema','public') AND nspname NOT LIKE 'pg_toast%'; SELECT count(*) FROM pg_tables WHERE schemaname='public';")
	if err != nil {
		return err
	}
	if mongo != fmt.Sprintf(`["%d",[]]`, release.Snapshot.MongoMajor) ||
		postgres != fmt.Sprintf("%d\n0\n0", release.Snapshot.PostgresMajor) {
		return errors.New("restore requires empty, compatible databases")
	}
	// Only retries after this point may clear partial data. An occupied
	// target rejected above never acquires the right to delete its volumes.
	p.Import.Started = true
	if err := writeJSON(m.file("pending.json"), p); err != nil {
		return err
	}
	fmt.Println("Importando MongoDB.")
	if err := m.restoreSnapshotMongo(s); err != nil {
		return err
	}
	fmt.Println("Importando PostgreSQL.")
	in, err := os.Open(m.file("snapshot-import", postgresPayload))
	if err != nil {
		return err
	}
	defer in.Close()
	if err := m.compose(s, in, nil, "exec", "-T", "postgres", "pg_restore", "--single-transaction", "--exit-on-error", "--no-owner", "--no-acl", "-U", "smaug", "-d", "smaug"); err != nil {
		return err
	}
	fmt.Println("Conferindo a importação e inicializando a instalação.")
	return m.verifyRestoredSnapshot(s, release, meta)
}

func (m *Manager) snapshotMongo(s State, js string) (string, error) {
	return m.capture(s, "exec", "-T", "mongo", "mongosh", "--quiet", "smaug", "--eval", js)
}

func (m *Manager) snapshotSQL(s State, sql string) (string, error) {
	return m.capture(s, "exec", "-T", "postgres", "psql", "-XAt", "-v", "ON_ERROR_STOP=1", "-U", "smaug", "-d", "smaug", "-c", sql)
}

func (m *Manager) restoreSnapshotMongo(s State) error {
	in, err := os.Open(m.file("snapshot-import", mongoPayload))
	if err != nil {
		return err
	}
	defer in.Close()
	reader, writer := io.Pipe()
	decode := make(chan error, 1)
	go func() {
		err := m.compose(s, in, writer, "run", "--rm", "--no-deps", "-T", "snapshot-decoder")
		writer.CloseWithError(err)
		decode <- err
	}()
	restoreErr := m.compose(s, reader, nil, "exec", "-T", "mongo", "mongorestore", "--archive", "--stopOnError", "--nsInclude=smaug.*")
	reader.Close()
	return errors.Join(restoreErr, <-decode)
}

func (m *Manager) verifyRestoredSnapshot(s State, release Manifest, meta snapshotMetadata) error {
	names, _ := json.Marshal(mongoSnapshotCollections)
	script := fmt.Sprintf(`const names = %s; if (JSON.stringify(db.getCollectionNames().sort()) !== JSON.stringify(names)) quit(1); print(JSON.stringify(names.map(n => db.getCollection(n).estimatedDocumentCount())));`, names)
	got, err := m.snapshotMongo(s, script)
	if err != nil {
		return err
	}
	var counts []int64
	for _, name := range mongoSnapshotCollections {
		counts = append(counts, meta.Coverage[name])
	}
	want, _ := json.Marshal(counts)
	if got != string(want) {
		return errors.New("restored MongoDB coverage does not match the snapshot")
	}
	sql := "SELECT version_num FROM alembic_version;"
	expected := []string{release.Schema}
	for _, name := range postgresSnapshotTables {
		sql += "SELECT count(*) FROM " + name + ";"
		expected = append(expected, strconv.FormatInt(meta.Coverage[name], 10))
	}
	sql += "SELECT count(*) FROM portfolio; SELECT count(*) FROM price_refresh_status; SELECT count(*) FROM ui_data_revision;"
	expected = append(expected, "0", "0", "0")
	got, err = m.snapshotSQL(s, sql)
	if err != nil {
		return err
	}
	if got != strings.Join(expected, "\n") {
		return errors.New("restored PostgreSQL schema or coverage does not match the snapshot")
	}
	_, err = m.snapshotSQL(s, "INSERT INTO ui_data_revision (id, generation) VALUES (1, 1)")
	return err
}

func (m *Manager) checkPreparedSnapshot(s State) error {
	var prepared preparedSnapshot
	if err := readJSON(m.file("prepared.json"), &prepared); err != nil || prepared.State != s {
		return errors.New("installation preparation is incomplete")
	}
	if prepared.Revision == "" {
		return nil // Existing legacy installations have no snapshot receipt.
	}
	if prepared.Project != m.Config.Project || !sha256Pattern.MatchString(prepared.Revision) {
		return errors.New("invalid installation snapshot receipt")
	}
	names, _ := json.Marshal(mongoSnapshotCollections)
	_, err := m.snapshotMongo(s, fmt.Sprintf(`if (!%s.every(n => db.getCollectionNames().includes(n) && db.getCollection(n).findOne({}, {_id:1}) !== null)) quit(1);`, names))
	if err != nil {
		return fmt.Errorf("imported MongoDB collections are missing or unreadable: %w", err)
	}
	revision, err := m.snapshotSQL(s, "SELECT generation FROM ui_data_revision WHERE id=1 AND generation>=1")
	if err != nil || revision == "" {
		return errors.New("installation data revision is missing or unreadable")
	}
	return nil
}
