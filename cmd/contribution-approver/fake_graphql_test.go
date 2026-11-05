package main

import (
	"fmt"
	"net/http"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"time"
)

var fakeGQLArg = regexp.MustCompile(`(\w+)\s*:\s*(\$\w+|"[^"]*"|-?\d+)`)

type fakeGQLField struct {
	args string
	sub  fakeGQLSel
}

type fakeGQLSel map[string]*fakeGQLField

func fakeGQLParse(q string) (fakeGQLSel, error) {
	depth, i := 0, 0
	for ; i < len(q); i++ {
		switch q[i] {
		case '(':
			depth++
		case ')':
			depth--
		}
		if q[i] == '{' && depth == 0 {
			break
		}
	}
	sel, _, err := fakeGQLBlock(q, i)
	return sel, err
}

func fakeGQLSkip(q string, i int, set string) int {
	for i < len(q) && strings.IndexByte(set, q[i]) >= 0 {
		i++
	}
	return i
}

func fakeGQLBlock(q string, i int) (fakeGQLSel, int, error) {
	if i >= len(q) || q[i] != '{' {
		return nil, i, fmt.Errorf("expected { at %d", i)
	}
	sel := fakeGQLSel{}
	i++
	for {
		i = fakeGQLSkip(q, i, " \t\r\n,")
		if i >= len(q) {
			return nil, i, fmt.Errorf("unterminated selection")
		}
		if q[i] == '}' {
			return sel, i + 1, nil
		}
		start := i
		i = fakeGQLSkip(q, i, "_abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
		if start == i {
			return nil, i, fmt.Errorf("unsupported syntax at %q", q[i:])
		}
		name, f := q[start:i], &fakeGQLField{}
		i = fakeGQLSkip(q, i, " \t\r\n")
		if i < len(q) && q[i] == ':' {
			return nil, i, fmt.Errorf("aliases are not supported (%s)", name)
		}
		if i < len(q) && q[i] == '(' {
			end := strings.IndexByte(q[i:], ')')
			if end < 0 {
				return nil, i, fmt.Errorf("unterminated arguments")
			}
			f.args, i = q[i+1:i+end], i+end+1
		}
		i = fakeGQLSkip(q, i, " \t\r\n")
		if i < len(q) && q[i] == '{' {
			sub, next, err := fakeGQLBlock(q, i)
			if err != nil {
				return nil, next, err
			}
			f.sub, i = sub, next
		}
		sel[name] = f
	}
}

func fakeGQLFilter(v any, sel fakeGQLSel, path string) (any, error) {
	switch val := v.(type) {
	case nil:
		return nil, nil
	case []any:
		out := []any{}
		for _, item := range val {
			f, err := fakeGQLFilter(item, sel, path)
			if err != nil {
				return nil, err
			}
			out = append(out, f)
		}
		return out, nil
	case map[string]any:
		if sel == nil {
			return nil, fmt.Errorf("%s needs a selection", path)
		}
		out := map[string]any{}
		for name, f := range sel {
			field, ok := val[name]
			if !ok {
				return nil, fmt.Errorf("the fake does not model %s.%s", path, name)
			}
			filtered, err := fakeGQLFilter(field, f.sub, path+"."+name)
			if err != nil {
				return nil, err
			}
			out[name] = filtered
		}
		return out, nil
	default:
		if sel != nil {
			return nil, fmt.Errorf("%s is a scalar", path)
		}
		return v, nil
	}
}

// fakeGQLRequired fails queries that could miss an edit: lastEditedAt, every edit's editedAt and deletedAt, and pageInfo when paging.
func fakeGQLRequired(node fakeGQLSel, edits map[string]string) error {
	if node["lastEditedAt"] == nil {
		return fmt.Errorf("the query omits lastEditedAt")
	}
	e := node["userContentEdits"]
	if e == nil {
		return nil
	}
	if e.sub["nodes"] == nil || e.sub["nodes"].sub["editedAt"] == nil || e.sub["nodes"].sub["deletedAt"] == nil {
		return fmt.Errorf("userContentEdits must select nodes{editedAt deletedAt}")
	}
	size, _ := strconv.Atoi(edits["first"] + edits["last"])
	if _, after := edits["after"]; after || size > 1 {
		if p := e.sub["pageInfo"]; p == nil || p.sub["hasNextPage"] == nil || p.sub["endCursor"] == nil {
			return fmt.Errorf("a paginated userContentEdits must select pageInfo{hasNextPage endCursor}")
		}
	}
	return nil
}

func (g *fakeGitHub) graphql(c *fakeReq) {
	if g.gqlError != "" {
		c.send(http.StatusOK, map[string]any{"data": nil, "errors": []any{map[string]any{"message": g.gqlError}}})
		return
	}
	query, _ := c.str("query")
	vars, _ := c.body["variables"].(map[string]any)
	args := func(src string) map[string]string {
		out := map[string]string{}
		for _, m := range fakeGQLArg.FindAllStringSubmatch(src, -1) {
			v := m[2]
			if strings.HasPrefix(v, "$") {
				val, ok := vars[v[1:]]
				if !ok || val == nil {
					continue
				}
				v = fmt.Sprint(val)
			}
			out[m[1]] = strings.Trim(v, `"`)
		}
		return out
	}
	reject := func(err error) {
		g.t.Errorf("fake GraphQL cannot answer %q: %v", query, err)
		c.send(http.StatusOK, map[string]any{"errors": []any{map[string]any{"message": err.Error()}}})
	}
	root, err := fakeGQLParse(query)
	if err != nil {
		reject(err)
		return
	}
	repoSel := root["repository"]
	if len(root) != 1 || repoSel == nil || len(repoSel.sub) != 1 {
		reject(fmt.Errorf("only repository{pullRequest|issue} is supported"))
		return
	}
	var kind string
	var nodeSel *fakeGQLField
	for k, v := range repoSel.sub {
		kind, nodeSel = k, v
	}
	if kind == "label" {
		g.labelNode(c, repoSel, nodeSel, args)
		return
	}
	if kind != "pullRequest" && kind != "issue" {
		reject(fmt.Errorf("unsupported repository field %s", kind))
		return
	}
	var editArgs map[string]string
	if e := nodeSel.sub["userContentEdits"]; e != nil {
		editArgs = args(e.args)
	}
	if err := fakeGQLRequired(nodeSel.sub, editArgs); err != nil {
		reject(err)
		return
	}
	ra, na := args(repoSel.args), args(nodeSel.args)
	r, ok := g.repos[strings.ToLower(ra["owner"]+"/"+ra["name"])]
	if !ok {
		c.send(http.StatusOK, fakeGQLNotFound(map[string]any{"repository": nil}, "Could not resolve to a Repository with the name '"+ra["owner"]+"/"+ra["name"]+"'.", "repository"))
		return
	}
	n, _ := strconv.Atoi(na["number"])
	is, ok := r.issues[n]
	if !ok || (kind == "pullRequest") != (is.pull != nil) {
		typ := map[string]string{"pullRequest": "PullRequest", "issue": "Issue"}[kind]
		c.send(http.StatusOK, fakeGQLNotFound(map[string]any{"repository": map[string]any{kind: nil}}, fmt.Sprintf("Could not resolve to %s %s with the number of %d.", fakeArticle(typ), typ, n), "repository", kind))
		return
	}
	node := map[string]any{
		"id": fmt.Sprintf("NODE_%d", is.number), "number": is.number, "title": is.title, "body": is.body,
		"createdAt": fakeTS(is.createdAt), "updatedAt": fakeTS(is.updatedAt), "lastEditedAt": fakeTSPtr(is.lastEditedAt),
		"includesCreatedEdit": len(is.edits) > 0, "author": fakeUser(is.user),
	}
	if editArgs != nil {
		conn, msg := g.editsConnection(is, editArgs)
		if msg != "" {
			c.send(http.StatusOK, map[string]any{"errors": []any{map[string]any{"message": msg}}})
			return
		}
		node["userContentEdits"] = conn
	}
	filtered, err := fakeGQLFilter(node, nodeSel.sub, kind)
	if err != nil {
		reject(err)
		return
	}
	c.send(http.StatusOK, map[string]any{"data": map[string]any{"repository": map[string]any{kind: filtered}}})
}

func fakeArticle(typ string) string {
	if strings.HasPrefix(typ, "I") {
		return "an"
	}
	return "a"
}

func fakeGQLNotFound(data map[string]any, message string, path ...string) map[string]any {
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
	if after, ok := a["after"]; ok && after != "" {
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
		var diff any = e.body
		if e.deletedAt != nil {
			diff = nil
		}
		nodes = append(nodes, map[string]any{
			"id": fmt.Sprintf("UCE_%d", e.id), "editedAt": fakeTS(e.editedAt), "createdAt": fakeTS(e.createdAt), "updatedAt": fakeTS(e.createdAt),
			"deletedAt": fakeTSPtr(e.deletedAt), "deletedBy": fakeUser(e.deletedBy), "editor": fakeUser(e.editor), "diff": diff,
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

func (g *fakeGitHub) labelNode(c *fakeReq, repoSel, nodeSel *fakeGQLField, args func(string) map[string]string) {
	ra := args(repoSel.args)
	r, ok := g.repos[strings.ToLower(ra["owner"]+"/"+ra["name"])]
	if !ok {
		c.send(http.StatusOK, fakeGQLNotFound(map[string]any{"repository": nil}, "Could not resolve to a Repository.", "repository"))
		return
	}
	name := args(nodeSel.args)["name"]
	at, ok := r.labelUpdated[name]
	if !ok {
		for _, is := range r.issues {
			if slices.Contains(is.labels, name) {
				at, ok = time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC), true
			}
		}
	}
	if !ok {
		c.send(http.StatusOK, map[string]any{"data": map[string]any{"repository": map[string]any{"label": nil}}})
		return
	}
	node := map[string]any{"id": "LA_" + name, "name": name, "createdAt": "2026-01-01T00:00:00Z", "updatedAt": fakeTS(at)}
	filtered, err := fakeGQLFilter(node, nodeSel.sub, "label")
	if err != nil {
		g.t.Errorf("fake GraphQL cannot answer: %v", err)
		c.send(http.StatusOK, map[string]any{"errors": []any{map[string]any{"message": err.Error()}}})
		return
	}
	res := map[string]any{"data": map[string]any{"repository": map[string]any{"label": filtered}}}
	if g.gqlLabelError != "" {
		res["errors"] = []any{map[string]any{"message": g.gqlLabelError}}
	}
	c.send(http.StatusOK, res)
}
