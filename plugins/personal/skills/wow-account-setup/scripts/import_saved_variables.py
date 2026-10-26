#!/usr/bin/env python3
"""Import supplied WoW saved data; no Lua execution, network or vault-task edits."""
import argparse
import copy
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile

KIND = "wow-account-observations"
GUID = re.compile(r"Player-\d+-[0-9A-Fa-f]+\Z")
NUMBER = r"(?:0[xX][0-9a-fA-F]+|(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)"
TOKEN = re.compile(r'''\s+|--[^\n]*|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|''' + NUMBER + r"|[A-Za-z_][A-Za-z_0-9]*|[{}\[\]=,;+-]", re.S)


def parse_lua(text):
    """Accept only assignments of literal tables/scalars emitted by saved variables."""
    text = text.lstrip("\ufeff")
    tokens, offset = [], 0
    for match in TOKEN.finditer(text):
        if match.start() != offset:
            raise ValueError(f"Unsupported Lua syntax at offset {offset}")
        offset = match.end()
        token = match.group()
        if not token.isspace() and not token.startswith("--"):
            tokens.append(token)
    if offset != len(text):
        raise ValueError(f"Unsupported Lua syntax at offset {offset}")
    index = 0

    def take(expected=None):
        nonlocal index
        if index >= len(tokens):
            raise ValueError("Incomplete Lua data")
        token = tokens[index]
        index += 1
        if expected is not None and token != expected:
            raise ValueError(f"Expected {expected!r}, got {token!r}")
        return token

    def string(token):
        # Lua decimal escapes are bytes, including multi-byte UTF-8 names.
        output = bytearray()
        escapes = {"n": 10, "r": 13, "t": 9, "a": 7, "b": 8, "f": 12, "v": 11, "\\": 92, '"': 34, "'": 39}
        for match in re.finditer(r"\\(\d{1,3}|.)|([^\\]+)", token[1:-1], re.S):
            escaped, plain = match.groups()
            if plain is not None:
                output.extend(plain.encode("utf-8"))
            elif escaped.isdigit() and int(escaped) <= 255:
                output.append(int(escaped))
            elif escaped in escapes:
                output.append(escapes[escaped])
            else:
                raise ValueError("Unsupported Lua string escape")
        return output.decode("utf-8")

    def value(depth=0):
        if depth > 100:
            raise ValueError("Lua data exceeds maximum nesting depth")
        token = take()
        if token == "{":
            result, ordinal = {}, 1
            while index < len(tokens) and tokens[index] != "}":
                if tokens[index] == "[":
                    take("["); key = value(depth + 1); take("]"); take("=")
                elif index + 1 < len(tokens) and tokens[index + 1] == "=":
                    key = take(); take("=")
                else:
                    key, ordinal = ordinal, ordinal + 1
                if type(key) not in (str, int) or key in result:
                    raise ValueError("Unsupported or duplicate Lua table key")
                result[key] = value(depth + 1)
                if index < len(tokens) and tokens[index] in (",", ";"):
                    take()
                elif index < len(tokens) and tokens[index] != "}":
                    raise ValueError("Expected table separator")
            take("}")
            return result
        if token.startswith(('"', "'")):
            return string(token)
        if token in ("true", "false", "nil"):
            return {"true": True, "false": False, "nil": None}[token]
        if token in ("-", "+"):
            number = value(depth + 1)
            if type(number) not in (int, float):
                raise ValueError("Sign before non-number")
            return -number if token == "-" else number
        if re.fullmatch(NUMBER, token):
            number = int(token, 16) if token.lower().startswith("0x") else float(token) if any(c in token for c in ".eE") else int(token)
            if not math.isfinite(number):
                raise ValueError("Non-finite number")
            return number
        raise ValueError("Non-literal Lua expression")

    result = {}
    while index < len(tokens):
        key = take()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", key) or key in result:
            raise ValueError("Invalid or duplicate root assignment")
        take("="); result[key] = value()
        if index < len(tokens) and tokens[index] == ";":
            take()
    return result


def read_lua(path):
    path = Path(path)
    before = path.stat()
    if before.st_size > 64 * 1024 * 1024:
        raise ValueError("Input exceeds 64 MiB; supply a bounded saved-variable export")
    raw = path.read_bytes()
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError("Source changed during read; finish logout and retry")
    return parse_lua(raw.decode("utf-8-sig")), {"filename": path.name, "sha256": hashlib.sha256(raw).hexdigest()}


def timestamp(value):
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
        return None
    try:
        return dt.datetime.fromtimestamp(value, dt.timezone.utc).isoformat()
    except (ValueError, OSError, OverflowError):
        return None


def table(value, label="value"):
    if not isinstance(value, dict):
        raise ValueError(f"Expected a table for {label}")
    return value


def positive_id(value):
    if type(value) is int and value > 0:
        return str(value)
    if isinstance(value, str) and value.isdecimal() and int(value) > 0:
        return str(int(value))
    raise ValueError("Invalid entity ID")


def array(value):
    value = table(value)
    if set(value) != set(range(1, len(value) + 1)):
        raise ValueError("Expected a contiguous Lua array")
    return [value[i] for i in range(1, len(value) + 1)]


def reputations(raw):
    result = {}
    for encoded in array(raw):
        if not isinstance(encoded, str) or not re.fullmatch(r"\d+:-?\d+", encoded):
            raise ValueError("Unsupported reputation encoding")
        faction, amount = encoded.split(":")
        faction = positive_id(faction)
        if faction in result and result[faction]["value"] != int(amount):
            raise ValueError("Conflicting faction values")
        result[faction] = {"value": int(amount), "scale": "collector-specific; resolve faction/renown definition"}
    return result


def currencies(raw):
    result = {}
    names = ["quantity", "maximum", "weekly_cap_enabled", "earned_this_week", "weekly_maximum", "uses_total_earned", "total_earned"]
    for key, encoded in table(raw).items():
        if key == 0 and encoded == "0:0:0:0:0:0:0":
            continue  # Collector's no-currency sentinel is not a real currency.
        if not isinstance(encoded, str) or not re.fullmatch(r"\d+(?::\d+){6}", encoded):
            raise ValueError("Unsupported currency encoding")
        values = list(map(int, encoded.split(":")))
        if values[2] not in (0, 1) or values[5] not in (0, 1):
            raise ValueError("Invalid currency flags")
        result[positive_id(key)] = dict(zip(names, values))
    return result


def professions(raw):
    result = {}
    for entry in array(raw):
        entry = table(entry)
        key = positive_id(entry.get("id"))
        if key in result:
            raise ValueError("Duplicate profession record")
        if any(entry.get(k) is not None and (type(entry[k]) not in (int, float) or entry[k] < 0) for k in ("currentSkill", "maxSkill")):
            raise ValueError("Invalid profession skill value")
        result[key] = {
            "current_skill": entry.get("currentSkill"), "max_skill": entry.get("maxSkill"),
            "known_skill_line_ability_ids": [int(positive_id(x)) for x in array(entry.get("knownRecipes", {}))],
        }
    return result


def achievements(raw):
    result = {}
    for entry in array(raw):
        entry = table(entry)
        earned = entry.get("earned")
        if earned is not None and type(earned) is not bool:
            raise ValueError("Invalid character achievement flag")
        key = positive_id(entry.get("id"))
        if key in result:
            raise ValueError("Duplicate character achievement record")
        result[key] = {
            "state": "completed" if earned is True else "incomplete" if earned is False else "unknown",
            "criteria": array(entry.get("criteria", {})),
        }
    return result


def inventory(raw):
    result = {}
    for bag, slots in table(raw).items():
        bag_items = {}
        for slot, encoded in table(slots).items():
            if not isinstance(encoded, str):
                raise ValueError("Unsupported inventory encoding")
            parts = encoded.split(":")
            if parts[0] == "pet":
                bag_items[str(slot)] = {"kind": "caged_pet", "raw": encoded}
            elif len(parts) == 12 and parts[0].isdecimal():
                bag_items[str(slot)] = {"item_id": int(positive_id(parts[1])), "quantity": int(parts[0]), "raw": encoded}
            else:
                raise ValueError("Unsupported item-link encoding")
        result[str(bag)] = bag_items
    return result


def observation(owner, key, decoder, clocks, source, scope, note):
    if key not in owner or owner[key] is None:
        return None
    clocks = {k: timestamp(v) for k, v in clocks.items() if timestamp(v)}
    item = {"status": "partial", "scope": scope, "observed_at": next(iter(clocks.values())) if len(clocks) == 1 else None,
            "scan_times": clocks, "source": source, "note": note, "present_in_latest_import": True}
    try:
        item["values"] = decoder(owner[key])
    except (ValueError, TypeError, KeyError) as error:
        item.update(status="unsupported", values=None, issue=str(error), raw=owner[key])
    return item


def scalar(value):
    if type(value) not in (str, int, float, bool):
        raise ValueError("Expected a scalar observation")
    return value


def merge_observation(old, new):
    if old is None:
        return new
    if new is None:
        return {**copy.deepcopy(old), "present_in_latest_import": False}
    if new.get("status") == "unsupported" and old.get("values") is not None:
        return {**copy.deepcopy(old), "status": "conflicting", "incoming": new, "present_in_latest_import": True}
    previous, current = old.get("scan_times", {}), new.get("scan_times", {})
    newer = bool(current) and (not previous or (all(k in current and current[k] >= v for k, v in previous.items()) and any(current.get(k, "") > v for k, v in previous.items())))
    equal = old.get("values") == new.get("values") and old.get("scope") == new.get("scope")
    if newer:
        merged = {**copy.deepcopy(old), **new}
        merged.pop("incoming", None)
        return merged
    if equal:
        return {**copy.deepcopy(old), "present_in_latest_import": True}
    return {**copy.deepcopy(old), "status": "conflicting", "incoming": new, "present_in_latest_import": True}


def identity(guid, char, att, previous):
    candidate = att.get(guid, {})
    if not isinstance(candidate, dict):
        candidate = {}
    name, realm = candidate.get("name"), candidate.get("realm")
    issues = []
    if candidate.get("guid") not in (None, guid):
        issues.append("ATT key and explicit GUID disagree")
    if char.get("name") and name and char["name"] != name:
        issues.append("Collector and ATT names disagree")
    if not isinstance(name, str) or not name or not isinstance(realm, str) or not realm:
        name, realm = None, None
    if previous and not name and char.get("name") and previous.get("name") and char["name"] != previous["name"]:
        issues.append("Collector name disagrees with the retained identity")
    if previous and not name and not issues:
        return {**copy.deepcopy(previous), "present_in_latest_import": False}
    if previous and previous.get("name") and name and (name, realm) != (previous.get("name"), previous.get("realm")):
        issues.append("Name/realm changed from previous identity; verify rename or transfer")
    result = {"status": "conflicting" if issues else "matched" if name else "unmatched", "name": name if not issues else None,
              "realm": realm if not issues else None, "class": candidate.get("class"), "source": "ATTCharacterData exact GUID", "issues": issues, "present_in_latest_import": bool(name)}
    if issues:
        result["candidates"] = {"collector_name": char.get("name"), "att_name": name, "att_realm": realm}
    if previous and result["status"] in ("unmatched", "conflicting"):
        result["previous_identity"] = {k: previous.get(k) for k in ("name", "realm", "source")}
    return result


def validate_context(game, environment):
    if game != "retail":
        raise ValueError("Unsupported game for this Collector/ATT adapter: only retail has verified fixtures; use manual observations for other versions")
    if environment not in ("live", "ptr", "beta"):
        raise ValueError("Environment must be explicitly resolved as live, ptr or beta")


def import_data(collector, att, account, region, previous=None, sources=None, *, game, environment, adopt_legacy_retail=False):
    validate_context(game, environment)
    table(collector); table(att)
    chars = table(collector.get("chars"))
    if any(not isinstance(g, str) or not GUID.fullmatch(g) or not isinstance(c, dict) for g, c in chars.items()):
        raise ValueError("Invalid Collector character table")
    has_previous = previous is not None
    previous = {} if previous is None else previous
    table(previous)
    if has_previous:
        if (previous.get("kind"), previous.get("account"), previous.get("region")) != (KIND, account, region):
            raise ValueError("Existing output is not the same account/region observation snapshot")
        legacy = previous.get("schema_version") == 1 and "game" not in previous and "environment" not in previous
        if legacy:
            if not adopt_legacy_retail:
                raise ValueError("Legacy snapshot has no game/environment; verify it is Retail in the selected environment and pass --adopt-legacy-retail")
        elif (previous.get("schema_version"), previous.get("game"), previous.get("environment")) != (2, game, environment):
            raise ValueError("Existing output has an incompatible schema or game/environment; use a separate output for each context")
        # Validate imported containers before merging, including retained characters.
        groups = [("account_observations", table(previous.get("account_observations", {}), "existing account_observations"))]
        for guid, char in table(previous.get("characters", {}), "existing characters").items():
            path = f"existing characters.{guid}"
            if not isinstance(guid, str) or not GUID.fullmatch(guid):
                raise ValueError("Invalid existing character GUID")
            table(char, path)
            table(char.get("identity"), path + ".identity")
            groups.append((path + ".observations", table(char.get("observations", {}), path + ".observations")))
        for path, observations in groups:
            for name, field in observations.items():
                table(field, f"{path}.{name}")
                table(field.get("scan_times", {}), f"{path}.{name}.scan_times")
    result = copy.deepcopy(previous)
    result.update(kind=KIND, schema_version=2, game=game, environment=environment, account=account, region=region, imported_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                  collector_format_version=collector.get("version"), sources=sources or [], account_ap=None,
                  limitations=["Partial addon observations, not a complete account roster or achievement catalog.",
                               "Collector achievement flags are character-specific; no account AP is inferred.",
                               "Profession/recipe coverage is partial; zero skill is not eligibility.",
                               "Inventory contains recorded containers only; missing storage/items are unknown.",
                               "Faction values need definitions to interpret standing, friendship or renown."])
    output = result.setdefault("characters", {})
    source = {"addon": "WoWthing Collector", "sha256": (sources or [{}])[0].get("sha256")}
    for guid, old in output.items():
        old["present_in_latest_import"] = False
        for field in old.get("observations", {}).values():
            field["present_in_latest_import"] = False
    for guid, char in chars.items():
        old = output.get(guid, {})
        record = copy.deepcopy(old)
        record.update(guid=guid, present_in_latest_import=True, identity=identity(guid, char, att, old.get("identity")))
        observed = timestamp(char.get("lastSeen"))
        record["last_seen"] = max(filter(None, [old.get("last_seen"), observed]), default=None)
        if record["identity"].get("present_in_latest_import"):
            record["identity"]["source_sha256"] = (sources or [{}, {}])[-1].get("sha256")
        scans = table(char.get("scanTimes", {}))
        fields = {
            "reputations": observation(char, "reputationsV2", reputations, {"reputations": scans.get("reputations")}, source, "character", "Only recorded factions; values use faction-specific scales."),
            "currencies": observation(char, "currencies", currencies, {"currencies": scans.get("currencies")}, source, "character", "Absent currency IDs do not imply zero balance."),
            "professions": observation(char, "professions", professions, {}, source, "character", "Partial tracked skill lines/recipes; lastSeen is not a profession scan time."),
            "achievements": observation(char, "achievements", achievements, {"achievements": scans.get("achievements")}, source, "character", "Selected character-earned flags, not account-wide completion."),
            "inventory": observation(char, "items", inventory, {"bags": scans.get("bags"), "bank": scans.get("bank")}, source, "character", "Mixed container freshness; retain bag/bank scan times, no global absence inference."),
        }
        for name in ("level", "copper", "currentLocation", "bindLocation"):
            fields[name] = observation(char, name, scalar, {"lastSeen": char.get("lastSeen")}, source, "character", "Observed value at character last-seen time; missing fields do not clear prior values.")
        target = record.setdefault("observations", {})
        for name in set(target) | set(fields):
            merged = merge_observation(target.get(name), fields.get(name))
            if merged is not None:
                target[name] = merged
        output[guid] = record
    scans = table(collector.get("scanTimes", {}))
    shared = result.setdefault("account_observations", {})
    warbank = table(collector.get("warbank", {}))
    fields = {
        "reputations": observation(collector, "reputations", reputations, {"reputations": scans.get("reputations")}, source, "account", "Collector identifies these as shared; do not duplicate as independent character standing."),
        "warbank": observation(warbank, "items", inventory, {"warbank": warbank.get("scannedAt")}, source, "account", "Only recorded shared-bank containers."),
    }
    for name in set(shared) | set(fields):
        merged = merge_observation(shared.get(name), fields.get(name))
        if merged is not None:
            shared[name] = merged
    result["coverage"] = coverage(result)
    return result


def coverage(data):
    current = [c for c in data["characters"].values() if c["present_in_latest_import"]]
    return {"current_characters": len(current), "retained_characters": len(data["characters"]) - len(current),
            "identities": {s: sum(c["identity"]["status"] == s for c in current) for s in ("matched", "unmatched", "conflicting")},
            "field_conflicts": sum(o.get("status") == "conflicting" for c in data["characters"].values() for o in c.get("observations", {}).values()) + sum(o.get("status") == "conflicting" for o in data.get("account_observations", {}).values()),
            "unsupported_fields": sum(o.get("status") == "unsupported" for c in data["characters"].values() for o in c.get("observations", {}).values()) + sum(o.get("status") == "unsupported" for o in data.get("account_observations", {}).values())}


def report(data):
    def escape(value):
        return str(value).replace("|", "\\|").replace("\n", " ").replace("\r", " ").replace("<", "&lt;").replace(">", "&gt;")
    lines = ["# WoW account import", "", f"Game: **{escape(data['game'])} / {escape(data['environment'])}**", "", f"Account: **{escape(data['account'])}** ({data['region']}). Import: {data['imported_at']}", "",
             "This is partial source coverage, not researched achievement readiness. Account AP remains unknown; source task notes are never edited.", "",
             "| Character / GUID | Identity | Seen in this import | Last seen | Reputation | Professions | Currencies | Inventory | Character achievements |",
             "|---|---|---|---|---|---|---|---|---|"]
    for guid, char in sorted(data["characters"].items()):
        ident = char["identity"]
        label = f"{ident['name']}-{ident['realm']}" if ident.get("name") else guid
        cells = [label, ident["status"], "yes" if char["present_in_latest_import"] else "retained", char.get("last_seen") or "unknown"]
        for name in ("reputations", "professions", "currencies", "inventory", "achievements"):
            value = char.get("observations", {}).get(name)
            cells.append("missing" if value is None else value["status"] + ("; retained" if not value.get("present_in_latest_import") else "") + "; " + (value.get("observed_at") or "see scan times / undated"))
        lines.append("| " + " | ".join(escape(c) for c in cells) + " |")
    lines += ["", "## Shared account observations", ""]
    for name, value in data.get("account_observations", {}).items():
        lines.append(f"- {name}: {value['status']}; observed {value.get('observed_at') or 'unknown'}; present in latest import: {value.get('present_in_latest_import')}. {value.get('note', '')}")
    lines += ["", "## Limits and next checks", ""] + ["- " + x for x in data["limitations"]]
    lines += ["- Resolve unmatched/conflicting identities using exact GUID evidence; do not request repeat logins just for missing names.",
              "- A populated field is partial coverage, not proof every faction, recipe, bank slot or achievement was scanned.",
              "- Inspect unsupported encodings and retained observations before making eligibility or purchasing claims.", "",
              "## Coverage summary", "", "```json", json.dumps(data["coverage"], indent=2), "```", ""]
    return "\n".join(lines)


def atomic_write(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, prefix=".wow-import-", delete=False)
    try:
        with handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(handle.name, path)
    finally:
        if os.path.exists(handle.name):
            os.unlink(handle.name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collector", required=True, type=Path)
    parser.add_argument("--att", required=True, type=Path)
    parser.add_argument("--account", required=True, help="Stable user-chosen alias; used to prevent cross-account merges")
    parser.add_argument("--region", required=True, choices=["eu", "us", "kr", "tw", "cn"])
    parser.add_argument("--game", required=True, help="Resolved game version; this verified adapter supports retail only")
    parser.add_argument("--environment", required=True, choices=["live", "ptr", "beta"], help="Verified source client environment, never inferred from GUID or filenames")
    parser.add_argument("--adopt-legacy-retail", action="store_true", help="Confirm the existing schema-1 snapshot was verified as Retail in the selected environment")
    parser.add_argument("--output", required=True, type=Path, help="Observation JSON; existing compatible output is merged")
    parser.add_argument("--report", type=Path, help="Optional generated coverage Markdown")
    args = parser.parse_args()
    validate_context(args.game, args.environment)
    inputs = {args.collector.resolve(), args.att.resolve()}
    outputs = [args.output.resolve()] + ([args.report.resolve()] if args.report else [])
    if len(set(outputs)) != len(outputs) or inputs.intersection(outputs):
        raise ValueError("Outputs must be distinct from each other and all source files")
    if args.output.is_symlink() or (args.report and args.report.is_symlink()):
        raise ValueError("Output symlinks are not supported; choose explicit output files")
    if not args.account.strip():
        raise ValueError("Account alias cannot be empty")
    if args.report and args.report.exists() and not args.report.read_text(encoding="utf-8").startswith("# WoW account import\n"):
        raise ValueError("Refusing to overwrite a non-generated report")
    before_output = args.output.read_bytes() if args.output.exists() else None
    existing = json.loads(before_output) if before_output is not None else None
    collector, source_c = read_lua(args.collector)
    att, source_a = read_lua(args.att)
    result = import_data(table(collector.get("WWTCSaved")), table(att.get("ATTCharacterData")), args.account, args.region, existing, [source_c, source_a],
                         game=args.game, environment=args.environment, adopt_legacy_retail=args.adopt_legacy_retail)
    payload = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    markdown = report(result)
    if (args.output.read_bytes() if args.output.exists() else None) != before_output:
        raise ValueError("Output changed during import; retry without overwriting concurrent edits")
    atomic_write(args.output, payload)
    if args.report:
        try:
            atomic_write(args.report, markdown)
        except OSError:
            print(f"Snapshot saved to {args.output}, but coverage report could not be written", file=sys.stderr)
            return 1
    print(json.dumps({"output": str(args.output), "report": str(args.report) if args.report else None, "coverage": result["coverage"]}))


if __name__ == "__main__":
    try:
        sys.exit(main() or 0)
    except (ValueError, TypeError, KeyError, OSError, RecursionError) as error:
        print(f"Import failed: {error}", file=sys.stderr)
        sys.exit(1)
