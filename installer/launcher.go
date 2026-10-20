package main

import (
	"errors"
	"os"
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
	desktop := strings.EqualFold(filepath.Base(executable), "smaug-desktop.exe")
	if !desktop && !strings.EqualFold(filepath.Base(executable), "smaugctl.exe") && !strings.EqualFold(filepath.Base(executable), "smaug.exe") {
		return false, nil
	}
	root := filepath.Dir(executable)
	if !exists(filepath.Join(root, "current.json")) {
		return false, nil
	}
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
	name := binaryName()
	if desktop {
		name = desktopBinaryName()
	}
	command := backgroundCommand(filepath.Join(root, "releases", state.Version, name), append([]string{"--dir", root}, args...)...)
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
