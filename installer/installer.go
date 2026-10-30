package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"time"
)

// Set in direct release executables. Development builds use the latest release.
var releaseVersion string
var defaultEdition = "full"
var releaseAPI = "https://api.github.com/repos/" + repository + "/releases/latest"
var userHomeDir = os.UserHomeDir

func (m *Manager) acquireLock() error {
	lock := m.file("operation.lock")
	if err := os.Mkdir(lock, 0700); err != nil {
		var owner struct {
			PID int `json:"pid"`
		}
		if readJSON(filepath.Join(lock, "owner.json"), &owner) != nil || owner.PID < 1 || processAlive(owner.PID) {
			return errors.New("another Smaug operation is active; wait for it to finish")
		}
		if m.Config.Project == "" && exists(m.file("config.json")) {
			if err := readJSON(m.file("config.json"), &m.Config); err != nil {
				return err
			}
			if err := validateConfig(m.Config); err != nil {
				return err
			}
		}
		if err := m.noCLI(); err != nil {
			return err
		}
		// Claim the abandoned directory by rename. Only one concurrent retry
		// can win, so two installers cannot both recover the same lock.
		abandoned := lock + ".abandoned"
		if err := os.Rename(lock, abandoned); err != nil {
			return err
		}
		defer os.RemoveAll(abandoned)
		if err := os.Mkdir(lock, 0700); err != nil {
			return err
		}
	}
	return writeJSON(filepath.Join(lock, "owner.json"), struct {
		PID int `json:"pid"`
	}{os.Getpid()})
}

func bootstrapRelease() string {
	if releaseVersion != "" {
		return "tags/" + releaseVersion
	}
	return "latest"
}

func (m *Manager) recoverInitialInstall(source, edition string) (Manifest, error) {
	var pending Pending
	var release Manifest
	if err := readJSON(m.file("pending.json"), &pending); err != nil {
		return release, err
	}
	if pending.Previous.Version != "" {
		return release, errors.New("recover the pending upgrade before selecting another release")
	}
	if !validEdition(edition) {
		return release, errors.New("edition must be backend or full")
	}
	var err error
	if source != "" {
		release, err = m.stage(source)
	} else {
		release, err = m.downloadLatest(edition, pending.Target.Version)
	}
	if err != nil {
		return release, err
	}
	// There is no previously working installation to roll back to. A verified
	// replacement can resume migrations and preparation on the existing volumes.
	pending.Target = State{release.Version, edition}
	if err := writeJSON(m.file("pending.json"), pending); err != nil {
		return release, err
	}
	return release, m.update()
}

func (m *Manager) prepare(s State) error {
	var prepared State
	if err := readJSON(m.file("prepared.json"), &prepared); err == nil && prepared.Version == s.Version && !exists(m.file("preparing.json")) {
		return nil
	}
	if err := m.noCLI(); err != nil {
		return err
	}
	if err := writeJSON(m.file("preparing.json"), s); err != nil {
		return err
	}
	if err := m.compose(s, nil, nil, "stop", "api", "frontend"); err != nil {
		return err
	}
	if err := m.compose(s, nil, nil, "up", "-d", "--wait", "mongo", "postgres"); err != nil {
		return err
	}
	fmt.Println("Preparando todos os dados CVM/B3. Esta etapa pode demorar; execute smaug installer novamente para retomar se houver interrupção.")
	if err := m.compose(s, nil, nil, "run", "--rm", "--no-deps", "-T", "--label", "io.smaug.operation=cli", "cli", "prepare", "--state-file", "/data/cache/preparation.json"); err != nil {
		return fmt.Errorf("preparação incompleta; execute smaug installer para retomar: %w", err)
	}
	if err := writeJSON(m.file("prepared.json"), s); err != nil {
		return err
	}
	return os.Remove(m.file("preparing.json"))
}

func (m *Manager) printAccess() {
	fmt.Println("Smaug instalado. Dados preparados.")
	if m.Config.Edition == "full" {
		fmt.Printf("Aplicação: http://localhost:%d\n", m.Config.WebPort)
	}
	fmt.Printf("API: http://localhost:%d/docs\n", m.Config.APIPort)
	fmt.Println("Atualizar: smaug upgrade\nConectar MCP: smaug mcp register --client codex|claude|both")
}

// Only interactive management commands call this; MCP stdout remains JSON-RPC.
// Offline checks never prevent using an installed version.
func (m *Manager) notifyUpgrade() {
	type notice struct {
		Checked time.Time `json:"checked"`
		Version string    `json:"version"`
	}
	var cached notice
	_ = readJSON(m.file("upgrade-check.json"), &cached)
	if time.Since(cached.Checked) > 24*time.Hour {
		client := http.Client{Timeout: 2 * time.Second}
		response, err := client.Get(releaseAPI)
		if err != nil {
			return
		}
		defer response.Body.Close()
		if response.StatusCode != http.StatusOK {
			return
		}
		var release struct {
			Version string `json:"tag_name"`
		}
		if json.NewDecoder(response.Body).Decode(&release) != nil || !releasePattern.MatchString(release.Version) {
			return
		}
		cached = notice{time.Now(), release.Version}
		_ = writeJSON(m.file("upgrade-check.json"), cached)
	}
	if cached.Version != "" && cached.Version != m.State.Version {
		fmt.Fprintln(os.Stderr, "Atualização disponível:", cached.Version, "— execute smaug upgrade.")
	}
}

func shellQuote(value string) string { return "'" + strings.ReplaceAll(value, "'", "'\"'\"'") + "'" }

func (m *Manager) installCommand() error {
	home, err := userHomeDir()
	if err != nil {
		return err
	}
	if runtime.GOOS == "windows" {
		// The native bootstrap dispatches through current.json and can stay
		// locked by an MCP session across upgrades.
		if !exists(m.file("smaug.exe")) {
			if err := copyFile(m.file("smaugctl.exe"), m.file("smaug.exe")); err != nil {
				return err
			}
		}
		script := `$p=[Environment]::GetEnvironmentVariable('Path','User'); $d=$env:SMAUG_INSTALL_DIR; if (($p -split ';') -notcontains $d) { [Environment]::SetEnvironmentVariable('Path',($p.TrimEnd(';')+';'+$d),'User') }`
		cmd, err := clientCommand("powershell.exe", "-NoProfile", "-Command", script)
		if err != nil {
			return err
		}
		cmd.Env = append(os.Environ(), "SMAUG_INSTALL_DIR="+m.Root)
		if b, err := cmd.CombinedOutput(); err != nil {
			return fmt.Errorf("register command: %s: %w", b, err)
		}
		fmt.Println("Comando smaug registrado. Abra um novo terminal para utilizá-lo.")
		return m.installDesktopShortcut(home)
	}
	bin := filepath.Join(home, ".local", "bin")
	if err := os.MkdirAll(bin, 0700); err != nil {
		return err
	}
	path := filepath.Join(bin, "smaug")
	launcher := "#!/bin/sh\n# Managed by the Smaug installer\nexec " + shellQuote(m.file("smaugctl")) + " \"$@\"\n"
	if old, err := os.ReadFile(path); err == nil && !strings.Contains(string(old), "# Managed by the Smaug installer") {
		return errors.New("~/.local/bin/smaug already exists and belongs to another program; choose another command location before retrying")
	}
	if err := os.WriteFile(path, []byte(launcher), 0700); err != nil {
		return err
	}
	// Register for the user's next login and interactive shell, without asking
	// them to define shell functions or modify PATH by hand.
	profiles := []string{".profile", ".bashrc"}
	if strings.HasSuffix(os.Getenv("SHELL"), "zsh") || runtime.GOOS == "darwin" {
		profiles = []string{".zprofile", ".zshrc"}
	}
	line := "\n# Smaug command\nexport PATH=\"$HOME/.local/bin:$PATH\"\n"
	for _, name := range profiles {
		path := filepath.Join(home, name)
		content, err := os.ReadFile(path)
		if err != nil && !os.IsNotExist(err) {
			return err
		}
		if strings.Contains(string(content), "# Smaug command") {
			continue
		}
		file, err := os.OpenFile(path, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0600)
		if err != nil {
			return err
		}
		_, err = file.WriteString(line)
		closeErr := file.Close()
		if err != nil {
			return err
		}
		if closeErr != nil {
			return closeErr
		}
	}
	fmt.Printf("Comando registrado: %s\nAbra um novo terminal para utilizar smaug.\n", path)
	return m.installDesktopShortcut(home)
}
