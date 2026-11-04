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

// codeStore holds single-use approval codes in memory; a restart drops them and the next poll announces again.
type codeStore struct {
	mu        sync.Mutex
	ttl       time.Duration
	now       func() time.Time
	byCode    map[string]pending
	announced map[prKey]string
}

func newCodeStore(ttl time.Duration, now func() time.Time) *codeStore {
	return &codeStore{ttl: ttl, now: now, byCode: map[string]pending{}, announced: map[prKey]string{}}
}

func (s *codeStore) announcedSHA(k prKey) (string, bool) {
	s.mu.Lock()
	defer s.mu.Unlock()
	sha, ok := s.announced[k]
	return sha, ok
}

// issue replaces every code for the pull request with a new one bound to sha.
func (s *codeStore) issue(k prKey, sha string) (string, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.dropLocked(k)
	now := s.now()
	for code, p := range s.byCode {
		if !now.Before(p.expires) {
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
		s.announced[k] = sha
		return code, nil
	}
	return "", fmt.Errorf("no free approval code")
}

// take consumes a live code; an expired code is removed and refused.
func (s *codeStore) take(code string) (pending, bool) {
	s.mu.Lock()
	defer s.mu.Unlock()
	p, ok := s.byCode[code]
	if !ok {
		return pending{}, false
	}
	delete(s.byCode, code)
	if !s.now().Before(p.expires) {
		return pending{}, false
	}
	return p, true
}

func (s *codeStore) forget(k prKey) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.dropLocked(k)
	delete(s.announced, k)
}

// keepOnly forgets every pull request of fork that is not in ready.
func (s *codeStore) keepOnly(fork string, ready map[prKey]bool) {
	s.mu.Lock()
	defer s.mu.Unlock()
	for k := range s.announced {
		if k.fork == fork && !ready[k] {
			s.dropLocked(k)
			delete(s.announced, k)
		}
	}
}

func (s *codeStore) live() int {
	s.mu.Lock()
	defer s.mu.Unlock()
	return len(s.byCode)
}

func (s *codeStore) dropLocked(k prKey) {
	for code, p := range s.byCode {
		if p.pr == k {
			delete(s.byCode, code)
		}
	}
}
