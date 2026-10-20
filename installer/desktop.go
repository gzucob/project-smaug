package main

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"strings"
)

func desktopBinaryName() string { return "smaug-desktop-windows-" + runtime.GOARCH + ".exe" }

// Shortcuts launch the version selector, so upgrades never pin the desktop to
// a stale manager. Other applications' shortcuts are never overwritten.
func (m *Manager) installDesktopShortcut(home string) error {
	switch runtime.GOOS {
	case "windows":
		target := m.file("smaug-desktop.exe")
		if !exists(target) {
			source := m.file("releases", m.State.Version, desktopBinaryName())
			if err := copyFile(source, target); err != nil {
				return err
			}
		}
		script := `$ErrorActionPreference='Stop'; $dir=Join-Path ([Environment]::GetFolderPath('Programs')) 'Smaug'; New-Item -ItemType Directory -Force -Path $dir | Out-Null; $path=Join-Path $dir 'Smaug.lnk'; $w=New-Object -ComObject WScript.Shell; $s=$w.CreateShortcut($path); if ((Test-Path -LiteralPath $path) -and $s.Description -ne 'Managed by the Smaug installer') { throw 'Another application owns the Smaug shortcut' }; $s.TargetPath=$env:SMAUG_DESKTOP_TARGET; $s.Arguments='gui'; $s.Description='Managed by the Smaug installer'; $s.Save()`
		cmd := backgroundCommand("powershell.exe", "-NoProfile", "-Command", script)
		cmd.Env = append(os.Environ(), "SMAUG_DESKTOP_TARGET="+target)
		if out, err := cmd.CombinedOutput(); err != nil {
			return fmt.Errorf("create Start menu shortcut: %s: %w", out, err)
		}
	case "darwin":
		app := filepath.Join(home, "Applications", "Smaug.app")
		marker := filepath.Join(app, "Contents", ".smaug-managed")
		if exists(app) && !exists(marker) {
			return errors.New("another application owns ~/Applications/Smaug.app")
		}
		bin := filepath.Join(app, "Contents", "MacOS")
		if err := os.MkdirAll(bin, 0700); err != nil {
			return err
		}
		plist := `<?xml version="1.0" encoding="UTF-8"?><plist version="1.0"><dict><key>CFBundleName</key><string>Smaug</string><key>CFBundleIdentifier</key><string>io.smaug.desktop</string><key>CFBundleExecutable</key><string>Smaug</string><key>CFBundlePackageType</key><string>APPL</string><key>LSUIElement</key><true/></dict></plist>`
		if err := os.WriteFile(filepath.Join(app, "Contents", "Info.plist"), []byte(plist), 0600); err != nil {
			return err
		}
		if err := os.WriteFile(filepath.Join(bin, "Smaug"), []byte("#!/bin/sh\nexec "+shellQuote(m.file("smaugctl"))+" gui\n"), 0700); err != nil {
			return err
		}
		return os.WriteFile(marker, []byte("Smaug\n"), 0600)
	default:
		data := os.Getenv("XDG_DATA_HOME")
		if data == "" {
			data = filepath.Join(home, ".local", "share")
		}
		applications := filepath.Join(data, "applications")
		if err := os.MkdirAll(applications, 0700); err != nil {
			return err
		}
		path := filepath.Join(applications, "smaug.desktop")
		if old, err := os.ReadFile(path); err == nil && !strings.Contains(string(old), "X-Smaug-Managed=true") {
			return errors.New("another application owns the Smaug desktop shortcut")
		}
		icon := m.file("smaug.svg")
		content, _ := guiFiles.ReadFile("ui/icon.svg")
		if err := os.WriteFile(icon, content, 0600); err != nil {
			return err
		}
		command, err := desktopQuote(m.file("smaugctl"))
		if err != nil {
			return err
		}
		iconValue := strings.ReplaceAll(icon, "\\", "\\\\")
		if strings.ContainsAny(iconValue, "\r\n") {
			return errors.New("invalid desktop icon path")
		}
		entry := "[Desktop Entry]\nType=Application\nName=Smaug\nComment=Instalar e gerenciar o Smaug\nExec=" + command + " gui\nIcon=" + iconValue + "\nTerminal=false\nCategories=Office;Finance;\nX-Smaug-Managed=true\n"
		return os.WriteFile(path, []byte(entry), 0600)
	}
	return nil
}
func desktopQuote(value string) (string, error) {
	if strings.ContainsAny(value, "\r\n") {
		return "", errors.New("invalid desktop command path")
	}
	value = strings.ReplaceAll(value, "%", "%%")
	value = strings.NewReplacer("\\", "\\\\\\\\", "\"", "\\\\\"", "`", "\\\\`", "$", "\\\\$").Replace(value)
	return "\"" + value + "\"", nil
}
