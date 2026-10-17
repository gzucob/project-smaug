package main

import (
	"errors"
	"os/exec"
	"strings"
	"syscall"
)

func newClientCommand(executable string, args ...string) (*exec.Cmd, error) {
	if !strings.HasSuffix(strings.ToLower(executable), ".cmd") && !strings.HasSuffix(strings.ToLower(executable), ".bat") {
		return exec.Command(executable, args...), nil
	}
	// npm installs Codex as a batch shim. cmd.exe needs its own quoting,
	// rather than the default CommandLineToArgvW escaping used by os/exec.
	tokens := append([]string{executable}, args...)
	for i, value := range tokens {
		if strings.ContainsAny(value, "\"%!\r\n") {
			return nil, errors.New("batch CLI arguments contain expansion characters; use mcp config --client codex instead")
		}
		tokens[i] = "\"" + value + "\""
	}
	command := exec.Command("cmd.exe")
	command.SysProcAttr = &syscall.SysProcAttr{CmdLine: "cmd.exe /d /s /c \"" + strings.Join(tokens, " ") + "\""}
	return command, nil
}
