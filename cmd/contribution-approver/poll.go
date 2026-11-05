package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"log"
	"strings"
	"unicode"
)

const (
	maxTitleRunes = 200
	maxBodyRunes  = 300
)

type poller struct {
	gh      *githubClient
	forks   []forkConfig
	codes   *codeStore
	notify  notifier
	login   string
	refused map[prKey]string
}

func contentDigest(title, body string) string {
	raw, _ := json.Marshal([]string{title, body})
	sum := sha256.Sum256(raw)
	return hex.EncodeToString(sum[:])
}

func (p *poller) poll(ctx context.Context) {
	for _, cfg := range p.forks {
		if err := p.pollFork(ctx, cfg); err != nil {
			log.Printf("contribution-approver: poll %s: %v", cfg.Fork, err)
		}
	}
}

func (p *poller) pollFork(ctx context.Context, cfg forkConfig) error {
	fork := cfg.Fork
	if p.login == "" {
		login, err := p.gh.login(ctx)
		if err != nil {
			return err
		}
		p.login = login
	}
	if p.login != cfg.Owner {
		return fmt.Errorf("the GitHub token belongs to %s, not the fork owner %s", p.login, cfg.Owner)
	}
	if err := p.gh.verifyFork(ctx, cfg); err != nil {
		return err
	}
	pulls, err := p.gh.openPulls(ctx, cfg)
	if err != nil {
		return err
	}
	ready := map[prKey]bool{}
	for _, pr := range pulls {
		if !isReady(cfg, pr) {
			continue
		}
		k := prKey{fork: fork, number: pr.Number}
		ready[k] = true
		if !p.codes.needsAnnouncement(k, pr.Head.SHA) {
			continue
		}
		if err := p.announce(ctx, cfg, k, pr.Head.SHA); err != nil {
			log.Printf("contribution-approver: announce %s#%d: %v", fork, pr.Number, err)
		}
	}
	p.codes.keepOnly(fork, ready)
	return nil
}

func isReady(cfg forkConfig, pr pullRequest) bool {
	return pr.hasLabel(readyLabel) &&
		fullSHA.MatchString(pr.Head.SHA) &&
		pr.Head.repo() == cfg.Fork && pr.Base.repo() == cfg.Fork &&
		pr.Base.Ref == cfg.DefaultBranch &&
		!strings.HasPrefix(strings.ToLower(pr.Head.Ref), "upstream/")
}

func (p *poller) announce(ctx context.Context, cfg forkConfig, k prKey, sha string) error {
	green, runs, err := p.gh.ciGreen(ctx, cfg, sha)
	if err != nil || !green {
		return err
	}
	pr, err := p.gh.pull(ctx, cfg.Fork, k.number)
	if err != nil {
		return err
	}
	if pr.Head.SHA != sha || !isReady(cfg, pr) {
		return nil
	}
	reviewed, err := p.gh.ownerReviewed(ctx, cfg.Fork, k.number, cfg.Owner, sha)
	if err != nil {
		return err
	}
	if reviewed {
		p.codes.markReviewed(k, sha)
		return nil
	}
	reason, err := p.gateRefusal(ctx, cfg, pr, sha)
	if err != nil {
		return err
	}
	if reason != "" {
		if p.refused == nil {
			p.refused = map[prKey]string{}
		}
		if seen := sha + "\x00" + reason; p.refused[k] != seen {
			p.refused[k] = seen
			log.Printf("contribution-approver: %s#%d at %s not announced: %s", cfg.Fork, k.number, sha, reason)
		}
		return nil
	}
	delete(p.refused, k)
	code, err := p.codes.issue(k, sha, contentDigest(pr.Title, pr.Body))
	if err != nil {
		return err
	}
	if err := p.notify.Notify(ctx, announcement(cfg, pr, sha, runs, code)); err != nil {
		p.codes.forget(k)
		return err
	}
	log.Printf("contribution-approver: announced %s#%d at %s", cfg.Fork, k.number, sha)
	return nil
}

func (p *poller) gateRefusal(ctx context.Context, cfg forkConfig, pr pullRequest, sha string) (string, error) {
	if !titleFormats[cfg.TitleFormat].MatchString(pr.Title) {
		return "the title does not follow " + cfg.TitleFormat, nil
	}
	return p.gh.compare(ctx, cfg, sha)
}

func announcement(cfg forkConfig, pr pullRequest, sha string, runs int, code string) string {
	return strings.Join([]string{
		fmt.Sprintf("Contribution ready for upstream: %s#%d", cfg.Fork, pr.Number),
		"Title: " + flatten(pr.Title, maxTitleRunes),
		"Body: " + bodyExcerpt(pr.Body),
		"Head: " + sha,
		fmt.Sprintf("Diff: %d files, +%d -%d", pr.ChangedFiles, pr.Additions, pr.Deletions),
		fmt.Sprintf("CI: %s passed (%d %s)", cfg.CIWorkflow, runs, plural(runs, "run", "runs")),
		pr.HTMLURL,
		"",
		fmt.Sprintf("Reply \"approve %s\" to publish this commit upstream or \"reject %s\" to request changes. The code works once and expires in %d minutes.", code, code, int(codeTTL.Minutes())),
	}, "\n")
}

func bodyExcerpt(body string) string {
	if body = strings.Join(strings.Fields(flatten(body, maxBodyRunes)), " "); body == "" {
		return "(empty)"
	}
	return body
}

func flatten(s string, limit int) string {
	s = strings.Map(func(r rune) rune {
		if unicode.IsControl(r) || r == ' ' || r == ' ' {
			return ' '
		}
		return r
	}, s)
	if r := []rune(s); len(r) > limit {
		s = string(r[:limit]) + "…"
	}
	return s
}

func plural(n int, one, many string) string {
	if n == 1 {
		return one
	}
	return many
}
