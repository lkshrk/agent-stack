package main

import (
	"context"
	"errors"
	"fmt"
	"log"
	"regexp"
	"sync"
)

type upstreamLinks struct {
	mu   sync.Mutex
	urls map[prKey]string
}

func (l *upstreamLinks) get(k prKey) string {
	l.mu.Lock()
	defer l.mu.Unlock()
	return l.urls[k]
}

func (l *upstreamLinks) set(k prKey, url string) {
	l.mu.Lock()
	defer l.mu.Unlock()
	if url == "" {
		delete(l.urls, k)
		return
	}
	l.urls[k] = url
}

func (l *upstreamLinks) keepOnly(fork string, ready map[prKey]bool) {
	l.mu.Lock()
	defer l.mu.Unlock()
	for k := range l.urls {
		if k.fork == fork && !ready[k] {
			delete(l.urls, k)
		}
	}
}

func settledKey(reviewID int64, sha, pull string) string {
	return fmt.Sprintf("%d\x00%s\x00%s", reviewID, sha, pull)
}

func (p *poller) maybePublish(ctx context.Context, cfg forkConfig, k prKey) {
	p.flush(ctx, k)
	rv, err := p.gh.decidingReview(ctx, cfg, k.number)
	var capped *pageCapError
	switch {
	case errors.As(err, &capped):
	case err != nil:
		p.logOnce(k, "read\x00"+err.Error(), "contribution-approver: publish %s#%d: %v", cfg.Fork, k.number, err)
		return
	case rv == nil || rv.State != "APPROVED":
		return
	}
	if rv != nil {
		pr, err := p.gh.pull(ctx, cfg.Fork, k.number)
		if err != nil {
			p.logOnce(k, "read\x00"+err.Error(), "contribution-approver: publish %s#%d: %v", cfg.Fork, k.number, err)
			return
		}
		if p.settled[k] == settledKey(rv.ID, rv.CommitID, pullFingerprint(pr)) {
			return
		}
	}
	res := p.publish(ctx, cfg, k.number)
	probed := false
	if res.outcome == outcomeRetry && res.decision.unannounced {
		if r, ok := p.gh.settledUpstream(ctx, cfg, res.decision); ok {
			p.codes.cancelReannouncement(k)
			res, probed = r, true
		}
	}
	p.report(ctx, cfg, k, res, probed)
	switch res.outcome {
	case outcomePublished, outcomeCurrent, outcomeClosedUpstream:
		p.settled[k] = settledKey(res.decision.reviewID, res.decision.sha, res.decision.pull)
	default:
		delete(p.settled, k)
	}
}

// report tells the owner only fixed text, the fork, the PR number, a validated SHA and a shape-checked upstream URL.
func (p *poller) report(ctx context.Context, cfg forkConfig, k prKey, res publishResult, probed bool) {
	d := res.decision
	name := fmt.Sprintf("%s#%d", cfg.Fork, k.number)
	at := ""
	if fullSHA.MatchString(d.sha) {
		at = " at " + d.sha
	}
	link := ownUpstreamURL(cfg, res.upstreamURL)
	attempt := fmt.Sprintf("%d\x00%s", d.reviewID, d.sha)
	retried := p.retried[k] == attempt
	if res.outcome != outcomeRetry {
		delete(p.retried, k)
	}
	p.logOnce(k, fmt.Sprintf("outcome\x00%s\x00%s\x00%s", res.outcome, attempt, res.reason), "contribution-approver: publish %s%s: %s %s", name, at, res.outcome, res.reason)
	switch res.outcome {
	case outcomePublished:
		p.links.set(k, link)
		p.send(ctx, k, withLink("Published "+name+at+" upstream", link))
	case outcomeCurrent:
		p.links.set(k, link)
		if retried {
			p.send(ctx, k, withLink("Published "+name+at+" upstream; the code is published, but the title and body may not be updated", link))
		}
	case outcomeClosedUpstream:
		p.links.set(k, "")
		if !probed {
			p.tellOnce(ctx, k, d.reviewID, res.reason, withLink("Not publishing "+name+at+": "+res.reason, link))
		}
	case outcomeRefused:
		p.tellOnce(ctx, k, d.reviewID, res.reason, "Not publishing "+name+at+": "+res.reason+". "+dismissalNote(res))
	case outcomeRetry:
		if d.reviewID != 0 {
			p.retried[k] = attempt
		}
		if res.reason == headTakenReason {
			p.tellOnce(ctx, k, d.reviewID, res.reason, "Publishing "+name+at+" waits: "+res.reason+". It is tried again on every poll.")
		}
	}
}

const headTakenReason = "the upstream head is taken or not visible yet, so opening the pull request waits"

func dismissalNote(res publishResult) string {
	switch {
	case res.decision.reviewID == 0:
		return "No approval was dismissed."
	case res.dismissed:
		return "The approval was dismissed; approve a new announcement to publish."
	}
	return "The approval could not be dismissed yet; the approver keeps trying."
}

func withLink(text, link string) string {
	if link == "" {
		return text + "."
	}
	return text + ": " + link
}

func ownUpstreamURL(cfg forkConfig, url string) string {
	if regexp.MustCompile(`^https://github\.com/` + regexp.QuoteMeta(cfg.Upstream) + `/pull/[1-9][0-9]*$`).MatchString(url) {
		return url
	}
	return ""
}

func (p *poller) tellOnce(ctx context.Context, k prKey, reviewID int64, reason, text string) {
	key := fmt.Sprintf("tell\x00%d\x00%s", reviewID, reason)
	if p.told[k][key] {
		return
	}
	if err := p.notify.Notify(ctx, text); err != nil {
		log.Printf("contribution-approver: owner notice not sent: %v", err)
		return
	}
	p.mark(k, key)
}

func (p *poller) send(ctx context.Context, k prKey, text string) {
	if err := p.notify.Notify(ctx, text); err != nil {
		log.Printf("contribution-approver: owner notice not sent, retried on the next poll: %v", err)
		p.unsent[k] = append(p.unsent[k], text)
	}
}

func (p *poller) flush(ctx context.Context, k prKey) {
	pending := p.unsent[k]
	delete(p.unsent, k)
	for _, text := range pending {
		p.send(ctx, k, text)
	}
}

func (p *poller) logOnce(k prKey, key, format string, args ...any) {
	if p.told[k]["log\x00"+key] {
		return
	}
	log.Printf(format, args...)
	p.mark(k, "log\x00"+key)
}

func (p *poller) mark(k prKey, key string) {
	if p.told[k] == nil {
		p.told[k] = map[string]bool{}
	}
	p.told[k][key] = true
}

func (p *poller) keepOnly(fork string, ready map[prKey]bool) {
	for _, m := range []map[prKey]string{p.settled, p.retried} {
		for k := range m {
			if k.fork == fork && !ready[k] {
				delete(m, k)
			}
		}
	}
	for k := range p.told {
		if k.fork == fork && !ready[k] {
			delete(p.told, k)
		}
	}
	for k := range p.unsent {
		if k.fork == fork && !ready[k] {
			delete(p.unsent, k)
		}
	}
	p.links.keepOnly(fork, ready)
}
