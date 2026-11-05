package main

import (
	"fmt"
	"net/http"
	"regexp"
	"strconv"
	"strings"
)

var (
	gqlRepository = regexp.MustCompile(`repository\s*\(([^)]*)\)`)
	gqlNode       = regexp.MustCompile(`\b(pullRequest|issue)\s*\(([^)]*)\)`)
	gqlEdits      = regexp.MustCompile(`userContentEdits\s*\(([^)]*)\)`)
	gqlArg        = regexp.MustCompile(`(\w+)\s*:\s*(\$\w+|"[^"]*"|-?\d+)`)
)

// graphql answers only repository{pullRequest|issue(number){… userContentEdits(first|last, after)}} queries.
func (g *fakeGitHub) graphql(c *fakeReq) {
	if g.gqlError != "" {
		c.send(http.StatusOK, map[string]any{"data": nil, "errors": []any{map[string]any{"message": g.gqlError}}})
		return
	}
	query, _ := c.str("query")
	vars, _ := c.body["variables"].(map[string]any)
	args := func(src string) map[string]string {
		out := map[string]string{}
		for _, m := range gqlArg.FindAllStringSubmatch(src, -1) {
			v := m[2]
			if strings.HasPrefix(v, "$") {
				v = fmt.Sprint(vars[v[1:]])
			}
			out[m[1]] = strings.Trim(v, `"`)
		}
		return out
	}
	repoM, nodeM := gqlRepository.FindStringSubmatch(query), gqlNode.FindStringSubmatch(query)
	if repoM == nil || nodeM == nil {
		g.t.Errorf("fake GraphQL cannot answer %q", query)
		c.send(http.StatusOK, map[string]any{"errors": []any{map[string]any{"message": "unsupported query"}}})
		return
	}
	ra, na := args(repoM[1]), args(nodeM[2])
	kind := nodeM[1]
	r, ok := g.repos[strings.ToLower(ra["owner"]+"/"+ra["name"])]
	if !ok {
		c.send(http.StatusOK, gqlNotFound(nil, "Could not resolve to a Repository with the name '"+ra["owner"]+"/"+ra["name"]+"'.", "repository"))
		return
	}
	n, _ := strconv.Atoi(na["number"])
	is, ok := r.issues[n]
	if !ok || (kind == "pullRequest") != (is.pull != nil) {
		typ := map[string]string{"pullRequest": "PullRequest", "issue": "Issue"}[kind]
		c.send(http.StatusOK, gqlNotFound(map[string]any{"repository": map[string]any{kind: nil}}, fmt.Sprintf("Could not resolve to %s %s with the number of %d.", article(typ), typ, n), "repository", kind))
		return
	}
	node := map[string]any{
		"number": is.number, "title": is.title, "body": is.body, "createdAt": ts(is.createdAt), "updatedAt": ts(is.updatedAt),
		"lastEditedAt": tsPtr(is.lastEditedAt),
	}
	if em := gqlEdits.FindStringSubmatch(query); em != nil {
		conn, msg := g.editsConnection(is, args(em[1]))
		if msg != "" {
			c.send(http.StatusOK, map[string]any{"errors": []any{map[string]any{"message": msg}}})
			return
		}
		node["userContentEdits"] = conn
	}
	c.send(http.StatusOK, map[string]any{"data": map[string]any{"repository": map[string]any{kind: node}}})
}

func article(typ string) string {
	if strings.HasPrefix(typ, "I") {
		return "an"
	}
	return "a"
}

func gqlNotFound(data map[string]any, message string, path ...string) map[string]any {
	p := []any{}
	for _, s := range path {
		p = append(p, s)
	}
	return map[string]any{"data": data, "errors": []any{map[string]any{"type": "NOT_FOUND", "path": p, "message": message}}}
}

func (g *fakeGitHub) editsConnection(is *fakeIssue, a map[string]string) (map[string]any, string) {
	newest := make([]*fakeEdit, 0, len(is.edits))
	for i := len(is.edits) - 1; i >= 0; i-- {
		newest = append(newest, is.edits[i])
	}
	first, hasFirst := a["first"]
	last, hasLast := a["last"]
	if !hasFirst && !hasLast {
		return nil, "You must provide a `first` or `last` value to properly paginate the `userContentEdits` connection."
	}
	start, end := 0, len(newest)
	if after, ok := a["after"]; ok && after != "" && after != "<nil>" {
		idx, err := strconv.Atoi(strings.TrimPrefix(after, "cursor:"))
		if err != nil {
			return nil, "invalid cursor"
		}
		start = idx + 1
	}
	if start > end {
		start = end
	}
	if hasFirst {
		k, _ := strconv.Atoi(first)
		if k < 0 || k > 100 {
			return nil, "first must be between 0 and 100"
		}
		if start+k < end {
			end = start + k
		}
	} else {
		k, _ := strconv.Atoi(last)
		if end-k > start {
			start = end - k
		}
	}
	nodes := []any{}
	for i := start; i < end; i++ {
		e := newest[i]
		var deletedBy any
		if e.deletedBy != "" {
			deletedBy = user(e.deletedBy)
		}
		var diff any = "diff"
		if e.deletedAt != nil {
			diff = nil
		}
		nodes = append(nodes, map[string]any{
			"id": fmt.Sprintf("UCE_%d", e.id), "editedAt": ts(e.editedAt), "createdAt": ts(e.createdAt), "updatedAt": ts(e.createdAt),
			"deletedAt": tsPtr(e.deletedAt), "deletedBy": deletedBy, "editor": user(e.editor), "diff": diff,
		})
	}
	var endCursor, startCursor any
	if end > start {
		startCursor, endCursor = fmt.Sprintf("cursor:%d", start), fmt.Sprintf("cursor:%d", end-1)
	}
	return map[string]any{
		"totalCount": len(newest), "nodes": nodes,
		"pageInfo": map[string]any{"hasNextPage": end < len(newest), "hasPreviousPage": start > 0, "endCursor": endCursor, "startCursor": startCursor},
	}, ""
}
