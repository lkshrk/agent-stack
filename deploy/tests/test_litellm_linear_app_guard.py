"""Check the Linear sub-issue guard rules. Full run (LITELLM_VERSION from .github/workflows/ci.yml): uv run --no-project --with "litellm==${LITELLM_VERSION#v}" --with httpx --with fastapi python3 -m unittest discover -s deploy/tests"""

import asyncio
import importlib.util
from pathlib import Path
import unittest

MODULE = Path(__file__).resolve().parents[1] / "litellm" / "linear_app_guard.py"
spec = importlib.util.spec_from_file_location("linear_app_guard", MODULE)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)

OWNER = "u-owner"
APP = "u-app"
POLICY = guard.Policy(owner_id=OWNER, app_user_id=APP)


def issue(n, parent=None, creator=OWNER, labels=(), children=0):
    return guard.Issue(
        id=f"uuid-{n}",
        identifier=f"ROU-{n}",
        parent_id=f"uuid-{parent}" if parent is not None else None,
        creator_id=creator,
        labels=tuple(labels),
        child_count=children,
    )


class FakeLookup:
    def __init__(self, *issues, error=None, delay=0):
        self.by_ref = {}
        for i in issues:
            self.by_ref[i.id] = i
            self.by_ref[i.identifier] = i
        self.error = error
        self.delay = delay

    async def issue(self, ref):
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return self.by_ref.get(ref)


class GuardTest(unittest.IsolatedAsyncioTestCase):
    async def decide(self, tool, args, *issues, **kwargs):
        return await guard.decide(tool, args, FakeLookup(*issues, **kwargs), POLICY)

    async def assertDenied(self, word, tool, args, *issues, **kwargs):
        reason = await self.decide(tool, args, *issues, **kwargs)
        self.assertIsNotNone(reason)
        self.assertIn(word, reason)

    async def assertAllowed(self, tool, args, *issues, **kwargs):
        self.assertIsNone(await self.decide(tool, args, *issues, **kwargs))

    async def test_read_tool_allowed(self):
        await self.assertAllowed("get_issue", {"id": "ROU-1"})

    async def test_comment_allowed(self):
        await self.assertAllowed("save_comment", {"issueId": "ROU-1", "body": "x"})

    async def test_unknown_write_tool_denied(self):
        await self.assertDenied("tool", "delete_comment", {"id": "c"})

    async def test_save_issue_listing_scan_allowed(self):
        await self.assertAllowed("save_issue", {})

    async def test_unknown_write_tool_denied_on_listing_scan(self):
        await self.assertDenied("tool", "delete_comment", {})

    async def test_image_upload_allowed(self):
        for content_type in ("image/png", "image/jpeg", "image/webp"):
            with self.subTest(content_type=content_type):
                await self.assertAllowed(
                    "prepare_file_upload", {"filename": "shot.png", "contentType": content_type, "size": 2048}
                )

    async def test_upload_size_bounds_allowed(self):
        for size in (1, 10 * 1024 * 1024):
            with self.subTest(size=size):
                await self.assertAllowed(
                    "prepare_file_upload", {"filename": "shot.png", "contentType": "image/png", "size": size}
                )

    async def test_upload_listing_scan_allowed(self):
        await self.assertAllowed("prepare_file_upload", {})

    async def test_upload_wrong_content_type_denied(self):
        for content_type in ("image/svg+xml", "text/html", "application/pdf", "IMAGE/PNG", "", None, ["image/png"]):
            with self.subTest(content_type=content_type):
                reason = await self.decide(
                    "prepare_file_upload", {"filename": "shot.png", "contentType": content_type, "size": 2048}
                )
                self.assertEqual(reason, "content type not allowed")

    async def test_upload_missing_content_type_denied(self):
        await self.assertDenied("content type", "prepare_file_upload", {"filename": "shot.png", "size": 2048})

    async def test_upload_bad_size_denied(self):
        for size in (0, -1, 10 * 1024 * 1024 + 1, 2048.0, "2048", True, None):
            with self.subTest(size=size):
                reason = await self.decide(
                    "prepare_file_upload", {"filename": "shot.png", "contentType": "image/png", "size": size}
                )
                self.assertEqual(reason, "upload size not allowed")

    async def test_upload_bad_filename_denied(self):
        for name in ("", "   ", "a" * 201, "../shot.png", "dir/shot.png", "dir\\shot.png", "shot\0.png", None, 7):
            with self.subTest(name=name):
                reason = await self.decide(
                    "prepare_file_upload", {"filename": name, "contentType": "image/png", "size": 2048}
                )
                self.assertEqual(reason, "filename not allowed")

    async def test_upload_filename_at_limit_allowed(self):
        await self.assertAllowed(
            "prepare_file_upload", {"filename": "a" * 196 + ".png", "contentType": "image/png", "size": 2048}
        )

    async def test_attachment_tools_denied(self):
        for tool in ("prepare_attachment_upload", "create_attachment", "create_attachment_from_upload"):
            for args in ({}, {"issueId": "ROU-1", "filename": "shot.png", "contentType": "image/png", "size": 2048}):
                with self.subTest(tool=tool, args=args):
                    self.assertEqual(await self.decide(tool, args), f"tool {tool} not allowed")

    async def test_create_without_parent_denied(self):
        for args in ({"title": "t"}, {"title": "t", "parentId": None}, {"title": "t", "parentId": ""}):
            with self.subTest(args=args):
                await self.assertDenied("parent", "save_issue", args)

    async def test_create_with_template_denied(self):
        await self.assertDenied("template", "save_issue", {"title": "t", "parentId": "ROU-1", "template": "x"}, issue(1))

    async def test_create_under_owner_root_allowed(self):
        await self.assertAllowed("save_issue", {"title": "t", "parentId": "ROU-1"}, issue(1))

    async def test_create_by_uuid_allowed(self):
        await self.assertAllowed("save_issue", {"title": "t", "parentId": "uuid-1"}, issue(1))

    async def test_create_under_app_created_root_denied(self):
        await self.assertDenied("owner", "save_issue", {"title": "t", "parentId": "ROU-1"}, issue(1, creator=APP))

    async def test_create_under_unknown_parent_denied(self):
        await self.assertDenied("parent", "save_issue", {"title": "t", "parentId": "ROU-9"})

    async def test_create_one_level_deep_allowed(self):
        await self.assertAllowed(
            "save_issue", {"title": "t", "parentId": "ROU-2"}, issue(1), issue(2, parent=1, creator=APP)
        )

    async def test_create_too_deep_denied(self):
        await self.assertDenied(
            "depth",
            "save_issue",
            {"title": "t", "parentId": "ROU-3"},
            issue(1),
            issue(2, parent=1, creator=APP),
            issue(3, parent=2, creator=APP),
        )

    async def test_create_parent_full_denied(self):
        await self.assertDenied("children", "save_issue", {"title": "t", "parentId": "ROU-1"}, issue(1, children=8))

    async def test_create_parent_with_room_allowed(self):
        await self.assertAllowed("save_issue", {"title": "t", "parentId": "ROU-1"}, issue(1, children=7))

    async def test_parent_cycle_denied(self):
        await self.assertDenied(
            "chain",
            "save_issue",
            {"title": "t", "parentId": "ROU-1"},
            issue(1, parent=2),
            issue(2, parent=1),
        )

    async def test_walk_cap_denied(self):
        chain = [issue(n, parent=n + 1) for n in range(1, 11)] + [issue(11)]
        await self.assertDenied("chain", "save_issue", {"title": "t", "parentId": "ROU-1"}, *chain)

    async def test_broken_chain_denied(self):
        await self.assertDenied("chain", "save_issue", {"title": "t", "parentId": "ROU-2"}, issue(2, parent=1))

    async def test_update_state_denied(self):
        await self.assertDenied("state", "save_issue", {"id": "ROU-2", "state": "Done"}, issue(1), issue(2, 1, APP))

    async def test_update_labels_replace_denied(self):
        await self.assertDenied("labels", "save_issue", {"id": "ROU-2", "labels": ["x"]}, issue(1), issue(2, 1, APP))

    async def test_update_detach_denied(self):
        for value in (None, ""):
            with self.subTest(value=value):
                await self.assertDenied(
                    "parent", "save_issue", {"id": "ROU-2", "parentId": value}, issue(1), issue(2, 1, APP)
                )

    async def test_update_same_parent_by_identifier_allowed(self):
        await self.assertAllowed("save_issue", {"id": "ROU-2", "parentId": "ROU-1"}, issue(1), issue(2, 1, APP))

    async def test_update_same_parent_by_uuid_allowed(self):
        await self.assertAllowed("save_issue", {"id": "ROU-2", "parentId": "uuid-1"}, issue(1), issue(2, 1, APP))

    async def test_update_new_parent_runs_create_checks(self):
        await self.assertDenied(
            "owner",
            "save_issue",
            {"id": "ROU-2", "parentId": "ROU-3"},
            issue(1),
            issue(2, 1, APP),
            issue(3, creator=APP),
        )

    async def test_update_new_parent_under_self_denied(self):
        await self.assertDenied(
            "chain",
            "save_issue",
            {"id": "ROU-2", "parentId": "ROU-3"},
            issue(1),
            issue(2, 1, APP),
            issue(3, 2, APP),
        )

    async def test_update_remove_labels_denied(self):
        for name in ("gate:pr", "Gate:PR", " risk:high", "bug"):
            with self.subTest(name=name):
                await self.assertDenied(
                    "label removal", "save_issue", {"id": "ROU-2", "removeLabels": [name]}, issue(1), issue(2, 1, APP)
                )

    async def test_update_remove_labels_on_owner_issue_denied(self):
        await self.assertDenied("owner issue", "save_issue", {"id": "ROU-1", "removeLabels": ["bug"]}, issue(1))

    async def test_update_lower_gate_case_and_space_variants_denied(self):
        for name in ("Gate:PR", " gate:pr", "GATE:SPLIT"):
            with self.subTest(name=name):
                await self.assertDenied(
                    "gate",
                    "save_issue",
                    {"id": "ROU-2", "addLabels": [name]},
                    issue(1),
                    issue(2, 1, APP, labels=("Gate:Plan",)),
                )

    async def test_update_add_label_uuid_denied(self):
        await self.assertDenied(
            "label",
            "save_issue",
            {"id": "ROU-2", "addLabels": ["123E4567-e89b-12d3-a456-426614174000"]},
            issue(1),
            issue(2, 1, APP),
        )

    async def test_update_add_labels_string_denied(self):
        await self.assertDenied(
            "addLabels", "save_issue", {"id": "ROU-2", "addLabels": "bug,gate:plan"}, issue(1), issue(2, 1, APP)
        )

    async def test_update_add_unknown_gate_denied(self):
        await self.assertDenied(
            "gate", "save_issue", {"id": "ROU-2", "addLabels": ["gate:other"]}, issue(1), issue(2, 1, APP)
        )

    async def test_update_add_risk_and_plain_labels_allowed(self):
        await self.assertAllowed(
            "save_issue", {"id": "ROU-2", "addLabels": ["risk:high", "bug"]}, issue(1), issue(2, 1, APP)
        )

    async def test_update_reparent_with_children_denied(self):
        await self.assertDenied(
            "children",
            "save_issue",
            {"id": "ROU-2", "parentId": "ROU-3"},
            issue(1),
            issue(2, 1, APP, children=1),
            issue(3),
        )

    async def test_update_self_parent_denied(self):
        await self.assertDenied(
            "chain", "save_issue", {"id": "ROU-2", "parentId": "ROU-2"}, issue(1), issue(2, 1, APP)
        )

    async def test_bad_argument_shapes_denied(self):
        cases = [
            {"title": "t", "parentId": 5},
            {"title": "t", "parentId": ["ROU-1"]},
            {"id": 5, "description": "x"},
            {"id": "", "description": "x"},
            {"id": "ROU-2", "parentId": 5},
        ]
        for args in cases:
            with self.subTest(args=args):
                self.assertIsNotNone(await self.decide("save_issue", args, issue(1), issue(2, 1, APP)))

    def test_policy_rejects_bad_identities(self):
        for owner, app in (("", "a"), ("o", ""), ("x", "x")):
            with self.subTest(owner=owner, app=app):
                with self.assertRaises(ValueError):
                    guard.Policy(owner_id=owner, app_user_id=app)

    async def test_update_lower_gate_denied(self):
        await self.assertDenied(
            "gate",
            "save_issue",
            {"id": "ROU-2", "addLabels": ["gate:pr"]},
            issue(1),
            issue(2, 1, APP, labels=("gate:plan",)),
        )

    async def test_update_raise_gate_allowed(self):
        await self.assertAllowed(
            "save_issue",
            {"id": "ROU-2", "addLabels": ["gate:plan"]},
            issue(1),
            issue(2, 1, APP, labels=("gate:pr",)),
        )

    async def test_update_add_gate_without_current_defaults_to_plan(self):
        for gate in ("gate:pr", "gate:split"):
            await self.assertDenied(
                "gate", "save_issue", {"id": "ROU-2", "addLabels": [gate]}, issue(1), issue(2, 1, APP)
            )
        await self.assertAllowed(
            "save_issue", {"id": "ROU-2", "addLabels": ["gate:plan"]}, issue(1), issue(2, 1, APP)
        )

    async def test_update_non_gate_current_labels_default_to_plan(self):
        await self.assertDenied(
            "gate",
            "save_issue",
            {"id": "ROU-2", "addLabels": ["gate:pr"]},
            issue(1),
            issue(2, 1, APP, labels=("bug",)),
        )

    async def test_create_gate_label_denied(self):
        for key in ("labels", "addLabels"):
            for gate in ("gate:plan", " Gate:PR "):
                await self.assertDenied(
                    "gate", "save_issue", {"title": "t", "parentId": "ROU-1", key: [gate]}, issue(1)
                )

    async def test_create_plain_labels_allowed(self):
        await self.assertAllowed("save_issue", {"title": "t", "parentId": "ROU-1", "labels": ["bug"]}, issue(1))

    async def test_create_label_uuid_denied(self):
        await self.assertDenied(
            "label ids",
            "save_issue",
            {"title": "t", "parentId": "ROU-1", "labels": ["86F579DD-3529-498B-B531-56ECE905D2CC"]},
            issue(1),
        )

    async def test_create_malformed_labels_denied(self):
        for value in ("gate:pr", [1], None, {"a": "b"}):
            await self.assertDenied(
                "labels", "save_issue", {"title": "t", "parentId": "ROU-1", "labels": value}, issue(1)
            )

    async def test_create_state_and_duplicate_of_denied(self):
        for key in ("state", "duplicateOf"):
            await self.assertDenied(key, "save_issue", {"title": "t", "parentId": "ROU-1", key: "x"}, issue(1))

    async def test_update_owner_issue_description_denied(self):
        await self.assertDenied("owner issue", "save_issue", {"id": "ROU-1", "description": "x"}, issue(1))

    async def test_update_owner_issue_non_gate_label_denied(self):
        for labels in (["bug"], ["gate:split", "risk:high"], ["epic"]):
            with self.subTest(labels=labels):
                await self.assertDenied(
                    "only gate labels", "save_issue", {"id": "ROU-1", "addLabels": labels}, issue(1, labels=("gate:pr",))
                )

    async def test_update_owner_issue_gate_label_allowed(self):
        await self.assertAllowed(
            "save_issue", {"id": "ROU-1", "addLabels": ["gate:split"]}, issue(1, labels=("gate:pr",))
        )

    async def test_update_owner_issue_lower_gate_still_denied(self):
        await self.assertDenied("gate", "save_issue", {"id": "ROU-1", "addLabels": ["gate:split"]}, issue(1))

    async def test_update_owner_issue_links_allowed(self):
        await self.assertAllowed(
            "save_issue",
            {"id": "ROU-1", "links": [{"url": "u"}], "blockedBy": ["ROU-2"]},
            issue(1),
            issue(2, 1, APP),
        )

    async def test_assignment_keys_denied(self):
        cases = {
            "create": {"title": "t", "parentId": "ROU-1"},
            "app update": {"id": "ROU-2"},
            "owner update": {"id": "ROU-1"},
        }
        for name, base in cases.items():
            for key in guard.ASSIGN_KEYS:
                for value in ("u-someone", None, "", "me"):
                    with self.subTest(case=name, key=key, value=value):
                        await self.assertDenied(
                            f"{key} not allowed", "save_issue", {**base, key: value}, issue(1), issue(2, 1, APP)
                        )

    async def test_assignment_keys_denied_without_lookups(self):
        await self.assertDenied(
            "delegate not allowed",
            "save_issue",
            {"id": "ROU-2", "delegate": None},
            issue(1),
            issue(2, 1, APP),
            error=RuntimeError("boom"),
        )

    async def test_create_reserved_labels_denied(self):
        for key in ("labels", "addLabels"):
            for name in ("epic", "EPIC", " Epic ", "chain:serial", "Chain:Anything"):
                with self.subTest(key=key, name=name):
                    await self.assertDenied(
                        "not allowed", "save_issue", {"title": "t", "parentId": "ROU-1", key: ["bug", name]}, issue(1)
                    )

    async def test_update_app_issue_reserved_labels_denied(self):
        for name in ("epic", "EPIC", "chain:serial"):
            with self.subTest(name=name):
                await self.assertDenied(
                    "not allowed", "save_issue", {"id": "ROU-2", "addLabels": [name]}, issue(1), issue(2, 1, APP)
                )

    async def test_create_blocked_by_sibling_allowed(self):
        await self.assertAllowed(
            "save_issue",
            {"title": "t", "parentId": "ROU-1", "blockedBy": ["ROU-2", "uuid-3"], "relatedTo": ["ROU-1"]},
            issue(1),
            issue(2, 1, APP),
            issue(3, 1, APP),
        )

    async def test_create_relation_under_nested_parent_allowed(self):
        await self.assertAllowed(
            "save_issue",
            {"title": "t", "parentId": "ROU-2", "blocks": ["ROU-3"]},
            issue(1),
            issue(2, 1, APP),
            issue(3, 1, APP),
        )

    async def test_update_app_child_blocks_sibling_allowed(self):
        await self.assertAllowed(
            "save_issue", {"id": "ROU-2", "blocks": ["ROU-3"]}, issue(1), issue(2, 1, APP), issue(3, 1, APP)
        )

    async def test_update_app_child_remove_relations_within_root_allowed(self):
        for key in ("removeBlocks", "removeBlockedBy", "removeRelatedTo"):
            with self.subTest(key=key):
                await self.assertAllowed(
                    "save_issue", {"id": "ROU-2", key: ["ROU-3"]}, issue(1), issue(2, 1, APP), issue(3, 1, APP)
                )

    async def test_relation_outside_root_denied(self):
        others = (issue(10), issue(11, 10, APP), issue(20))
        tree = (issue(1), issue(2, 1, APP))
        cases = [
            ("create other tree", {"title": "t", "parentId": "ROU-1", "blockedBy": ["ROU-11"]}),
            ("create other root", {"title": "t", "parentId": "ROU-1", "relatedTo": ["ROU-10"]}),
            ("create top-level", {"title": "t", "parentId": "ROU-1", "blocks": ["ROU-20"]}),
            ("app update other tree", {"id": "ROU-2", "blocks": ["ROU-11"]}),
            ("app update top-level", {"id": "ROU-2", "relatedTo": ["ROU-20"]}),
            ("app update remove", {"id": "ROU-2", "removeBlockedBy": ["ROU-11"]}),
            ("owner update other tree", {"id": "ROU-1", "blockedBy": ["ROU-11"]}),
            ("owner update top-level", {"id": "ROU-1", "relatedTo": ["ROU-20"]}),
            ("mixed", {"id": "ROU-2", "blocks": ["ROU-1"], "blockedBy": ["ROU-20"]}),
        ]
        for name, args in cases:
            with self.subTest(case=name):
                await self.assertDenied("outside", "save_issue", args, *tree, *others)

    async def test_relation_under_third_user_root_denied(self):
        other = "u-other"
        await self.assertDenied(
            "not owner-created",
            "save_issue",
            {"id": "ROU-31", "blocks": ["ROU-32"]},
            issue(30, creator=other),
            issue(31, 30, creator=other),
            issue(32, 30, creator=other),
        )
        await self.assertDenied(
            "not owner-created",
            "save_issue",
            {"id": "ROU-30", "relatedTo": ["ROU-31"]},
            issue(30, creator=other),
            issue(31, 30, creator=other),
        )

    async def test_reparent_checks_relations_against_new_root(self):
        tree = (issue(1), issue(2, 1, APP), issue(3, 1, APP), issue(10), issue(11, 10, APP))
        await self.assertDenied(
            "outside", "save_issue", {"id": "ROU-2", "parentId": "ROU-10", "blocks": ["ROU-3"]}, *tree
        )
        await self.assertAllowed(
            "save_issue", {"id": "ROU-2", "parentId": "ROU-10", "blocks": ["ROU-11"]}, *tree
        )

    async def test_lookups_memoised_per_call(self):
        lookup = FakeLookup(issue(1), *(issue(n, 1, APP) for n in range(2, 7)))
        calls = []
        original = lookup.issue

        async def counting(ref):
            calls.append(ref)
            return await original(ref)

        lookup.issue = counting
        args = {"title": "t", "parentId": "ROU-1", "blockedBy": ["ROU-2", "ROU-3", "ROU-4", "ROU-5", "ROU-6"]}
        self.assertIsNone(await guard.decide("save_issue", args, lookup, POLICY))
        self.assertEqual(calls.count("uuid-1"), 0)
        self.assertEqual(calls.count("ROU-1"), 1)
        self.assertEqual(len(calls), 6)

    async def test_owner_issue_remove_relations_denied(self):
        await self.assertDenied(
            "owner issue", "save_issue", {"id": "ROU-1", "removeBlocks": ["ROU-2"]}, issue(1), issue(2, 1, APP)
        )

    async def test_relation_unknown_target_denied(self):
        await self.assertDenied(
            "not found", "save_issue", {"title": "t", "parentId": "ROU-1", "blockedBy": ["ROU-9"]}, issue(1)
        )
        await self.assertDenied("not found", "save_issue", {"id": "ROU-2", "blocks": ["ROU-9"]}, issue(1), issue(2, 1, APP))

    async def test_relation_target_broken_chain_denied(self):
        await self.assertDenied(
            "relation target: parent chain broken",
            "save_issue",
            {"id": "ROU-2", "blocks": ["ROU-3"]},
            issue(1),
            issue(2, 1, APP),
            issue(3, 7, APP),
        )

    async def test_relation_lookup_failure_denied(self):
        await self.assertDenied(
            "lookup failed",
            "save_issue",
            {"id": "ROU-2", "blocks": ["ROU-3"]},
            issue(1),
            issue(2, 1, APP),
            issue(3, 1, APP),
            error=RuntimeError("boom"),
        )

    async def test_too_many_relations_denied(self):
        siblings = [issue(n, 1, APP) for n in range(2, 14)]
        await self.assertDenied(
            "too many relations",
            "save_issue",
            {"title": "t", "parentId": "ROU-1", "blockedBy": [f"ROU-{n}" for n in range(2, 8)], "relatedTo": [f"ROU-{n}" for n in range(8, 13)]},
            issue(1),
            *siblings,
        )
        await self.assertAllowed(
            "save_issue",
            {"id": "ROU-2", "blocks": [f"ROU-{n}" for n in range(3, 13)]},
            issue(1),
            *siblings,
        )

    async def test_relation_bad_shape_denied(self):
        for value in ("ROU-2", [5], [""], None):
            with self.subTest(value=value):
                await self.assertDenied(
                    "blocks", "save_issue", {"id": "ROU-2", "blocks": value}, issue(1), issue(2, 1, APP)
                )

    async def test_update_own_subissue_description_allowed(self):
        await self.assertAllowed(
            "save_issue", {"id": "ROU-2", "description": "x", "title": "y"}, issue(1), issue(2, 1, APP)
        )

    async def test_update_unknown_target_denied(self):
        await self.assertDenied("not found", "save_issue", {"id": "ROU-9", "description": "x"})

    async def test_lookup_error_denied(self):
        await self.assertDenied(
            "lookup failed", "save_issue", {"title": "t", "parentId": "ROU-1"}, issue(1), error=RuntimeError("boom")
        )

    async def test_slow_lookup_denied_within_budget(self):
        lookup = FakeLookup(issue(1), delay=5)
        loop = asyncio.get_running_loop()
        start = loop.time()
        reason = await guard.decide("save_issue", {"title": "t", "parentId": "ROU-1"}, lookup, POLICY, timeout=0.05)
        self.assertEqual(reason, "lookup failed")
        self.assertLess(loop.time() - start, 1)


try:
    import litellm  # noqa: F401
except ImportError:
    httpx = None
else:
    import httpx
    from fastapi import HTTPException
    from litellm.integrations.custom_guardrail import CustomGuardrail  # noqa: F401


def graphql_issue(**overrides):
    node = {
        "id": "uuid-1",
        "identifier": "ROU-1",
        "parent": {"id": "uuid-0"},
        "creator": {"id": OWNER},
        "labels": {"nodes": [{"name": "gate:plan"}]},
        "children": {"nodes": [{"id": "c1"}, {"id": "c2"}]},
    }
    node.update(overrides)
    return {"data": {"issue": node}}


def lookup_with(handler):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return guard.GraphQLLookup("lin_token", client)


@unittest.skipIf(httpx is None, "litellm wiring needs httpx and fastapi")
class GraphQLLookupTest(unittest.IsolatedAsyncioTestCase):
    async def test_maps_issue_with_label_names(self):
        seen = {}

        def handler(request):
            seen["auth"] = request.headers["Authorization"]
            seen["url"] = str(request.url)
            seen["body"] = request.content.decode()
            return httpx.Response(200, json=graphql_issue())

        found = await lookup_with(handler).issue("ROU-1")
        self.assertEqual(found, guard.Issue("uuid-1", "ROU-1", "uuid-0", OWNER, ("gate:plan",), 2))
        self.assertEqual(seen["auth"], "lin_token")
        self.assertEqual(seen["url"], "https://api.linear.app/graphql")
        self.assertIn("labels{ nodes{ name } }", seen["body"])

    async def test_null_issue_is_none(self):
        found = await lookup_with(lambda r: httpx.Response(200, json={"data": {"issue": None}})).issue("ROU-9")
        self.assertIsNone(found)

    async def test_missing_parent_and_creator(self):
        response = httpx.Response(200, json=graphql_issue(parent=None, creator=None))
        found = await lookup_with(lambda r: response).issue("ROU-1")
        self.assertIsNone(found.parent_id)
        self.assertIsNone(found.creator_id)

    async def test_graphql_errors_raise(self):
        with self.assertRaises(Exception):
            await lookup_with(lambda r: httpx.Response(200, json={"errors": [{"message": "x"}]})).issue("ROU-1")

    async def test_non_200_raises(self):
        with self.assertRaises(Exception):
            await lookup_with(lambda r: httpx.Response(500, text="no")).issue("ROU-1")

    async def test_malformed_body_raises(self):
        with self.assertRaises(Exception):
            await lookup_with(lambda r: httpx.Response(200, json={"data": {}})).issue("ROU-1")


@unittest.skipIf(httpx is None, "litellm wiring needs httpx and fastapi")
class LinearAppGuardTest(unittest.IsolatedAsyncioTestCase):
    def make(self, *issues, **kwargs):
        return guard.LinearAppGuard(
            guardrail_name="linear-app-guard",
            event_hook="pre_mcp_call",
            default_on=True,
            lookup=FakeLookup(*issues, **kwargs),
            policy=POLICY,
        )

    async def call(self, instance, tool, arguments=None, server="linear_app"):
        data = {"mcp_tool_name": tool}
        if server is not None:
            data["mcp_server_name"] = server
        if arguments is not None:
            data["mcp_arguments"] = arguments
        inputs = {"texts": []}
        result = await instance.apply_guardrail(inputs, data, "request")
        self.assertIs(result, inputs)

    async def test_guard_passes_other_servers(self):
        for server in ("linear", "context7"):
            await self.call(self.make(), "save_issue", {"title": "t"}, server=server)

    async def test_guard_denies_top_level_create(self):
        with self.assertRaises(HTTPException) as caught:
            await self.call(self.make(), "save_issue", {"team": "T", "title": "t"})
        self.assertEqual(caught.exception.status_code, 403)
        self.assertEqual(caught.exception.detail, "linear-app guard: parent required")

    async def test_guard_denies_missing_server_name(self):
        with self.assertRaises(HTTPException) as caught:
            await self.call(self.make(), "get_issue", {"id": "ROU-1"}, server=None)
        self.assertEqual(caught.exception.status_code, 403)
        self.assertEqual(caught.exception.detail, "linear-app guard: server unknown")

    async def test_guard_allows_subissue_under_owner_issue(self):
        await self.call(self.make(issue(1)), "save_issue", {"title": "t", "parentId": "ROU-1"})

    async def test_guard_allows_read_tool_with_missing_arguments(self):
        await self.call(self.make(), "list_teams")

    async def test_guard_denies_unlisted_tool(self):
        with self.assertRaises(HTTPException) as caught:
            await self.call(self.make(), "delete_comment", {"id": "x"})
        self.assertEqual(caught.exception.status_code, 403)

    async def scan(self, instance, tool):
        data = {
            "mcp_tool_name": tool,
            "mcp_server_name": "linear_app",
            "mcp_arguments": {},
            "mcp_tool_description": f"{tool} description",
            "mcp_input_schema": {"type": "object", "properties": {"id": {"type": "string", "description": "id"}}},
        }
        inputs = {"texts": [f"{tool} description", "id"]}
        result = await instance.apply_guardrail(inputs, data, "request")
        self.assertIs(result, inputs)

    async def test_listing_scan_shows_allowed_tools(self):
        for tool in ("save_issue", "save_comment", "prepare_file_upload", "get_issue", "list_teams"):
            with self.subTest(tool=tool):
                await self.scan(self.make(), tool)

    async def test_listing_scan_hides_unlisted_tool(self):
        with self.assertRaises(HTTPException) as caught:
            await self.scan(self.make(), "delete_comment")
        self.assertEqual(caught.exception.detail, "linear-app guard: tool delete_comment not allowed")

    async def test_guard_denies_svg_upload(self):
        with self.assertRaises(HTTPException) as caught:
            await self.call(
                self.make(), "prepare_file_upload", {"filename": "x.svg", "contentType": "image/svg+xml", "size": 10}
            )
        self.assertEqual(caught.exception.status_code, 403)
        self.assertEqual(caught.exception.detail, "linear-app guard: content type not allowed")

    async def test_save_issue_without_arguments_needs_no_lookup(self):
        await self.call(self.make(error=RuntimeError("boom")), "save_issue")

    async def test_guard_times_out_closed(self):
        loop = asyncio.get_running_loop()
        start = loop.time()
        with self.assertRaises(HTTPException) as caught:
            await self.call(
                self.make(issue(1), delay=6), "save_issue", {"title": "t", "parentId": "ROU-1"}
            )
        self.assertEqual(caught.exception.status_code, 403)
        self.assertLess(loop.time() - start, 5.5)

    async def test_guard_denies_delegate(self):
        with self.assertRaises(HTTPException) as caught:
            await self.call(self.make(issue(1)), "save_issue", {"title": "t", "parentId": "ROU-1", "delegate": "me"})
        self.assertEqual(caught.exception.status_code, 403)
        self.assertEqual(caught.exception.detail, "linear-app guard: delegate not allowed")

    async def test_guard_logs_lookup_failure(self):
        instance = self.make(issue(1), error=RuntimeError("boom"))
        with self.assertLogs("LiteLLM Proxy", level="WARNING") as logs:
            with self.assertRaises(HTTPException):
                await self.call(instance, "save_issue", {"title": "t", "parentId": "ROU-1"})
        self.assertIn("boom", "\n".join(logs.output))

    async def test_guard_denies_when_policy_unconfigured(self):
        instance = guard.LinearAppGuard(
            guardrail_name="linear-app-guard", event_hook="pre_mcp_call", default_on=True
        )
        with self.assertRaises(HTTPException) as caught:
            await self.call(instance, "get_issue", {"id": "ROU-1"})
        self.assertEqual(caught.exception.status_code, 403)


if __name__ == "__main__":
    unittest.main()
