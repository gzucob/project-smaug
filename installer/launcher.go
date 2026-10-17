package main

import (
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
)

func dispatchNativeLauncher(args []string) (bool, error) {
	if runtime.GOOS != "windows" {
		return false, nil
	}
	executable, err := os.Executable()
	if err != nil {
		return false, err
	}
	if !strings.EqualFold(filepath.Base(executable), "smaugctl.exe") {
		return false, nil
	}
	root := filepath.Dir(executable)
	if len(args) >= 2 && args[0] == "--dir" {
		root, args = args[1], args[2:]
	}
	root, err = filepath.Abs(root)
	if err != nil {
		return true, err
	}
	var state State
	if err = readJSON(filepath.Join(root, "current.json"), &state); err != nil {
		return true, err
	}
	if !releasePattern.MatchString(state.Version) {
		return true, errors.New("invalid release version")
	}
	command := exec.Command(filepath.Join(root, "releases", state.Version, binaryName()), append([]string{"--dir", root}, args...)...)
	command.Stdin, command.Stdout, command.Stderr = os.Stdin, os.Stdout, os.Stderr
	return true, command.Run()
}

func (m *Manager) nativeLauncher() error {
	if runtime.GOOS != "windows" || exists(m.file("smaugctl.exe")) {
		return nil
	}
	var state State
	if err := readJSON(m.file("current.json"), &state); err != nil {
		return err
	}
	if !releasePattern.MatchString(state.Version) {
		return errors.New("invalid release version")
	}
	temporary, err := os.CreateTemp(m.Root, ".smaugctl-*.exe")
	if err != nil {
		return err
	}
	name := temporary.Name()
	temporary.Close()
	defer os.Remove(name)
	if err := copyFile(m.file("releases", state.Version, binaryName()), name); err != nil {
		return err
	}
	// This bootstrap only dispatches to current.json. Never replace it during
	// updates: Windows keeps a connected MCP client's executable locked.
	return os.Rename(name, m.file("smaugctl.exe"))
}
