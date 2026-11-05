package main

import (
	"bytes"
	_ "embed"
	"encoding/json"
	"fmt"
	"strings"
)

//go:generate python3 ../../templates/render_approver_config.py

//go:embed forks.json
var embeddedForks []byte

func loadForks(raw []byte) ([]forkConfig, error) {
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.DisallowUnknownFields()
	var forks []forkConfig
	if err := dec.Decode(&forks); err != nil {
		return nil, fmt.Errorf("forks.json is invalid: %w", err)
	}
	if len(forks) == 0 {
		return nil, fmt.Errorf("forks.json lists no forks")
	}
	seen := map[string]bool{}
	for _, cfg := range forks {
		if err := cfg.validate(); err != nil {
			return nil, err
		}
		if seen[cfg.Fork] {
			return nil, fmt.Errorf("forks.json lists %s twice", cfg.Fork)
		}
		seen[cfg.Fork] = true
	}
	return forks, nil
}

func (c forkConfig) validate() error {
	if c.Upstream == "" || c.Fork == "" || c.Owner == "" || c.DefaultBranch == "" || c.TitleFormat == "" || c.CIWorkflow == "" {
		return fmt.Errorf("forks.json entry %q must set every field", c.Fork)
	}
	switch {
	case !slug.MatchString(c.Fork):
		return fmt.Errorf("forks.json fork %q is not owner/name", c.Fork)
	case !slug.MatchString(c.Upstream) || strings.SplitN(c.Fork, "/", 2)[0] != c.Owner:
		return fmt.Errorf("%s upstream or owner is invalid", c.Fork)
	case titleFormats[c.TitleFormat] == nil || !branchName.MatchString(c.DefaultBranch) || strings.Contains(c.DefaultBranch, ".."):
		return fmt.Errorf("%s title_format or default_branch is invalid", c.Fork)
	case strings.ContainsAny(c.CIWorkflow, "/\\") || c.CIWorkflow == "." || c.CIWorkflow == "..":
		return fmt.Errorf("%s ci_workflow is invalid", c.Fork)
	}
	return nil
}
