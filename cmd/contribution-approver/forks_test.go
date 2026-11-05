package main

import (
	"os"
	"strings"
	"testing"
)

func TestLoadForksAcceptsTheRegistryEntry(t *testing.T) {
	forks, err := loadForks([]byte(forksJSON))
	if err != nil {
		t.Fatal(err)
	}
	want := forkConfig{Upstream: "omnigent-ai/omnigent", Fork: "lkshrk/omnigent", Owner: "lkshrk", DefaultBranch: "main", TitleFormat: "conventional-scoped", CIWorkflow: "contribution-ci.yml"}
	if len(forks) != 1 || forks[0] != want {
		t.Fatalf("forks = %+v", forks)
	}
}

func TestLoadForksRefusesMalformedConfig(t *testing.T) {
	for name, raw := range map[string]string{
		"not json":     "nope",
		"empty list":   "[]",
		"extra key":    strings.Replace(forksJSON, `{`, `{"extra":"x",`, 1),
		"missing key":  strings.Replace(forksJSON, `,"ci_workflow":"contribution-ci.yml"`, ``, 1),
		"owner":        strings.Replace(forksJSON, `"owner":"lkshrk"`, `"owner":"someone"`, 1),
		"fork slug":    strings.Replace(forksJSON, `"fork":"lkshrk/omnigent"`, `"fork":"omnigent"`, 1),
		"upstream":     strings.Replace(forksJSON, `"upstream":"omnigent-ai/omnigent"`, `"upstream":"omnigent"`, 1),
		"title format": strings.Replace(forksJSON, `"conventional-scoped"`, `"anything"`, 1),
		"branch":       strings.Replace(forksJSON, `"default_branch":"main"`, `"default_branch":"../x"`, 1),
		"duplicate":    "[" + strings.Trim(forksJSON, "[]") + "," + strings.Trim(forksJSON, "[]") + "]",
	} {
		t.Run(name, func(t *testing.T) {
			if forks, err := loadForks([]byte(raw)); err == nil {
				t.Fatalf("accepted %+v", forks)
			}
		})
	}
}

func TestEmbeddedForksAreValid(t *testing.T) {
	forks, err := loadForks(embeddedForks)
	if err != nil || len(forks) == 0 {
		t.Fatalf("embedded forks: %v (%d)", err, len(forks))
	}
	disk, err := os.ReadFile("forks.json")
	if err != nil || string(disk) != string(embeddedForks) {
		t.Fatal("embedded forks differ from forks.json")
	}
}
