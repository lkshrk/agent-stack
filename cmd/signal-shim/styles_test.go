package main

import (
	"math/rand"
	"sort"
	"strconv"
	"strings"
	"testing"
	"unicode/utf16"
	"unicode/utf8"
)

// restParse is a port of signal-cli-rest-api 0.101 src/utils/textstyleparser.go (MIT).
func restParse(input string) (string, []string) {
	type token struct{ begin, kind int }
	const (
		italic = iota + 1
		bold
		mono
		strike
		spoiler
		boldItalic
	)
	names := map[int]string{italic: "ITALIC", bold: "BOLD", mono: "MONOSPACE", strike: "STRIKETHROUGH", spoiler: "SPOILER"}
	var (
		stack  []token
		full   strings.Builder
		styles []string
		pos    int
		prev   rune
	)
	u16 := func() int { return len(utf16.Encode([]rune(full.String()))) }
	next := func() rune {
		if pos >= len(input) {
			return -1
		}
		r, w := utf8.DecodeRuneInString(input[pos:])
		pos += w
		return r
	}
	peek := func() rune {
		if pos >= len(input) {
			return -1
		}
		r, _ := utf8.DecodeRuneInString(input[pos:])
		return r
	}
	handle := func(kind int) {
		if len(stack) > 0 && stack[len(stack)-1].kind == kind {
			t := stack[len(stack)-1]
			stack = stack[:len(stack)-1]
			length := u16() - t.begin
			if kind == boldItalic {
				styles = append(styles, strconv.Itoa(t.begin)+":"+strconv.Itoa(length)+":BOLD", strconv.Itoa(t.begin)+":"+strconv.Itoa(length)+":ITALIC")
			} else {
				styles = append(styles, strconv.Itoa(t.begin)+":"+strconv.Itoa(length)+":"+names[kind])
			}
			return
		}
		stack = append(stack, token{u16(), kind})
	}
	for {
		c := next()
		if c == -1 {
			break
		}
		n := peek()
		switch {
		case c == '*':
			if n == '*' {
				next()
				if peek() == '*' {
					next()
					if prev == '\\' {
						prev = c
						full.WriteString("***")
						continue
					}
					handle(boldItalic)
				} else {
					if prev == '\\' {
						prev = c
						full.WriteString("**")
						continue
					}
					handle(bold)
				}
			} else {
				if prev == '\\' {
					prev = c
					full.WriteString("*")
					continue
				}
				handle(italic)
			}
		case c == '|' && n == '|':
			next()
			if prev == '\\' {
				prev = c
				full.WriteString("||")
				continue
			}
			handle(spoiler)
		case c == '~':
			if prev == '\\' {
				prev = c
				full.WriteString("~")
				continue
			}
			handle(strike)
		case c == '`':
			if prev == '\\' {
				prev = c
				full.WriteString("`")
				continue
			}
			handle(mono)
		case c == '\\' && (n == '*' || n == '`' || n == '|' || n == '~'):
			prev = c
			continue
		default:
			full.WriteRune(c)
		}
		prev = c
	}
	return full.String(), styles
}

func sortedCopy(s []string) []string {
	out := append([]string(nil), s...)
	sort.Strings(out)
	return out
}

func checkRoundTrip(t *testing.T, text string, styles []string) []string {
	t.Helper()
	units := utf16.Encode([]rune(text))
	kept := keepRenderable(units, parseRanges(units, styles))
	var want []string
	for _, r := range kept {
		want = append(want, strconv.Itoa(r.start)+":"+strconv.Itoa(r.end-r.start)+":"+r.style)
	}
	msg, ok := styledMessage(text, styles)
	if !ok {
		if len(kept) != 0 {
			t.Fatalf("styledMessage(%q, %v) = !ok with %d renderable ranges", text, styles, len(kept))
		}
		return nil
	}
	plain, got := restParse(msg)
	if plain != text {
		t.Fatalf("text %q styles %v rendered %q, parsed back as %q", text, styles, msg, plain)
	}
	if strings.Join(sortedCopy(got), ",") != strings.Join(sortedCopy(want), ",") {
		t.Fatalf("text %q styles %v rendered %q, parsed styles %v, want %v", text, styles, msg, got, want)
	}
	return got
}

func TestStyledMessageRoundTrips(t *testing.T) {
	cases := []struct {
		name   string
		text   string
		styles []string
		keep   int
	}{
		{"bold", "hello world", []string{"0:5:BOLD"}, 1},
		{"every style", "a b c d e", []string{"0:1:BOLD", "2:1:ITALIC", "4:1:STRIKETHROUGH", "6:1:MONOSPACE", "8:1:SPOILER"}, 5},
		{"bold and italic on one span", "both", []string{"0:4:BOLD", "0:4:ITALIC"}, 2},
		{"bold and monospace on one span", "both", []string{"0:4:BOLD", "0:4:MONOSPACE"}, 2},
		{"nested", "outer inner end", []string{"0:15:BOLD", "6:5:MONOSPACE"}, 2},
		{"literal markers are escaped", "2*3 = `x` ~y~ a||b \\ done", []string{"0:3:BOLD"}, 1},
		{"table in monospace", "| a | b |\n|---|---|", []string{"0:19:MONOSPACE"}, 1},
		{"emoji offsets", "🚀 launch 🚀 ok", []string{"3:6:BOLD", "12:2:ITALIC"}, 2},
		{"crossing ranges keep the first", "abcdef", []string{"0:4:BOLD", "2:4:ITALIC"}, 1},
		{"italic right after a literal star", "a*b", []string{"2:1:ITALIC"}, 0},
		{"marker after a backslash", "path\\x", []string{"5:1:MONOSPACE"}, 0},
		{"touching star markers", "abcd", []string{"0:2:ITALIC", "2:2:ITALIC"}, 1},
		{"out of range and malformed", "abc", []string{"2:5:BOLD", "x:1:BOLD", "0:1:UNDERLINE", "0:0:BOLD"}, 0},
		{"split surrogate pair", "🚀x", []string{"1:2:BOLD"}, 0},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			if got := checkRoundTrip(t, c.text, c.styles); len(got) != c.keep {
				t.Fatalf("kept %d styles %v, want %d", len(got), got, c.keep)
			}
		})
	}
}

func TestStyledMessageRandomRoundTrips(t *testing.T) {
	rng := rand.New(rand.NewSource(1))
	alphabet := []rune("ab *`~|\\\n🚀é")
	kinds := []string{"BOLD", "ITALIC", "STRIKETHROUGH", "MONOSPACE", "SPOILER"}
	for n := 0; n < 20000; n++ {
		runes := make([]rune, 1+rng.Intn(24))
		for i := range runes {
			runes[i] = alphabet[rng.Intn(len(alphabet))]
		}
		text := string(runes)
		size := len(utf16.Encode(runes))
		var styles []string
		for i := rng.Intn(5); i > 0; i-- {
			start := rng.Intn(size)
			length := 1 + rng.Intn(size-start)
			styles = append(styles, strconv.Itoa(start)+":"+strconv.Itoa(length)+":"+kinds[rng.Intn(len(kinds))])
		}
		checkRoundTrip(t, text, styles)
	}
}

func TestSendForwardsTextStylesAsStyledText(t *testing.T) {
	f := newFakeREST(t)
	h := (&shim{upstream: f.srv.URL, http: f.srv.Client()}).routes()

	callRPC(t, h, "send", map[string]any{"account": "+491", "message": "Deploy done: 2*3 ok", "recipient": []any{"+492"}, "textStyles": []any{"0:6:BOLD", "13:6:MONOSPACE"}})

	got := f.last().body
	if got["text_mode"] != "styled" || got["message"] != "**Deploy** done: `2\\*3 ok`" {
		t.Fatalf("send body = %v", got)
	}
}

func TestSendWithoutStylesStaysPlain(t *testing.T) {
	f := newFakeREST(t)
	h := (&shim{upstream: f.srv.URL, http: f.srv.Client()}).routes()

	callRPC(t, h, "send", map[string]any{"account": "+491", "message": "a*b", "recipient": []any{"+492"}, "textStyle": "1:1:BOLD"})

	got := f.last().body
	if _, styled := got["text_mode"]; styled || got["message"] != "a*b" {
		t.Fatalf("send body = %v, want the plain message without text_mode", got)
	}
}
