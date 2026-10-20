package main

import (
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"net"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
)

type ConnectorConfig struct {
	APIBaseURL string `json:"api_base_url"`
}

func (m *Manager) mcp(args []string) error {
	if len(args) == 0 {
		return errors.New("usage: smaug mcp setup|run|config|register|update|status")
	}
	// Desktop clients may not inherit the terminal PATH used at registration.
	if dockerPath := os.Getenv("SMAUG_MCP_DOCKER"); dockerPath != "" {
		if !filepath.IsAbs(dockerPath) {
			return errors.New("SMAUG_MCP_DOCKER must be an absolute path")
		}
		previous := dockerCommand
		dockerCommand = func(_ string, args ...string) *exec.Cmd { return previous(dockerPath, args...) }
		defer func() { dockerCommand = previous }()
	}
	if args[0] == "setup" {
		return m.setupConnector(args[1:])
	}
	standalone := exists(m.file("mcp.json"))
	if standalone && exists(m.file("config.json")) {
		return errors.New("mixed MCP/application configuration; use separate installation directories")
	}
	if err := readJSON(m.file("current.json"), &m.State); err != nil {
		return fmt.Errorf("MCP installation not found: %w", err)
	}
	if !standalone {
		if err := readJSON(m.file("config.json"), &m.Config); err != nil {
			return err
		}
		if err := validateConfig(m.Config); err != nil {
			return err
		}
	}
	switch args[0] {
	case "config", "register":
		return m.configureMCP(args[0], args[1:])
	case "run":
		if len(args) != 1 {
			return errors.New("usage: smaug mcp run")
		}
		if standalone {
			return m.runConnector()
		}
		// Sessions coexist without the writer lock, including during recovery.
		// Never start dependencies or migrations just to read help or status.
		return m.compose(m.State, os.Stdin, os.Stdout, "run", "--rm", "--no-deps", "-T", "mcp")
	case "update":
		if !standalone {
			return errors.New("use smaug upgrade for backend/full installations")
		}
		if len(args) == 3 && args[1] == "--source" {
			return m.setupConnector([]string{"--source", args[2]})
		}
		if len(args) != 1 {
			return errors.New("usage: smaug mcp update [--source BUNDLE]")
		}
		if err := os.MkdirAll(m.Root, 0700); err != nil {
			return err
		}
		unlock, err := m.connectorLock()
		if err != nil {
			return err
		}
		defer unlock()
		v, err := m.downloadLatest("mcp", m.State.Version)
		if err != nil {
			return err
		}
		if v.Version == m.State.Version {
			fmt.Println("Already up to date.")
			return nil
		}
		return m.commitConnector(v, nil)
	case "status":
		if len(args) != 1 {
			return errors.New("usage: smaug mcp status")
		}
		if !standalone {
			return errors.New("use smaug status and MCP get_service_status for backend/full")
		}
		var config ConnectorConfig
		if err := readJSON(m.file("mcp.json"), &config); err != nil {
			return err
		}
		return json.NewEncoder(os.Stdout).Encode(map[string]string{"version": m.State.Version, "api_base_url": config.APIBaseURL, "directory": m.Root})
	default:
		return fmt.Errorf("unknown MCP command %q", args[0])
	}
}

func (m *Manager) connectorLock() (func(), error) {
	lock := m.file("operation.lock")
	if err := os.Mkdir(lock, 0700); err != nil {
		return nil, fmt.Errorf("another connector update is active (or interrupted); inspect before removing %s: %w", lock, err)
	}
	return func() { os.Remove(lock) }, nil
}

func validateAPIBase(value string) error {
	parsed, err := url.Parse(value)
	if err != nil || (parsed.Scheme != "http" && parsed.Scheme != "https") || parsed.Hostname() == "" || parsed.User != nil || parsed.RawQuery != "" || parsed.Fragment != "" {
		return errors.New("api-base-url must be an HTTP(S) API URL without credentials, query or fragment")
	}
	return nil
}

func connectorNetwork(apiBase, system string) (string, []string, error) {
	if err := validateAPIBase(apiBase); err != nil {
		return "", nil, err
	}
	if system == "linux" {
		return apiBase, []string{"--network", "host"}, nil
	}
	parsed, _ := url.Parse(apiBase)
	host := parsed.Hostname()
	ip := net.ParseIP(host)
	if host == "localhost" || (ip != nil && ip.IsLoopback()) {
		port := parsed.Port()
		parsed.Host = "host.docker.internal"
		if port != "" {
			parsed.Host = net.JoinHostPort("host.docker.internal", port)
		}
	}
	return parsed.String(), nil, nil
}

func (m *Manager) connectorManifest() (Manifest, error) {
	if m.State.Edition != "mcp" || !releasePattern.MatchString(m.State.Version) {
		return Manifest{}, errors.New("invalid MCP release state")
	}
	var v Manifest
	if err := readJSON(m.file("releases", m.State.Version, "manifest.json"), &v); err != nil {
		return v, err
	}
	if err := validateManifest(v); err != nil {
		return v, err
	}
	if v.Version != m.State.Version {
		return v, errors.New("MCP release version mismatch")
	}
	return v, nil
}

func (m *Manager) runConnector() error {
	v, err := m.connectorManifest()
	if err != nil {
		return err
	}
	var config ConnectorConfig
	if err = readJSON(m.file("mcp.json"), &config); err != nil {
		return err
	}
	apiBase, network, err := connectorNetwork(config.APIBaseURL, runtime.GOOS)
	if err != nil {
		return err
	}
	args := append([]string{"run", "--rm", "-i"}, network...)
	args = append(args, "--env", "MCP_API_BASE_URL="+apiBase, "--entrypoint", "smaug-mcp", v.Images["backend"])
	cmd := dockerCommand("docker", args...)
	cmd.Stdin, cmd.Stdout, cmd.Stderr = os.Stdin, os.Stdout, os.Stderr
	return cmd.Run()
}

func (m *Manager) setupConnector(args []string) error {
	flags := flag.NewFlagSet("mcp setup", flag.ContinueOnError)
	source := flags.String("source", "", "extracted MCP bundle")
	api := flags.String("api-base-url", "", "existing API URL (default http://127.0.0.1:8000)")
	client := flags.String("client", "", "register codex, claude or both after installation")
	name := flags.String("name", "smaug", "MCP server name")
	replace := flags.Bool("replace", false, "replace an existing registration after backup")
	if err := flags.Parse(args); err != nil {
		return err
	}
	if *source == "" || flags.NArg() != 0 {
		return errors.New("usage: smaug mcp setup --source BUNDLE [--api-base-url URL] [--client codex|claude|both]")
	}
	if exists(m.file("config.json")) {
		return errors.New("backend/full already provides MCP here; use mcp register, or select a separate --dir")
	}
	edition, err := os.ReadFile(filepath.Join(*source, "edition.txt"))
	if err != nil || string(edition) != "mcp\n" {
		return errors.New("setup requires the separate MCP bundle")
	}
	if *client != "" && *client != "codex" && *client != "claude" && *client != "both" {
		return errors.New("client must be codex, claude or both")
	}
	if err = os.MkdirAll(m.Root, 0700); err != nil {
		return err
	}
	unlock, err := m.connectorLock()
	if err != nil {
		return err
	}
	defer unlock()
	var config ConnectorConfig
	if exists(m.file("mcp.json")) {
		if err = readJSON(m.file("mcp.json"), &config); err != nil {
			return err
		}
	} else {
		config.APIBaseURL = "http://127.0.0.1:8000"
	}
	if *api != "" {
		config.APIBaseURL = *api
	}
	if err = validateAPIBase(config.APIBaseURL); err != nil {
		return err
	}

	v, err := m.stage(*source)
	if err != nil {
		return err
	}
	if err = m.commitConnector(v, &config); err != nil {
		return err
	}
	if *client != "" {
		registration := []string{"--client", *client, "--name", *name}
		if *replace {
			registration = append(registration, "--replace")
		}
		if err = m.configureMCP("register", registration); err != nil {
			return fmt.Errorf("MCP installed; client registration failed (retry mcp register): %w", err)
		}
	}
	return nil
}

func (m *Manager) commitConnector(v Manifest, config *ConnectorConfig) error {
	cmd := dockerCommand("docker", "pull", v.Images["backend"])
	cmd.Stdout, cmd.Stderr = os.Stdout, os.Stderr
	if err := cmd.Run(); err != nil {
		return err
	}
	if config != nil {
		if err := writeJSON(m.file("mcp.json"), config); err != nil {
			return err
		}
	}
	m.State = State{v.Version, "mcp"}
	if err := writeJSON(m.file("current.json"), m.State); err != nil {
		return err
	}
	if err := m.launchers(); err != nil {
		return err
	}
	fmt.Printf("MCP installed: %s\nDirectory: %s\nUse smaug mcp register --client codex|claude|both, then restart the client.\n", v.Version, m.Root)
	return nil
}
