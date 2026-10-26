#!/usr/bin/env python3
"""Read-only, bounded Raider.IO consistency diagnostic; never certifies run provenance."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import subprocess
from urllib.parse import urlencode


def fetch(endpoint, **params):
    url = "https://raider.io/api/v1/mythic-plus/" + endpoint + "?" + urlencode(params)
    result = subprocess.run(
        ["curl", "--fail", "--silent", "--show-error", "--location",
         "--max-time", "15", url], capture_output=True, text=True, check=True,
    )
    return json.loads(result.stdout)


def compare(record, detail, spec):
    """Require identity, content, spec and export agreement before counting nodes."""
    run, player = record
    if (detail.get("keystone_run_id") != run["keystone_run_id"]
            or detail.get("season") != run["season"]
            or detail.get("dungeon", {}).get("slug") != run["dungeon"]["slug"]):
        return "wrong_run", None
    member = next((r for r in detail.get("roster", [])
                   if r.get("character", {}).get("id") == player["character"]["id"]), {})
    talent = member.get("character", {}).get("talentLoadout") or {}
    if talent.get("specId") != spec:
        return "missing_or_wrong_spec", None
    export = talent.get("exportLoadoutText")
    if not export or not player.get("loadout") or not talent.get("loadout"):
        return "missing_talents", None
    if export != player["loadout"]:
        return "unresolved_export_disagreement", None
    if not talent.get("dbcIndexVersion"):
        return "missing_tree_version", None
    nodes = []
    for selection in talent["loadout"]:
        if selection["rank"] > 0:
            node = selection["node"]
            entry = node["entries"][selection["entryIndex"]]
            nodes.append((node["id"], entry["id"], selection["rank"]))
    key = (talent["dbcIndexVersion"], spec, talent.get("heroSubTreeId"), tuple(sorted(nodes)))
    return "consistent_not_proven_historical", (key, export)


def self_test():
    import copy
    run = {"keystone_run_id": 1, "season": "test", "dungeon": {"slug": "test"}}
    player = {"character": {"id": 2}, "loadout": "fixture-A"}
    talent = {"specId": 71, "dbcIndexVersion": "test", "heroSubTreeId": 60,
              "exportLoadoutText": "fixture-A", "loadout": [
                  {"node": {"id": 3, "entries": [{"id": 4}]}, "entryIndex": 0, "rank": 1},
                  {"node": {"id": 5, "entries": [{"id": 6}]}, "entryIndex": 0, "rank": 2}]}
    detail = dict(run, roster=[{"character": {"id": 2, "talentLoadout": talent}}])
    status, matched = compare((run, player), detail, 71)
    assert status == "consistent_not_proven_historical" and matched[1] == "fixture-A"
    changed = copy.deepcopy(detail)
    changed["roster"][0]["character"]["talentLoadout"]["exportLoadoutText"] = "fixture-B"
    assert compare((run, player), changed, 71) == ("unresolved_export_disagreement", None)
    assert compare((run, player), dict(detail, keystone_run_id=9), 71)[0] == "wrong_run"
    assert compare((run, player), detail, 72)[0] == "missing_or_wrong_spec"
    reordered = copy.deepcopy(detail)
    reordered["roster"][0]["character"]["talentLoadout"]["loadout"].reverse()
    assert compare((run, player), reordered, 71) == (status, matched)
    print("ok: matching, conflicting, wrong-run and wrong-spec snapshots")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--season")
    parser.add_argument("--dungeon")
    parser.add_argument("--since", help="Inclusive UTC date, YYYY-MM-DD")
    parser.add_argument("--spec", type=int)
    parser.add_argument("--characters", type=int, default=50)
    parser.add_argument("--pages", type=int, default=20)
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    if not all((args.season, args.dungeon, args.since, args.spec)):
        parser.error("season, dungeon, since and spec are required")
    since = datetime.strptime(args.since, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    if not 1 <= args.characters <= 50 or not 1 <= args.pages <= 100:
        parser.error("characters must be 1..50 and pages 1..100")
    selected, seen_runs = {}, set()
    page_count = 0
    # Retain these exact responses; never refetch the leaderboard during analysis.
    for page in range(args.pages):
        response = fetch("runs", season=args.season, dungeon=args.dungeon, region="world", page=page)
        rows = response["rankings"]
        page_count += 1
        for row in rows:
            run = row["run"]
            if run["keystone_run_id"] in seen_runs:
                continue
            seen_runs.add(run["keystone_run_id"])
            if (run.get("num_chests", 0) < 1 or run["season"] != args.season
                    or run["dungeon"]["slug"] != args.dungeon
                    or datetime.fromisoformat(run["completed_at"].replace("Z", "+00:00")) < since):
                continue
            for player in run["roster"]:
                character = player["character"]
                if character.get("spec", {}).get("id") == args.spec and len(selected) < args.characters:
                    selected.setdefault(character["id"], (run, player))
        if len(selected) >= args.characters or not rows:
            break
    statuses, groups, records = Counter(), {}, []
    for identity, record in selected.items():
        run, player = record
        try:
            detail = fetch("run-details", season=args.season, id=run["keystone_run_id"])
            status, result = compare(record, detail, args.spec)
        except (subprocess.SubprocessError, ValueError, KeyError, IndexError, TypeError) as error:
            if isinstance(error, subprocess.CalledProcessError) and "429" in (error.stderr or ""):
                raise SystemExit("Raider.IO rate limited this sample; stopped without further requests.")
            status, result = "request_or_schema_failure", None
        statuses[status] += 1
        records.append({"character_id": identity, "run_id": run["keystone_run_id"],
                        "completed_at": run["completed_at"], "key": run["mythic_level"], "status": status})
        if result:
            key, export = result
            group = groups.setdefault(key, {"count": 0, "tree_version": key[0],
                                            "hero_tree_id": key[2], "import_string": export,
                                            "example_run": run["keystone_run_id"]})
            group["count"] += 1
    assert sum(statuses.values()) == len(selected)
    assert sum(g["count"] for g in groups.values()) == statuses["consistent_not_proven_historical"]
    print(json.dumps({"checked_at": datetime.now(timezone.utc).isoformat(),
                      "query": vars(args), "pages": page_count, "runs_inspected": len(seen_runs),
                      "characters": len(selected), "statuses": statuses,
                      "provenance": "unverified; consistency is not proof of run-time talents",
                      "groups": sorted(groups.values(), key=lambda g: -g["count"]),
                      "records": records}, indent=2))


if __name__ == "__main__":
    main()
