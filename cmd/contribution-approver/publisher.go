package main

import (
	"context"
	"errors"
	"fmt"
	"log"
	"regexp"
	"strings"
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
	rv, err := p.gh.decidingReview(ctx, cfg, k.number)
	var capped *pageCapError
	if err != nil && !errors.As(err, &capped) {
		p.logOnce(k, "read\x00"+err.Error(), "contribution-approver: publish %s#%d: %v", cfg.Fork, k.number, err)
		return
	}
	if err == nil && (rv == nil || rv.State != "APPROVED") {
		p.clearReadLogs(k)
		return
	}
	if rv != nil {
		pr, err := p.gh.pull(ctx, cfg.Fork, k.number)
		if err != nil {
			p.logOnce(k, "read\x00"+err.Error(), "contribution-approver: publish %s#%d: %v", cfg.Fork, k.number, err)
			return
		}
		p.clearReadLogs(k)
		if p.settled[k] == settledKey(rv.ID, rv.CommitID, pullFingerprint(pr)) {
			return
		}
	}
	res := p.publish(ctx, cfg, k.number)
	probed := false
	switch {
	case res.alreadyUpstream:
		p.codes.cancelReannouncement(k)
	case res.outcome == outcomeRetry && res.decision.unannounced:
		if r, ok := p.gh.settledUpstream(ctx, cfg, res.decision); ok {
			if r.carried() {
				p.codes.cancelReannouncement(k)
				probed = true
			}
			res = r
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
	upstreamKey := fmt.Sprintf("upstream\x00%d", d.reviewID)
	reasonKey := fmt.Sprintf("tell\x00%d\x00%s", d.reviewID, res.reason)
	p.logOnce(k, fmt.Sprintf("outcome\x00%s\x00%s\x00%s\x00%v", res.outcome, attempt, res.reason, res.alreadyUpstream), "contribution-approver: publish %s%s: %s %s", name, at, res.outcome, res.reason)
	switch {
	case res.alreadyUpstream:
		p.links.set(k, link)
		p.tellOnce(ctx, k, fmt.Sprintf("after\x00%d", d.reviewID), name+at+" is already upstream"+linkPart(link)+"; later changes to the title or body are not published.")
	case res.outcome == outcomePublished:
		p.links.set(k, link)
		p.tellOnce(ctx, k, upstreamKey, withLink("Published "+name+at+" upstream", link))
	case res.outcome == outcomeCurrent:
		p.links.set(k, link)
		switch {
		case retried:
			p.tellOnce(ctx, k, upstreamKey, withLink("Published "+name+at+" upstream; the code is published, but the title and body may not be updated", link))
		case !probed:
			p.tellOnce(ctx, k, upstreamKey, name+at+" is already upstream"+linkPart(link)+"; the title and body were not changed.")
		}
	case res.outcome == outcomeClosedUpstream:
		p.links.set(k, link)
		if !probed {
			p.tellOnce(ctx, k, reasonKey, withLink("Not publishing "+name+at+": "+res.reason, link))
		}
	case res.outcome == outcomeRefused:
		p.tellOnce(ctx, k, reasonKey, "Not publishing "+name+at+": "+res.reason+". "+dismissalNote(res))
	case res.outcome == outcomeRetry:
		if d.reviewID != 0 {
			p.retried[k] = attempt
		}
		if res.reason == headTakenReason {
			p.tellOnce(ctx, k, reasonKey, "Publishing "+name+at+" waits: "+res.reason+". It is tried again on every poll.")
		}
	}
}

func linkPart(link string) string {
	if link == "" {
		return ""
	}
	return ": " + link
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

type notice struct{ key, text string }

// tellOnce marks key only once Signal accepts the text; a failed send waits in unsent for the next poll.
func (p *poller) tellOnce(ctx context.Context, k prKey, key, text string) {
	if p.told[k][key] {
		return
	}
	for _, n := range p.unsent[k] {
		if n.key == key {
			return
		}
	}
	if !p.deliver(ctx, k, notice{key, text}) {
		p.unsent[k] = append(p.unsent[k], notice{key, text})
	}
}

func (p *poller) deliver(ctx context.Context, k prKey, n notice) bool {
	if err := p.notify.Notify(ctx, n.text); err != nil {
		log.Printf("contribution-approver: owner notice not sent, retried on the next poll: %v", err)
		return false
	}
	p.mark(k, n.key)
	return true
}

func (p *poller) flush(ctx context.Context, fork string) {
	for k, pending := range p.unsent {
		if k.fork != fork {
			continue
		}
		var left []notice
		for _, n := range pending {
			if !p.deliver(ctx, k, n) {
				left = append(left, n)
			}
		}
		if len(left) == 0 {
			delete(p.unsent, k)
		} else {
			p.unsent[k] = left
		}
	}
}

func (p *poller) clearReadLogs(k prKey) {
	for key := range p.told[k] {
		if strings.HasPrefix(key, "log\x00read\x00") {
			delete(p.told[k], key)
		}
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
	p.links.keepOnly(fork, ready)
}
