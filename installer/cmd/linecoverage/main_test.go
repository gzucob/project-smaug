package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestStatementLinesIncludeNeverExecutedFile(t *testing.T) {
	path := filepath.Join(t.TempDir(), "fixture.go")
	source := "package fixture\nfunc example() int {\n value := 2\n return value\n}\n"
	if err := os.WriteFile(path, []byte(source), 0600); err != nil {
		t.Fatal(err)
	}
	if hit, total, err := statementLines(path, nil); err != nil || hit != 0 || total != 2 {
		t.Fatalf("unmeasured lines = %d/%d, %v", hit, total, err)
	}
	profile, err := readProfile(strings.NewReader(
		"mode: count\nfixture.go:2.20,5.2 2 0\nfixture.go:2.20,5.2 2 1\n",
	))
	if err != nil {
		t.Fatal(err)
	}
	if hit, total, err := statementLines(path, profile["fixture.go"]); err != nil || hit != 2 || total != 2 {
		t.Fatalf("measured lines = %d/%d, %v", hit, total, err)
	}
}
