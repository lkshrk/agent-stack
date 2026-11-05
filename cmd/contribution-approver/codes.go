package main

import (
	"crypto/rand"
	"fmt"
	"math/big"
	"sync"
	"time"
)

const (
	codeDigits   = 8
	tombstoneTTL = 24 * time.Hour
	maxBindHold  = 24 * time.Hour
)

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

type sentAnnouncement struct {
	digest string
	// lastReview is the highest review ID seen after the announcement went out; only higher IDs bind to it.
	lastReview int64
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
	codeExpiredStale
	codeValid
)

// codeStore holds single-use approval codes in memory; a restart drops them and the next poll announces again.
type codeStore struct {
	mu        sync.Mutex
	ttl       time.Duration
	now       func() time.Time
	byCode    map[string]pending
	announced map[prKey]headState
	sent      map[prKey]map[string]sentAnnouncement
	// resend maps a pull request that must be announced again to the review ID its next record must exceed.
	resend    map[prKey]int64
	unbound   map[prKey]int
	holdUntil map[prKey]time.Time
	heldSHA   map[prKey]string
}

func newCodeStore(ttl time.Duration, now func() time.Time) *codeStore {
	return &codeStore{
		ttl: ttl, now: now, byCode: map[string]pending{}, announced: map[prKey]headState{},
		sent: map[prKey]map[string]sentAnnouncement{}, resend: map[prKey]int64{},
		unbound: map[prKey]int{}, holdUntil: map[prKey]time.Time{}, heldSHA: map[prKey]string{},
	}
}

func (s *codeStore) recordAnnouncement(k prKey, sha, digest string, lastReview int64) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.sent[k] == nil {
		s.sent[k] = map[string]sentAnnouncement{}
	}
	s.sent[k][sha] = sentAnnouncement{digest: digest, lastReview: max(lastReview, s.resend[k])}
	delete(s.resend, k)
	delete(s.unbound, k)
	delete(s.holdUntil, k)
	delete(s.heldSHA, k)
}

func (s *codeStore) announcementOf(k prKey, sha string) (sentAnnouncement, bool) {
	s.mu.Lock()
	defer s.mu.Unlock()
	a, ok := s.sent[k][sha]
	return a, ok
}

func (s *codeStore) requestReannouncement(k prKey, floor int64) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.resend[k] = max(s.resend[k], floor)
}

func (s *codeStore) cancelReannouncement(k prKey) {
	s.mu.Lock()
	defer s.mu.Unlock()
	delete(s.resend, k)
}

// held reports whether announcing sha waits for the review listing; another head is announced at once.
func (s *codeStore) held(k prKey, sha string) bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.heldLocked(k, sha)
}

func (s *codeStore) heldLocked(k prKey, sha string) bool {
	return s.heldSHA[k] == sha && s.now().Before(s.holdUntil[k])
}

func (s *codeStore) reannouncing(k prKey) bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	_, ok := s.resend[k]
	return ok
}

// bindFailed asks for a re-announcement; after the first consecutive failure it holds off, doubling up to a day.
func (s *codeStore) bindFailed(k prKey, sha string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.resend[k] = max(s.resend[k], 0)
	s.unbound[k]++
	if n := s.unbound[k]; n > 1 {
		s.holdUntil[k], s.heldSHA[k] = s.now().Add(min(s.ttl<<min(n-2, 10), maxBindHold)), sha
	}
}

func (s *codeStore) needsAnnouncement(k prKey, sha string) bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.heldLocked(k, sha) {
		return false
	}
	a, ok := s.announced[k]
	_, resend := s.resend[k]
	return resend || !ok || a.sha != sha || (a.expired && !a.consumed)
}

func (s *codeStore) issue(k prKey, sha string) (string, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	now := s.now()
	s.dropLive(k)
	// Expired codes stay as tombstones so a late reply maps back to its pull request.
	for code, p := range s.byCode {
		if !now.Before(p.expires.Add(tombstoneTTL)) {
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
	now := s.now()
	if !now.Before(p.expires.Add(tombstoneTTL)) {
		return pending{}, codeUnknown
	}
	a, current := s.announced[p.pr]
	current = current && a.sha == p.sha
	if !now.Before(p.expires) {
		if !current || a.consumed {
			return p, codeExpiredStale
		}
		a.expired = true
		s.announced[p.pr] = a
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
	s.dropLive(k)
	s.announced[k] = headState{sha: sha, consumed: true}
}

func (s *codeStore) forget(k prKey) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.dropLive(k)
	delete(s.announced, k)
}

func (s *codeStore) keepOnly(fork string, ready map[prKey]bool) {
	s.mu.Lock()
	defer s.mu.Unlock()
	for k := range s.announced {
		if k.fork == fork && !ready[k] {
			s.dropLive(k)
			delete(s.announced, k)
		}
	}
	for k := range s.sent {
		if k.fork == fork && !ready[k] {
			delete(s.sent, k)
		}
	}
	for k := range s.resend {
		if k.fork == fork && !ready[k] {
			delete(s.resend, k)
		}
	}
	for k := range s.unbound {
		if k.fork == fork && !ready[k] {
			delete(s.unbound, k)
			delete(s.holdUntil, k)
			delete(s.heldSHA, k)
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

func (s *codeStore) dropLive(k prKey) {
	now := s.now()
	for code, p := range s.byCode {
		if p.pr == k && now.Before(p.expires) {
			delete(s.byCode, code)
		}
	}
}
