package main

import (
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"syscall"
)

func newClientCommand(executable string, args ...string) (*exec.Cmd, error) {
	if !strings.HasSuffix(strings.ToLower(executable), ".cmd") && !strings.HasSuffix(strings.ToLower(executable), ".bat") {
		return backgroundCommand(executable, args...), nil
	}
	// npm installs Codex as a batch shim. cmd.exe needs its own quoting,
	// rather than the default CommandLineToArgvW escaping used by os/exec.
	resolved, err := filepath.Abs(executable)
	if err != nil {
		return nil, err
	}
	tokens := append([]string{resolved}, args...)
	for i, value := range tokens {
		if strings.ContainsAny(value, "\"%!\r\n") {
			return nil, errors.New("batch CLI arguments contain expansion characters; use mcp config --client codex instead")
		}
		tokens[i] = "\"" + value + "\""
	}
	command := exec.Command("cmd.exe")
	// cmd.exe prints a warning to stdout when inheriting a WSL UNC directory.
	// These commands configure global clients, so use a local working directory.
	command.Dir = os.TempDir()
	command.SysProcAttr = &syscall.SysProcAttr{CmdLine: "cmd.exe /d /s /c \"" + strings.Join(tokens, " ") + "\""}
	hideWindow(command)
	return command, nil
}
