// Command linecoverage checks Go coverage per executable statement line.
package main

import (
	"bufio"
	"fmt"
	"go/ast"
	"go/parser"
	"go/token"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
)

type position struct {
	line, column int
}

func (p position) before(other position) bool {
	return p.line < other.line || p.line == other.line && p.column < other.column
}

type block struct {
	start, end position
	hit        bool
}

var profileRow = regexp.MustCompile(`^(.+):(\d+)\.(\d+),(\d+)\.(\d+) \d+ (\d+)$`)

func readProfile(input io.Reader) (map[string][]block, error) {
	files := map[string][]block{}
	scanner := bufio.NewScanner(input)
	if !scanner.Scan() || !strings.HasPrefix(scanner.Text(), "mode: ") {
		return nil, fmt.Errorf("missing Go coverage profile header")
	}
	for scanner.Scan() {
		fields := profileRow.FindStringSubmatch(scanner.Text())
		if fields == nil {
			return nil, fmt.Errorf("invalid Go coverage entry: %s", scanner.Text())
		}
		values := make([]int, 5)
		for i, field := range fields[2:] {
			number, err := strconv.Atoi(field)
			if err != nil {
				return nil, err
			}
			values[i] = number
		}
		files[fields[1]] = append(files[fields[1]], block{
			start: position{values[0], values[1]},
			end:   position{values[2], values[3]},
			hit:   values[4] > 0,
		})
	}
	return files, scanner.Err()
}

func statementLines(path string, blocks []block) (int, int, error) {
	positions := token.NewFileSet()
	file, err := parser.ParseFile(positions, path, nil, 0)
	if err != nil {
		return 0, 0, err
	}
	lines := map[int]bool{}
	ast.Inspect(file, func(node ast.Node) bool {
		switch node.(type) {
		case *ast.BlockStmt, *ast.CaseClause, *ast.CommClause, *ast.EmptyStmt, *ast.LabeledStmt:
			return true
		case ast.Stmt:
			source := positions.Position(node.Pos())
			point := position{source.Line, source.Column}
			hit := false
			var selected *block
			for i := range blocks {
				candidate := &blocks[i]
				if point.before(candidate.start) || !point.before(candidate.end) {
					continue
				}
				if selected == nil || selected.start.before(candidate.start) {
					selected = candidate
					hit = candidate.hit
				} else if selected.start == candidate.start && selected.end == candidate.end {
					hit = hit || candidate.hit
				}
			}
			if previous, exists := lines[source.Line]; exists {
				hit = hit && previous
			}
			lines[source.Line] = hit
		}
		return true
	})
	covered := 0
	for _, hit := range lines {
		if hit {
			covered++
		}
	}
	return covered, len(lines), nil
}

func check(profilePath string, inventory io.Reader, output io.Writer) (bool, error) {
	profile, err := os.Open(profilePath)
	if err != nil {
		return false, err
	}
	defer profile.Close()
	files, err := readProfile(profile)
	if err != nil {
		return false, err
	}
	passed := true
	scanner := bufio.NewScanner(inventory)
	for scanner.Scan() {
		if scanner.Text() == "" {
			continue
		}
		projectFile, source, ok := strings.Cut(scanner.Text(), "\t")
		if !ok {
			return false, fmt.Errorf("invalid Go source inventory: %s", scanner.Text())
		}
		path := filepath.FromSlash(source)
		covered, total, err := statementLines(path, files[projectFile])
		if err != nil {
			return false, err
		}
		if covered*10 < total*9 {
			passed = false
			fmt.Fprintf(output, "%s: %d/%d executable statement lines (%.2f%%)\n",
				path, covered, total, float64(covered)*100/float64(total))
		}
	}
	return passed, scanner.Err()
}

func main() {
	if len(os.Args) != 2 {
		fmt.Fprintln(os.Stderr, "usage: linecoverage profile < inventory")
		os.Exit(2)
	}
	passed, err := check(os.Args[1], os.Stdin, os.Stdout)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(2)
	}
	if !passed {
		os.Exit(1)
	}
}
