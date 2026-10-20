package main

import (
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
)

func backgroundCommand(name string, args ...string) *exec.Cmd {
	cmd := exec.Command(name, args...)
	hideWindow(cmd)
	return cmd
}
func startDetached(cmd *exec.Cmd) error {
	hideWindow(cmd)
	if err := cmd.Start(); err != nil {
		return err
	}
	go cmd.Wait()
	return nil
}
func openBrowser(url string) error {
	switch runtime.GOOS {
	case "windows":
		return startDetached(backgroundCommand("rundll32.exe", "url.dll,FileProtocolHandler", url))
	case "darwin":
		return startDetached(backgroundCommand("/usr/bin/open", url))
	default:
		return startDetached(backgroundCommand("xdg-open", url))
	}
}
func launchDockerDesktop() error {
	switch runtime.GOOS {
	case "windows":
		for _, path := range []string{filepath.Join(os.Getenv("LOCALAPPDATA"), "Programs", "DockerDesktop", "Docker Desktop.exe"), filepath.Join(os.Getenv("ProgramFiles"), "Docker", "Docker", "Docker Desktop.exe")} {
			if exists(path) {
				return startDetached(backgroundCommand(path))
			}
		}
		return os.ErrNotExist
	case "darwin":
		return startDetached(backgroundCommand("/usr/bin/open", "-a", "Docker"))
	default:
		return startDetached(backgroundCommand("systemctl", "--user", "start", "docker-desktop"))
	}
}
