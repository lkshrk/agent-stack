package main

import (
	"sort"
	"strconv"
	"strings"
	"unicode/utf16"
)

var styleMarkers = map[string]string{
	"BOLD":          "**",
	"ITALIC":        "*",
	"STRIKETHROUGH": "~",
	"MONOSPACE":     "`",
	"SPOILER":       "||",
}

type textRange struct {
	start, end, order int
	style             string
}

// styledMessage renders signal-cli textStyle ranges ("start:length:STYLE", UTF-16 offsets) as the
// marker syntax signal-cli-rest-api parses in text_mode "styled". ok is false when no range survives.
func styledMessage(text string, styles []string) (string, bool) {
	units := utf16.Encode([]rune(text))
	ranges := keepRenderable(units, parseRanges(units, styles))
	if len(ranges) == 0 {
		return "", false
	}

	opens := map[int][]textRange{}
	closes := map[int][]textRange{}
	for _, r := range ranges {
		opens[r.start] = append(opens[r.start], r)
		closes[r.end] = append(closes[r.end], r)
	}
	for _, rs := range opens {
		sort.Slice(rs, func(i, j int) bool {
			if rs[i].end != rs[j].end {
				return rs[i].end > rs[j].end
			}
			return rs[i].order < rs[j].order
		})
	}
	for _, rs := range closes {
		sort.Slice(rs, func(i, j int) bool {
			if rs[i].start != rs[j].start {
				return rs[i].start > rs[j].start
			}
			return rs[i].order > rs[j].order
		})
	}

	var b strings.Builder
	for i := 0; i <= len(units); i++ {
		for _, r := range closes[i] {
			b.WriteString(styleMarkers[r.style])
		}
		for _, r := range opens[i] {
			b.WriteString(styleMarkers[r.style])
		}
		if i == len(units) {
			break
		}
		n := 1
		if isHigh(units[i]) && i+1 < len(units) {
			n = 2
		}
		c := string(utf16.Decode(units[i : i+n]))
		if strings.ContainsAny(c, "*`~|") {
			b.WriteByte('\\')
		}
		b.WriteString(c)
		i += n - 1
	}
	return b.String(), true
}

func parseRanges(units []uint16, styles []string) []textRange {
	var out []textRange
	for _, s := range styles {
		parts := strings.SplitN(s, ":", 3)
		if len(parts) != 3 {
			continue
		}
		start, err1 := strconv.Atoi(parts[0])
		length, err2 := strconv.Atoi(parts[1])
		if err1 != nil || err2 != nil || start < 0 || length <= 0 || start+length > len(units) {
			continue
		}
		if _, ok := styleMarkers[parts[2]]; !ok {
			continue
		}
		if splitsPair(units, start) || splitsPair(units, start+length) {
			continue
		}
		out = append(out, textRange{start: start, end: start + length, style: parts[2]})
	}
	return out
}

func isHigh(u uint16) bool { return u >= 0xD800 && u < 0xDC00 }

func splitsPair(units []uint16, i int) bool {
	return i > 0 && i < len(units) && isHigh(units[i-1])
}

// keepRenderable drops ranges the styled parser would misread: crossing ranges, a style overlapping
// itself, a marker the parser would read as escaped, or two `*` markers touching.
func keepRenderable(units []uint16, ranges []textRange) []textRange {
	sort.SliceStable(ranges, func(i, j int) bool {
		if ranges[i].start != ranges[j].start {
			return ranges[i].start < ranges[j].start
		}
		return ranges[i].end > ranges[j].end
	})
	var kept []textRange
	stars := map[int][]textRange{}
	for _, r := range ranges {
		marker := styleMarkers[r.style]
		if swallowed(units, r.start, marker) || swallowed(units, r.end, marker) || !nests(kept, r) {
			continue
		}
		star := marker[0] == '*'
		if star && (!starsFit(stars[r.start], r) || !starsFit(stars[r.end], r)) {
			continue
		}
		r.order = len(kept)
		kept = append(kept, r)
		if star {
			stars[r.start] = append(stars[r.start], r)
			stars[r.end] = append(stars[r.end], r)
		}
	}
	return kept
}

// swallowed reports whether the text unit before position i would make the parser read marker as literal text.
func swallowed(units []uint16, i int, marker string) bool {
	if i == 0 {
		return false
	}
	switch units[i-1] {
	case '\\':
		return true
	case '*':
		return marker[0] == '*'
	case '|':
		return marker == "||"
	}
	return false
}

func nests(kept []textRange, r textRange) bool {
	for _, k := range kept {
		disjoint := r.end <= k.start || k.end <= r.start
		if k.style == r.style && !disjoint {
			return false
		}
		inside := k.start <= r.start && r.end <= k.end
		around := r.start <= k.start && k.end <= r.end
		if !disjoint && !inside && !around {
			return false
		}
	}
	return true
}

// starsFit allows one `*` marker per boundary, except BOLD and ITALIC on the same span, which render as `***`.
func starsFit(at []textRange, r textRange) bool {
	switch len(at) {
	case 0:
		return true
	case 1:
		k := at[0]
		return k.start == r.start && k.end == r.end && k.style != r.style
	}
	return false
}
