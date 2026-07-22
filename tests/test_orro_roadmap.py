from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from witnessd.orro_roadmap import (
    ERR_ORRO_ROADMAP_INVALID,
    ERR_ORRO_ROADMAP_ITEM_UNKNOWN,
    OrroRoadmapError,
    read_roadmap,
    read_roadmap_binding,
    seal_roadmap_binding,
    write_roadmap,
)


class OrroRoadmapTests(unittest.TestCase):
    def test_absent_roadmap_is_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(read_roadmap(Path(tmp)))

    def test_roadmap_round_trip_uses_canonical_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            roadmap = {
                "kind": "orro-roadmap",
                "schema_version": "0.1",
                "items": [
                    {
                        "id": "health-v1",
                        "title": "code-health verdict axis",
                        "status": "done",
                        "note": "shipped",
                        "spec": "docs/health.md",
                    },
                    {"id": "legibility-v1", "title": "status and tidy"},
                ],
            }

            path = write_roadmap(repo, roadmap)

            self.assertEqual(path, repo / ".orro" / "roadmap.json")
            self.assertEqual(read_roadmap(repo), roadmap)
            text = path.read_text(encoding="utf-8")
            self.assertTrue(text.endswith("\n"))
            self.assertEqual(text, json.dumps(roadmap, indent=2, sort_keys=True) + "\n")

    def test_malformed_roadmaps_raise_structured_error(self) -> None:
        invalid_payloads = [
            [],
            {"kind": "wrong", "schema_version": "0.1", "items": []},
            {"kind": "orro-roadmap", "schema_version": "9", "items": []},
            {"kind": "orro-roadmap", "schema_version": "0.1", "items": {}},
            {
                "kind": "orro-roadmap",
                "schema_version": "0.1",
                "items": [{"id": "Not Kebab", "title": "bad"}],
            },
            {
                "kind": "orro-roadmap",
                "schema_version": "0.1",
                "items": [{"id": "dup", "title": "one"}, {"id": "dup", "title": "two"}],
            },
            {
                "kind": "orro-roadmap",
                "schema_version": "0.1",
                "items": [{"id": "valid", "title": ""}],
            },
            {
                "kind": "orro-roadmap",
                "schema_version": "0.1",
                "items": [{"id": "valid", "title": "title", "status": "started"}],
            },
            {
                "kind": "orro-roadmap",
                "schema_version": "0.1",
                "items": [{"id": "valid", "title": "title", "note": 1}],
            },
        ]
        for payload in invalid_payloads:
            with self.subTest(payload=payload), tempfile.TemporaryDirectory() as tmp:
                repo = Path(tmp)
                path = repo / ".orro" / "roadmap.json"
                path.parent.mkdir(parents=True)
                path.write_text(json.dumps(payload), encoding="utf-8")

                with self.assertRaises(OrroRoadmapError) as caught:
                    read_roadmap(repo)

                self.assertEqual(caught.exception.code, ERR_ORRO_ROADMAP_INVALID)

    def test_binding_seals_ledger_hash_and_is_readable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            run_dir = root / "home" / "runs" / "run-1"
            run_dir.mkdir(parents=True)
            ledger_path = write_roadmap(
                repo,
                {
                    "kind": "orro-roadmap",
                    "schema_version": "0.1",
                    "items": [{"id": "legibility-v1", "title": "status and tidy"}],
                },
            )

            binding = seal_roadmap_binding(
                repo=repo, run_dir=run_dir, item_id="legibility-v1"
            )

            expected = {
                "kind": "orro-roadmap-binding",
                "schema_version": "0.1",
                "item_id": "legibility-v1",
                "ledger_path": ".orro/roadmap.json",
                "ledger_sha256": hashlib.sha256(ledger_path.read_bytes()).hexdigest(),
            }
            self.assertEqual(binding, expected)
            self.assertEqual(read_roadmap_binding(run_dir), expected)

    def test_absent_binding_is_none_and_malformed_binding_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            self.assertIsNone(read_roadmap_binding(run_dir))
            (run_dir / "roadmap-binding.json").write_text("{}", encoding="utf-8")

            with self.assertRaises(OrroRoadmapError) as caught:
                read_roadmap_binding(run_dir)

            self.assertEqual(caught.exception.code, ERR_ORRO_ROADMAP_INVALID)

    def test_unknown_item_fails_closed_without_binding(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            run_dir = root / "run"
            run_dir.mkdir()
            write_roadmap(
                repo,
                {
                    "kind": "orro-roadmap",
                    "schema_version": "0.1",
                    "items": [{"id": "known-item", "title": "Known"}],
                },
            )

            with self.assertRaises(OrroRoadmapError) as caught:
                seal_roadmap_binding(repo=repo, run_dir=run_dir, item_id="typo-item")

            self.assertEqual(caught.exception.code, ERR_ORRO_ROADMAP_ITEM_UNKNOWN)
            self.assertFalse((run_dir / "roadmap-binding.json").exists())


if __name__ == "__main__":
    unittest.main()
