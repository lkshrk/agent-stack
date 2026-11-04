package main

import (
	"crypto/rand"
	"fmt"
	"math/big"
	"sync"
	"time"
)

const codeDigits = 8

var codeSpace = big.NewInt(100_000_000)

type prKey struct {
	fork   string
	number int
}

type pending struct {
	pr      prKey
	sha     string
	expires time.Time
}

type headState struct {
	sha      string
	consumed bool
	expired  bool
}

type codeStatus int

const (
	codeUnknown codeStatus = iota
	codeExpired
	codeValid
)

// codeStore holds single-use approval codes in memory; a restart drops them and the next poll announces again.
type codeStore struct {
	mu        sync.Mutex
	ttl       time.Duration
	now       func() time.Time
	byCode    map[string]pending
	announced map[prKey]headState
}

func newCodeStore(ttl time.Duration, now func() time.Time) *codeStore {
	return &codeStore{ttl: ttl, now: now, byCode: map[string]pending{}, announced: map[prKey]headState{}}
}

func (s *codeStore) needsAnnouncement(k prKey, sha string) bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	a, ok := s.announced[k]
	return !ok || a.sha != sha || (a.expired && !a.consumed)
}

func (s *codeStore) issue(k prKey, sha string) (string, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	now := s.now()
	s.dropLocked(k, func(p pending) bool { return now.Before(p.expires) })
	// Expired codes stay one more TTL so a late reply is told a new code follows.
	for code, p := range s.byCode {
		if !now.Before(p.expires.Add(s.ttl)) {
			delete(s.byCode, code)
		}
	}
	for range 16 {
		n, err := rand.Int(rand.Reader, codeSpace)
		if err != nil {
			return "", err
		}
		code := fmt.Sprintf("%0*d", codeDigits, n)
		if _, taken := s.byCode[code]; taken {
			continue
		}
		s.byCode[code] = pending{pr: k, sha: sha, expires: now.Add(s.ttl)}
		s.announced[k] = headState{sha: sha}
		return code, nil
	}
	return "", fmt.Errorf("no free approval code")
}

func (s *codeStore) take(code string) (pending, codeStatus) {
	s.mu.Lock()
	defer s.mu.Unlock()
	p, ok := s.byCode[code]
	if !ok {
		return pending{}, codeUnknown
	}
	delete(s.byCode, code)
	a, current := s.announced[p.pr]
	current = current && a.sha == p.sha
	if !s.now().Before(p.expires) {
		if current {
			a.expired = true
			s.announced[p.pr] = a
		}
		return p, codeExpired
	}
	if current {
		a.consumed = true
		s.announced[p.pr] = a
	}
	return p, codeValid
}

func (s *codeStore) markReviewed(k prKey, sha string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.dropLocked(k, func(pending) bool { return true })
	s.announced[k] = headState{sha: sha, consumed: true}
}

func (s *codeStore) forget(k prKey) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.dropLocked(k, func(pending) bool { return true })
	delete(s.announced, k)
}

func (s *codeStore) keepOnly(fork string, ready map[prKey]bool) {
	s.mu.Lock()
	defer s.mu.Unlock()
	for k := range s.announced {
		if k.fork == fork && !ready[k] {
			s.dropLocked(k, func(pending) bool { return true })
			delete(s.announced, k)
		}
	}
}

func (s *codeStore) live() int {
	s.mu.Lock()
	defer s.mu.Unlock()
	n := 0
	for _, p := range s.byCode {
		if s.now().Before(p.expires) {
			n++
		}
	}
	return n
}

func (s *codeStore) dropLocked(k prKey, match func(pending) bool) {
	for code, p := range s.byCode {
		if p.pr == k && match(p) {
			delete(s.byCode, code)
		}
	}
}
