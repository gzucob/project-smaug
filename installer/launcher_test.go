package main

import (
	"bytes"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

func TestWindowsNativeBootstrapPreservesStdioAndFollowsCurrentVersion(t *testing.T) {
	if runtime.GOOS != "windows" {
		t.Skip("Windows native bootstrap")
	}
	directory := t.TempDir()
	managerBinary := filepath.Join(directory, "manager.exe")
	if data, err := exec.Command("go", "build", "-o", managerBinary, ".").CombinedOutput(); err != nil {
		t.Fatalf("build manager: %s %v", data, err)
	}
	m := &Manager{Root: filepath.Join(directory, "installation with spaces")}
	first := "2026.10.17-" + strings.Repeat("a", 12)
	next := "2026.10.17-" + strings.Repeat("b", 12)
	os.MkdirAll(m.file("releases", first), 0700)
	os.MkdirAll(m.file("releases", next), 0700)
	if err := copyFile(managerBinary, m.file("releases", first, binaryName())); err != nil {
		t.Fatal(err)
	}
	writeJSON(m.file("current.json"), State{first, "mcp"})
	if err := m.launchers(); err != nil {
		t.Fatal(err)
	}
	original, _ := hashFile(m.file("smaugctl.exe"))
	targetSource := filepath.Join(directory, "echo.go")
	os.WriteFile(targetSource, []byte("package main\nimport (\"io\";\"os\")\nfunc main(){if len(os.Args)<3 || os.Args[1]!=\"--dir\" {os.Exit(7)};io.Copy(os.Stdout,os.Stdin)}\n"), 0600)
	if data, err := exec.Command("go", "build", "-o", m.file("releases", next, binaryName()), targetSource).CombinedOutput(); err != nil {
		t.Fatalf("build target: %s %v", data, err)
	}
	writeJSON(m.file("current.json"), State{next, "mcp"})
	if err := m.launchers(); err != nil {
		t.Fatal(err)
	}
	after, _ := hashFile(m.file("smaugctl.exe"))
	if original != after {
		t.Fatal("update replaced native bootstrap")
	}
	request := []byte("{\"jsonrpc\":\"2.0\",\"texto\":\"ação\",\"value\":\"20.1234567890123456789\"}\n")
	command := exec.Command(m.file("smaugctl.exe"), "mcp", "run")
	command.Stdin = bytes.NewReader(request)
	response, err := command.Output()
	if err != nil || !bytes.Equal(request, response) {
		t.Fatalf("native stdio changed bytes: %q %v", response, err)
	}
}
