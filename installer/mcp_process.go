//go:build !windows

package main

import "os/exec"

func newClientCommand(executable string, args ...string) (*exec.Cmd, error) {
	return exec.Command(executable, args...), nil
}
