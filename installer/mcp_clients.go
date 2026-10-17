package main

import (
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"maps"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"runtime"
	"slices"
)

var findProgram = exec.LookPath
var clientCommand = newClientCommand

type MCPLaunch struct {
	Command string            `json:"command"`
	Args    []string          `json:"args"`
	Env     map[string]string `json:"env"`
}

func (m *Manager) mcpLaunch() (MCPLaunch, error) {
	dockerPath, err := findProgram("docker")
	if err != nil {
		return MCPLaunch{}, fmt.Errorf("install Docker before registering MCP: %w", err)
	}
	dockerPath, err = filepath.Abs(dockerPath)
	if err != nil {
		return MCPLaunch{}, err
	}
	spec := MCPLaunch{Command: m.file("smaugctl"), Args: []string{"mcp", "run"}, Env: map[string]string{"SMAUG_MCP_DOCKER": dockerPath}}
	if runtime.GOOS == "windows" {
		spec.Command = m.file("smaugctl.exe")
	}
	return spec, nil
}

func (m *Manager) configureMCP(operation string, args []string) error {
	flags := flag.NewFlagSet("mcp "+operation, flag.ContinueOnError)
	client := flags.String("client", "", "codex, claude or both")
	name := flags.String("name", "smaug", "MCP server name")
	config := flags.String("config", "", "explicit Claude Desktop JSON config path")
	replace := flags.Bool("replace", false, "replace an existing registration after backup")
	if err := flags.Parse(args); err != nil {
		return err
	}
	if flags.NArg() != 0 || (*client != "codex" && *client != "claude" && *client != "both") || !regexp.MustCompile(`^[A-Za-z0-9_-]+$`).MatchString(*name) {
		return errors.New("usage: mcp config|register --client codex|claude|both [--name NAME] [--replace] [--config CLAUDE_JSON]")
	}
	if *config != "" && *client == "codex" {
		return errors.New("--config is only for Claude Desktop; Codex uses CODEX_HOME")
	}
	spec, err := m.mcpLaunch()
	if err != nil {
		return err
	}
	if operation == "config" {
		if *client == "both" {
			return errors.New("select one client when printing configuration")
		}
		if *client == "claude" {
			return json.NewEncoder(os.Stdout).Encode(map[string]any{"mcpServers": map[string]MCPLaunch{*name: spec}})
		}
		command, _ := json.Marshal(spec.Command)
		arguments, _ := json.Marshal(spec.Args)
		dockerPath, _ := json.Marshal(spec.Env["SMAUG_MCP_DOCKER"])
		fmt.Printf("[mcp_servers.%s]\ncommand = %s\nargs = %s\n\n[mcp_servers.%s.env]\nSMAUG_MCP_DOCKER = %s\n", *name, command, arguments, *name, dockerPath)
		return nil
	}
	claudePath := *config
	if *client != "codex" && claudePath == "" {
		claudePath, err = defaultClaudeConfig()
		if err != nil {
			return err
		}
	}
	if *client != "claude" {
		if err = registerCodex(*name, spec, *replace); err != nil {
			return err
		}
	}
	if *client != "codex" {
		if err = registerClaude(claudePath, *name, spec, *replace); err != nil {
			return err
		}
	}
	fmt.Println("MCP registered. Restart the client to reconnect; Docker must be running.")
	return nil
}

func defaultClaudeConfig() (string, error) {
	if runtime.GOOS == "linux" {
		return "", errors.New("Claude Desktop has no standard Linux config path; pass --config PATH or register from Windows/macOS")
	}
	base, err := os.UserConfigDir()
	if err != nil {
		return "", err
	}
	return filepath.Join(base, "Claude", "claude_desktop_config.json"), nil
}

func backupClientConfig(path string) error {
	data, err := os.ReadFile(path)
	if errors.Is(err, os.ErrNotExist) {
		return nil
	}
	if err != nil {
		return err
	}
	file, err := os.CreateTemp(filepath.Dir(path), filepath.Base(path)+".smaug-backup-*")
	if err != nil {
		return err
	}
	name := file.Name()
	if _, err = file.Write(data); err != nil {
		file.Close()
		os.Remove(name)
		return err
	}
	if err = file.Close(); err != nil {
		os.Remove(name)
		return err
	}
	fmt.Println("Client configuration backup:", name)
	return nil
}

func sameLaunch(raw json.RawMessage, desired MCPLaunch) bool {
	var current MCPLaunch
	return json.Unmarshal(raw, &current) == nil && current.Command == desired.Command && slices.Equal(current.Args, desired.Args) && maps.Equal(current.Env, desired.Env)
}

func registerClaude(path, name string, spec MCPLaunch, replace bool) error {
	data, err := os.ReadFile(path)
	root := map[string]json.RawMessage{}
	if err == nil {
		if err = json.Unmarshal(data, &root); err != nil || root == nil {
			return errors.New("invalid Claude Desktop JSON; original configuration preserved")
		}
	} else if !errors.Is(err, os.ErrNotExist) {
		return err
	}
	servers := map[string]json.RawMessage{}
	if raw, ok := root["mcpServers"]; ok {
		if err = json.Unmarshal(raw, &servers); err != nil || servers == nil {
			return errors.New("invalid Claude mcpServers object; original configuration preserved")
		}
	}
	if existing, ok := servers[name]; ok {
		if sameLaunch(existing, spec) {
			return nil
		}
		if !replace {
			return fmt.Errorf("Claude MCP %q already exists; choose --name or use --replace after reviewing mcp config", name)
		}
	}
	if err = os.MkdirAll(filepath.Dir(path), 0700); err != nil {
		return err
	}
	if err = backupClientConfig(path); err != nil {
		return err
	}
	servers[name], _ = json.Marshal(spec)
	root["mcpServers"], _ = json.Marshal(servers)
	return writeJSON(path, root)
}

func registerCodex(name string, spec MCPLaunch, replace bool) error {
	executable, err := findProgram("codex")
	if err != nil {
		return fmt.Errorf("Codex CLI not found; install it or use mcp config --client codex: %w", err)
	}
	command, err := clientCommand(executable, "mcp", "list", "--json")
	if err != nil {
		return err
	}
	command.Stderr = os.Stderr
	data, err := command.Output()
	if err != nil {
		return fmt.Errorf("cannot read Codex MCP config; no changes made: %w", err)
	}
	var servers []struct {
		Name      string          `json:"name"`
		Transport json.RawMessage `json:"transport"`
	}
	if err = json.Unmarshal(data, &servers); err != nil {
		return err
	}
	for _, server := range servers {
		if server.Name == name {
			if sameLaunch(server.Transport, spec) {
				return nil
			}
			if !replace {
				return fmt.Errorf("Codex MCP %q already exists; choose --name or use --replace after reviewing mcp config", name)
			}
		}
	}
	base := os.Getenv("CODEX_HOME")
	if base == "" {
		home, err := os.UserHomeDir()
		if err != nil {
			return err
		}
		base = filepath.Join(home, ".codex")
	}
	if err = backupClientConfig(filepath.Join(base, "config.toml")); err != nil {
		return err
	}
	arguments := []string{"mcp", "add", name, "--env", "SMAUG_MCP_DOCKER=" + spec.Env["SMAUG_MCP_DOCKER"], "--", spec.Command}
	arguments = append(arguments, spec.Args...)
	command, err = clientCommand(executable, arguments...)
	if err != nil {
		return err
	}
	command.Stdout, command.Stderr = os.Stdout, os.Stderr
	if err = command.Run(); err != nil {
		return fmt.Errorf("Codex registration failed; restore the printed backup if necessary: %w", err)
	}
	return nil
}
