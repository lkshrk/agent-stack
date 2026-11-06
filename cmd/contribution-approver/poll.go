package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"log"
	"maps"
	"strings"
	"time"
	"unicode"
)

const (
	maxTitleRunes = 200
	maxBodyRunes  = 300
)

// poller is driven by one goroutine, run; only links and kick are shared with the decision handler.
type poller struct {
	gh           *githubClient
	forks        []forkConfig
	codes        *codeStore
	notify       notifier
	login        string
	refused      map[prKey]string
	forkTimeout  time.Duration
	kick         chan struct{}
	links        *upstreamLinks
	settled      map[prKey]string
	retried      map[prKey]string
	told         map[prKey]map[string]bool
	unsent       map[prKey][]notice
	prTimeout    time.Duration
	draftTimeout time.Duration
	writeTimeout time.Duration
	// posted: upstream links written by this process whose record is not written yet; any later approval only records them.
	posted    map[prKey]draftPost
	livePulls map[string]map[prKey]bool
	// knownDrafts: every draft ever polled, so its notices survive the draft leaving the list.
	knownDrafts map[string]map[prKey]bool
	// started separates reviews a restart may already have reported from reviews this process must report.
	started time.Time
}

func newPoller(gh *githubClient, forks []forkConfig, codes *codeStore, notify notifier) *poller {
	return &poller{
		gh: gh, forks: forks, codes: codes, notify: notify, forkTimeout: pollTimeout, prTimeout: prTimeout, draftTimeout: prTimeout, writeTimeout: draftWriteTimeout, posted: map[prKey]draftPost{}, started: codes.now(),
		kick: make(chan struct{}, 1), links: &upstreamLinks{urls: map[prKey]string{}},
		livePulls: map[string]map[prKey]bool{}, knownDrafts: map[string]map[prKey]bool{},
		settled: map[prKey]string{}, retried: map[prKey]string{}, told: map[prKey]map[string]bool{}, unsent: map[prKey][]notice{},
	}
}

func (p *poller) run(ctx context.Context, interval time.Duration) {
	for {
		p.poll(ctx)
		timer := time.NewTimer(interval)
		select {
		case <-ctx.Done():
			timer.Stop()
			return
		case <-timer.C:
		case <-p.kick:
			timer.Stop()
		}
	}
}

func (p *poller) wake() {
	select {
	case p.kick <- struct{}{}:
	default:
	}
}

func contentDigest(title, body string) string {
	raw, _ := json.Marshal([]string{title, body})
	sum := sha256.Sum256(raw)
	return hex.EncodeToString(sum[:])
}

func (p *poller) poll(ctx context.Context) {
	for _, cfg := range p.forks {
		fctx, cancel := context.WithTimeout(ctx, p.forkTimeout)
		verified, err := p.pollFork(fctx, cfg)
		cancel()
		if err != nil {
			log.Printf("contribution-approver: poll %s: %v", cfg.Fork, err)
		}
		if !verified {
			continue
		}
		dctx, cancel := context.WithTimeout(ctx, p.forkTimeout)
		if err := p.pollDrafts(dctx, cfg); err != nil {
			log.Printf("contribution-approver: drafts %s: %v", cfg.Fork, err)
		}
		cancel()
		live := maps.Clone(p.livePulls[cfg.Fork])
		if live == nil {
			live = map[prKey]bool{}
		}
		maps.Copy(live, p.knownDrafts[cfg.Fork])
		p.keepOnly(cfg.Fork, live)
	}
}

// pollFork reports whether the fork was verified, which drafts need before they are polled.
func (p *poller) pollFork(ctx context.Context, cfg forkConfig) (bool, error) {
	fork := cfg.Fork
	p.flush(ctx, fork)
	if p.login == "" {
		login, err := p.gh.login(ctx)
		if err != nil {
			return false, err
		}
		p.login = login
	}
	if p.login != cfg.Owner {
		return false, fmt.Errorf("the GitHub token belongs to %s, not the fork owner %s", p.login, cfg.Owner)
	}
	if err := p.gh.verifyFork(ctx, cfg); err != nil {
		return false, err
	}
	pulls, err := p.gh.openPulls(ctx, cfg)
	if err != nil {
		return true, err
	}
	ready := map[prKey]bool{}
	for _, pr := range pulls {
		if !isReady(cfg, pr) {
			continue
		}
		k := prKey{fork: fork, number: pr.Number}
		ready[k] = true
		pctx, cancel := context.WithTimeout(ctx, p.prTimeout)
		p.maybePublish(pctx, cfg, k)
		if p.codes.needsAnnouncement(k, pr.Head.SHA) {
			if err := p.announce(pctx, cfg, k, pr.Head.SHA); err != nil {
				log.Printf("contribution-approver: announce %s#%d: %v", fork, pr.Number, err)
			}
		}
		cancel()
	}
	p.codes.keepOnly(fork, ready)
	p.livePulls[fork] = ready
	return true, nil
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
	if reviewed && !p.codes.reannouncing(k) {
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
	code, err := p.codes.issue(k, sha)
	if err != nil {
		return err
	}
	if err := p.notify.Notify(ctx, announcement(cfg, pr, sha, runs, code)); err != nil {
		p.codes.forget(k)
		return err
	}
	last, err := p.gh.lastReviewID(ctx, cfg.Fork, k.number)
	if err != nil {
		p.codes.bindFailed(k, sha)
		return fmt.Errorf("announced, but listing reviews to bind it failed: %w", err)
	}
	p.codes.recordAnnouncement(k, sha, contentDigest(pr.Title, pr.Body), last)
	log.Printf("contribution-approver: announced %s#%d at %s", cfg.Fork, k.number, sha)
	return nil
}

func (p *poller) gateRefusal(ctx context.Context, cfg forkConfig, pr pullRequest, sha string) (string, error) {
	if !titleFormats[cfg.TitleFormat].MatchString(pr.Title) {
		return "the title does not follow " + cfg.TitleFormat, nil
	}
	for _, check := range []func() error{
		func() error { return p.gh.checkTimeline(ctx, cfg, pr.Number, nil) },
		func() error { return p.gh.checkDiff(ctx, cfg, sha) },
	} {
		if err := check(); err != nil {
			var r *refusal
			var capped *pageCapError
			switch {
			case errors.As(err, &r):
				return r.reason, nil
			case errors.As(err, &capped):
				return capped.what, nil
			}
			return "", err
		}
	}
	return "", nil
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
