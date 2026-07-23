import io
import hashlib
import importlib.metadata
import json
import os
import stat
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import witnessd.cli.bootstrap as witnessd_cli
from witnessd.__main__ import main
from witnessd.distribution import (
    DEFAULT_DEPONE_REF,
    ERR_WITNESSD_DEPONE_PIN_MISMATCH,
    InitConfig,
    ProvisionError,
    classify_depone_pin_state,
    init_witnessd_home,
    build_orro_engine_lock,
    validate_depone_pin,
)


class DistributionInitTests(unittest.TestCase):
    def test_default_depone_ref_pins_v023_lane_intent_support(self) -> None:
        self.assertEqual(
            DEFAULT_DEPONE_REF,
            "f067a05299f755fe6b3c4b86aace2aa8822cc711",
        )
        self.assertRegex(DEFAULT_DEPONE_REF, r"^[0-9a-f]{40}$")

    def _depone_root(self) -> Path:
        env_root = os.environ.get("WITNESSD_DEPONE_ROOT")
        if env_root:
            return Path(env_root)
        witnessd_root = Path(__file__).resolve().parents[1]
        return witnessd_root.parent / "depone"

    def _seed_git_repo(self, root: Path, files: dict[str, str]) -> None:
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
        subprocess.run(
            ["git", "config", "user.email", "w18@example.invalid"],
            cwd=root,
            check=True,
        )
        subprocess.run(["git", "config", "user.name", "w18"], cwd=root, check=True)
        for rel, text in files.items():
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=root, check=True)
        subprocess.run(["git", "commit", "-qm", "seed"], cwd=root, check=True)

    def _commit_file(self, root: Path, relative_path: str, text: str) -> str:
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        subprocess.run(["git", "add", relative_path], cwd=root, check=True)
        subprocess.run(
            ["git", "commit", "-qm", f"update {relative_path}"], cwd=root, check=True
        )
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()

    def test_init_records_config_keys_and_repo_hashes(self) -> None:
        witnessd_root = Path(__file__).resolve().parents[1]
        depone_root = self._depone_root()
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"

            result = init_witnessd_home(
                InitConfig(
                    home=home,
                    witnessd_root=witnessd_root,
                    depone_root=depone_root,
                    network_allowed=False,
                )
            )

            config_path = home / "config.json"
            provision_path = home / "provision.json"
            keys_dir = home / "keys"
            self.assertEqual(result["config"], str(config_path))
            self.assertTrue(config_path.is_file())
            self.assertTrue(provision_path.is_file())
            self.assertTrue(keys_dir.is_dir())
            self.assertEqual(stat.S_IMODE(keys_dir.stat().st_mode), 0o700)
            private_key = keys_dir / "operator-private-key.placeholder"
            self.assertTrue(private_key.is_file())
            self.assertEqual(stat.S_IMODE(private_key.stat().st_mode), 0o600)

            provision = json.loads(provision_path.read_text(encoding="utf-8"))
            self.assertEqual(provision["kind"], "witnessd-depone-provision")
            self.assertEqual(provision["depone"]["root"], str(depone_root.resolve()))
            self.assertEqual(provision["depone"]["network_used"], False)
            self.assertRegex(provision["witnessd"]["commit"], r"^[0-9a-f]{40}$")
            self.assertRegex(provision["depone"]["commit"], r"^[0-9a-f]{40}$")

    def test_init_records_unknown_witnessd_commit_when_root_is_not_a_git_checkout(
        self,
    ) -> None:
        # An installed witnessd (pip into site-packages) is not a git checkout,
        # so `git rev-parse HEAD` fails there. init must still succeed and record
        # the witnessd commit as "unknown" rather than aborting, while the depone
        # commit stays strict.
        depone_root = self._depone_root()
        with tempfile.TemporaryDirectory() as tmp:
            non_git_root = Path(tmp) / "site-packages"
            non_git_root.mkdir()
            home = Path(tmp) / "home"

            init_witnessd_home(
                InitConfig(
                    home=home,
                    witnessd_root=non_git_root,
                    depone_root=depone_root,
                    network_allowed=False,
                )
            )

            provision = json.loads(
                (home / "provision.json").read_text(encoding="utf-8")
            )
            self.assertEqual(provision["witnessd"]["commit"], "unknown")
            self.assertRegex(provision["depone"]["commit"], r"^[0-9a-f]{40}$")

    def test_engine_lock_uses_pip_identity_for_non_git_witnessd_root(self) -> None:
        depone_root = self._depone_root()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "home"
            package_root = root / "site-packages" / "witnessd"
            package_root.mkdir(parents=True)
            init_witnessd_home(
                InitConfig(
                    home=home,
                    witnessd_root=package_root,
                    depone_root=depone_root,
                    network_allowed=False,
                )
            )

            lock = build_orro_engine_lock(home=home, witnessd_root=package_root)

            self.assertEqual(
                lock["witnessd"],
                {
                    "repository": "Moonweave-Systems/witnessd",
                    "commit": None,
                    "version": importlib.metadata.version("witnessd"),
                    "source": "pip-package",
                },
            )

    def test_engine_lock_compares_version_when_pip_commit_is_null(self) -> None:
        depone_root = self._depone_root()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "home"
            package_root = root / "site-packages" / "witnessd"
            package_root.mkdir(parents=True)
            init_witnessd_home(
                InitConfig(
                    home=home,
                    witnessd_root=package_root,
                    depone_root=depone_root,
                    network_allowed=False,
                )
            )
            lock = build_orro_engine_lock(home=home, witnessd_root=package_root)
            lock["witnessd"]["version"] = "0.0.0-mismatch"
            lock_path = root / "lock.json"
            lock_path.write_text(json.dumps(lock), encoding="utf-8")

            from witnessd.distribution import check_orro_engine_lock

            result = check_orro_engine_lock(
                home=home, witnessd_root=package_root, lock_path=lock_path
            )

            self.assertFalse(result["locked"])
            self.assertIn(
                "witnessd.version",
                {entry["field"] for entry in result["mismatches"]},
            )

    def test_validate_depone_pin_rejects_forged_hash(self) -> None:
        witnessd_root = Path(__file__).resolve().parents[1]
        depone_root = self._depone_root()
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            init_witnessd_home(
                InitConfig(
                    home=home,
                    witnessd_root=witnessd_root,
                    depone_root=depone_root,
                    network_allowed=False,
                )
            )
            provision_path = home / "provision.json"
            provision = json.loads(provision_path.read_text(encoding="utf-8"))
            provision["depone"]["commit"] = "0" * 40
            provision_path.write_text(
                json.dumps(provision, sort_keys=True), encoding="utf-8"
            )

            with self.assertRaises(ProvisionError) as cm:
                validate_depone_pin(home)

            self.assertEqual(cm.exception.code, ERR_WITNESSD_DEPONE_PIN_MISMATCH)

    def test_classify_depone_pin_distinguishes_stale_upgrade_from_forged(self) -> None:
        witnessd_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            depone_root = root / "depone"
            depone_root.mkdir()
            self._seed_git_repo(depone_root, {"depone/__init__.py": "old\n"})
            old_commit = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=depone_root,
                text=True,
                capture_output=True,
                check=True,
            ).stdout.strip()
            home = root / "home"
            init_witnessd_home(
                InitConfig(
                    home=home,
                    witnessd_root=witnessd_root,
                    depone_root=depone_root,
                    network_allowed=False,
                )
            )
            self.assertEqual(classify_depone_pin_state(home)["state"], "ok")
            new_commit = self._commit_file(depone_root, "depone/__init__.py", "new\n")

            with patch.dict(os.environ, {"WITNESSD_DEPONE_REF": new_commit}):
                stale = classify_depone_pin_state(home)

            self.assertEqual(
                stale,
                {
                    "state": "stale-upgrade",
                    "code": ERR_WITNESSD_DEPONE_PIN_MISMATCH,
                    "depone_root": str(depone_root.resolve()),
                    "recorded_commit": old_commit,
                    "current_commit": new_commit,
                    "expected_commit": new_commit,
                },
            )
            with self.assertRaises(ProvisionError) as cm:
                validate_depone_pin(home)
            self.assertEqual(cm.exception.code, ERR_WITNESSD_DEPONE_PIN_MISMATCH)

            provision_path = home / "provision.json"
            provision = json.loads(provision_path.read_text(encoding="utf-8"))
            provision["depone"]["commit"] = "0" * 40
            provision_path.write_text(json.dumps(provision), encoding="utf-8")

            with patch.dict(os.environ, {"WITNESSD_DEPONE_REF": new_commit}):
                forged = classify_depone_pin_state(home)

            self.assertEqual(forged["state"], "mismatch")
            self.assertEqual(forged["recorded_commit"], "0" * 40)
            self.assertEqual(forged["current_commit"], new_commit)
            self.assertEqual(forged["expected_commit"], new_commit)
            self.assertEqual(
                classify_depone_pin_state(root / "missing-home")["state"],
                "missing",
            )

    def test_doctor_guides_only_stale_upgrade_and_init_preserves_runs(self) -> None:
        witnessd_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            depone_root = root / "depone"
            depone_root.mkdir()
            self._seed_git_repo(depone_root, {"depone/__init__.py": "old\n"})
            home = root / "home"
            init_witnessd_home(
                InitConfig(
                    home=home,
                    witnessd_root=witnessd_root,
                    depone_root=depone_root,
                    network_allowed=False,
                )
            )
            prior_artifact = home / "runs" / "prior-run" / "evidence.json"
            prior_artifact.parent.mkdir(parents=True)
            prior_artifact.write_text('{"observed": true}\n', encoding="utf-8")
            prior_bytes = prior_artifact.read_bytes()
            new_commit = self._commit_file(depone_root, "depone/__init__.py", "new\n")

            stdout = io.StringIO()
            with (
                patch.dict(os.environ, {"WITNESSD_DEPONE_REF": new_commit}),
                patch(
                    "witnessd.preflight.probe_adapter_capability",
                    return_value={"decision": "pass"},
                ),
                redirect_stdout(stdout),
            ):
                doctor_code = main(
                    [
                        "orro",
                        "doctor",
                        "--home",
                        str(home),
                        "--adapter",
                        "codex",
                        "--json",
                    ]
                )

            self.assertEqual(doctor_code, 1)
            doctor = json.loads(stdout.getvalue())
            depone_pin = {check["name"]: check for check in doctor["checks"]}[
                "depone_pin"
            ]
            self.assertEqual(depone_pin["status"], "blocked")
            self.assertEqual(depone_pin["reason"], "stale-upgrade-provision")
            self.assertEqual(depone_pin["current_commit"], new_commit)
            self.assertEqual(depone_pin["expected_commit"], new_commit)
            self.assertTrue(depone_pin["remediation"]["requires_explicit_user_action"])
            self.assertIn("python3 -m orro init", depone_pin["remediation"]["command"])
            self.assertIn(str(depone_root), depone_pin["remediation"]["command"])
            self.assertIn("stale", depone_pin["detail"].lower())

            human_stdout = io.StringIO()
            human_stderr = io.StringIO()
            with (
                patch.dict(os.environ, {"WITNESSD_DEPONE_REF": new_commit}),
                patch(
                    "witnessd.preflight.probe_adapter_capability",
                    return_value={"decision": "pass"},
                ),
                redirect_stdout(human_stdout),
                redirect_stderr(human_stderr),
            ):
                human_code = main(
                    [
                        "orro",
                        "doctor",
                        "--home",
                        str(home),
                        "--adapter",
                        "codex",
                    ]
                )
            self.assertEqual(human_code, 1)
            self.assertIn("stale-upgrade-provision", human_stderr.getvalue())
            self.assertIn("python3 -m orro init", human_stderr.getvalue())
            json.loads(human_stdout.getvalue())

            init_stdout = io.StringIO()
            with redirect_stdout(init_stdout):
                init_code = main(
                    [
                        "orro",
                        "init",
                        "--home",
                        str(home),
                        "--repo",
                        str(repo),
                        "--depone-root",
                        str(depone_root),
                    ]
                )
            self.assertEqual(init_code, 0, init_stdout.getvalue())
            self.assertEqual(prior_artifact.read_bytes(), prior_bytes)
            self.assertEqual(validate_depone_pin(home)["depone"]["commit"], new_commit)

            proofrun_stdout = io.StringIO()
            proofrun_stderr = io.StringIO()
            with redirect_stdout(proofrun_stdout), redirect_stderr(proofrun_stderr):
                proofrun_code = main(
                    [
                        "orro",
                        "proofrun",
                        "--goal",
                        "pin gate smoke",
                        "--repo",
                        str(repo),
                        "--home",
                        str(home),
                        "--json",
                    ]
                )
            self.assertEqual(proofrun_code, 2)
            self.assertEqual(
                json.loads(proofrun_stdout.getvalue())["error"]["code"],
                "ERR_ORRO_PROOFRUN_NO_PLAN",
            )
            self.assertNotIn(
                ERR_WITNESSD_DEPONE_PIN_MISMATCH, proofrun_stderr.getvalue()
            )

    def test_doctor_does_not_offer_init_for_forged_provision(self) -> None:
        witnessd_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            depone_root = root / "depone"
            depone_root.mkdir()
            self._seed_git_repo(depone_root, {"depone/__init__.py": "base\n"})
            home = root / "home"
            init_witnessd_home(
                InitConfig(
                    home=home,
                    witnessd_root=witnessd_root,
                    depone_root=depone_root,
                    network_allowed=False,
                )
            )
            subprocess.run(
                ["git", "checkout", "-qb", "forged"],
                cwd=depone_root,
                check=True,
            )
            forged_commit = self._commit_file(
                depone_root, "depone/__init__.py", "unrelated\n"
            )
            subprocess.run(
                ["git", "checkout", "-q", "main"],
                cwd=depone_root,
                check=True,
            )
            current_commit = self._commit_file(
                depone_root, "depone/__init__.py", "expected\n"
            )
            provision_path = home / "provision.json"
            provision = json.loads(provision_path.read_text(encoding="utf-8"))
            provision["depone"]["commit"] = forged_commit
            provision_path.write_text(json.dumps(provision), encoding="utf-8")

            stdout = io.StringIO()
            with (
                patch.dict(os.environ, {"WITNESSD_DEPONE_REF": current_commit}),
                patch(
                    "witnessd.preflight.probe_adapter_capability",
                    return_value={"decision": "pass"},
                ),
                redirect_stdout(stdout),
            ):
                code = main(
                    [
                        "orro",
                        "doctor",
                        "--home",
                        str(home),
                        "--adapter",
                        "codex",
                        "--json",
                    ]
                )

            self.assertEqual(code, 1)
            doctor = json.loads(stdout.getvalue())
            depone_pin = {check["name"]: check for check in doctor["checks"]}[
                "depone_pin"
            ]
            self.assertEqual(depone_pin["status"], "blocked")
            self.assertEqual(depone_pin["reason"], "depone-pin-mismatch")
            self.assertNotIn("remediation", depone_pin)

    def test_cli_init_writes_home_and_prints_config_path(self) -> None:
        depone_root = self._depone_root()
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            out = io.StringIO()
            err = io.StringIO()

            with redirect_stdout(out), redirect_stderr(err):
                code = main(
                    [
                        "init",
                        "--home",
                        str(home),
                        "--depone-root",
                        str(depone_root),
                    ]
                )

            self.assertEqual(code, 0, err.getvalue())
            payload = json.loads(out.getvalue())
            self.assertEqual(payload["config"], str(home / "config.json"))
            self.assertTrue((home / "provision.json").is_file())

    def test_cli_init_records_validated_team_ref(self) -> None:
        depone_root = self._depone_root()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "home"
            team_path = root / ".orro" / "team.json"
            team_path.parent.mkdir()
            team_payload = {
                "kind": "moonweave-rolepack",
                "schema_version": "0.2",
                "name": "custom-team",
                "grants": [
                    {
                        "role_id": "runner",
                        "capability": "execute",
                        "adapters": ["shell"],
                        "model": "team-runner-model",
                    }
                ],
            }
            team_bytes = json.dumps(team_payload, sort_keys=True).encode("utf-8")
            team_path.write_bytes(team_bytes)
            out = io.StringIO()
            err = io.StringIO()

            with redirect_stdout(out), redirect_stderr(err):
                code = main(
                    [
                        "init",
                        "--home",
                        str(home),
                        "--depone-root",
                        str(depone_root),
                        "--team",
                        str(team_path),
                    ]
                )

            self.assertEqual(code, 0, err.getvalue())
            provision = json.loads(
                (home / "provision.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                provision["team_ref"],
                {
                    "path": str(team_path),
                    "sha256": hashlib.sha256(team_bytes).hexdigest(),
                    "kind": "moonweave-rolepack",
                    "schema_version": "0.2",
                    "name": "custom-team",
                },
            )

    def test_cli_init_auto_detects_sibling_depone_checkout(self) -> None:
        # Build a synthetic sibling layout so auto-detection is covered on
        # any machine (CI has no real sibling depone checkout).
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            witnessd_root = base / "witnessd"
            depone_root = base / "depone"
            witnessd_root.mkdir()
            depone_root.mkdir()
            self._seed_git_repo(
                witnessd_root,
                {"witnessd/__init__.py": "", "witnessd/__main__.py": ""},
            )
            self._seed_git_repo(depone_root, {"depone/__init__.py": ""})
            home = base / "home"
            out = io.StringIO()
            err = io.StringIO()

            with (
                patch.dict(os.environ, {"WITNESSD_DEPONE_ROOT": ""}),
                patch.object(
                    witnessd_cli,
                    "__file__",
                    str(witnessd_root / "witnessd" / "__main__.py"),
                ),
            ):
                os.environ.pop("WITNESSD_DEPONE_ROOT", None)
                with redirect_stdout(out), redirect_stderr(err):
                    code = main(["init", "--home", str(home)])

            self.assertEqual(code, 0, err.getvalue())
            payload = json.loads(out.getvalue())
            self.assertEqual(payload["provision"], str(home / "provision.json"))

            provision = json.loads(
                (home / "provision.json").read_text(encoding="utf-8")
            )
            self.assertEqual(provision["depone"]["root"], str(depone_root.resolve()))
            self.assertEqual(provision["depone"]["source"], "sibling-checkout")
            self.assertFalse(provision["depone"]["network_used"])

    def test_init_allow_network_provisions_depone_when_no_local_checkout_exists(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_witnessd = root / "isolated" / "witnessd"
            fake_depone_remote = root / "remote-depone"
            fake_witnessd.mkdir(parents=True)
            fake_depone_remote.mkdir()
            self._seed_git_repo(fake_witnessd, {"README.md": "witnessd\n"})
            self._seed_git_repo(
                fake_depone_remote,
                {
                    "depone/__init__.py": "",
                    "README.md": "depone\n",
                },
            )
            home = root / "home"

            with patch.dict(os.environ, {"WITNESSD_DEPONE_ROOT": ""}):
                os.environ.pop("WITNESSD_DEPONE_ROOT", None)
                result = init_witnessd_home(
                    InitConfig(
                        home=home,
                        witnessd_root=fake_witnessd,
                        network_allowed=True,
                        depone_repository=str(fake_depone_remote),
                        depone_ref="main",
                    )
                )

            provision = json.loads(
                Path(result["provision"]).read_text(encoding="utf-8")
            )
            depone_root = Path(provision["depone"]["root"])
            self.assertEqual(depone_root, home.resolve() / "depone-pinned")
            self.assertTrue((depone_root / "depone").is_dir())
            self.assertEqual(provision["depone"]["source"], "setup-clone")
            self.assertTrue(provision["depone"]["network_used"])
            self.assertRegex(provision["depone"]["commit"], r"^[0-9a-f]{40}$")

    def test_orro_setup_provisions_depone_and_writes_engine_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_witnessd = root / "isolated" / "witnessd"
            fake_depone_remote = root / "remote-depone"
            fake_witnessd.mkdir(parents=True)
            fake_depone_remote.mkdir()
            self._seed_git_repo(fake_witnessd, {"README.md": "witnessd\n"})
            self._seed_git_repo(
                fake_depone_remote,
                {
                    "depone/__init__.py": "",
                    "README.md": "depone\n",
                },
            )
            home = root / "home"
            out = io.StringIO()
            err = io.StringIO()

            with (
                patch.dict(os.environ, {"WITNESSD_DEPONE_ROOT": ""}),
                patch.object(
                    witnessd_cli,
                    "__file__",
                    str(fake_witnessd / "witnessd" / "__main__.py"),
                ),
            ):
                os.environ.pop("WITNESSD_DEPONE_ROOT", None)
                with redirect_stdout(out), redirect_stderr(err):
                    code = main(
                        [
                            "orro",
                            "setup",
                            "--home",
                            str(home),
                            "--depone-repository",
                            str(fake_depone_remote),
                            "--depone-ref",
                            "main",
                            "--json",
                            "--yes",
                        ]
                    )

            self.assertEqual(code, 0, err.getvalue())
            payload = json.loads(out.getvalue())
            self.assertEqual(payload["kind"], "orro-setup-result")
            self.assertEqual(payload["command"], "orro setup")
            self.assertEqual(payload["home"], str(home.resolve(strict=False)))
            self.assertTrue((home / "provision.json").is_file())
            self.assertTrue((home / "orro-engine-lock.json").is_file())
            self.assertEqual(
                payload["engine_lock"], str(home / "orro-engine-lock.json")
            )
            self.assertEqual(payload["depone_source"], "setup-clone")
            self.assertTrue(payload["depone_network_used"])
            self.assertRegex(payload["depone_commit"], r"^[0-9a-f]{40}$")
            self.assertIn("python3 -m orro doctor", payload["next_steps"][0])

    def test_orro_setup_with_depone_root_uses_local_checkout_without_network(
        self,
    ) -> None:
        depone_root = self._depone_root()
        depone_commit = subprocess.run(
            ["git", "-C", str(depone_root), "rev-parse", "HEAD"],
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            out = io.StringIO()
            err = io.StringIO()

            with redirect_stdout(out), redirect_stderr(err):
                code = main(
                    [
                        "orro",
                        "setup",
                        "--home",
                        str(home),
                        "--depone-root",
                        str(depone_root),
                        "--depone-ref",
                        depone_commit,
                        "--json",
                    ]
                )

            self.assertEqual(code, 0, err.getvalue())
            payload = json.loads(out.getvalue())
            self.assertEqual(payload["depone_root"], str(depone_root.resolve()))
            self.assertEqual(payload["depone_source"], "local-checkout")
            self.assertFalse(payload["depone_network_used"])
            self.assertTrue((home / "provision.json").is_file())
            self.assertTrue((home / "orro-engine-lock.json").is_file())

    def test_orro_setup_rejects_local_depone_checkout_that_misses_default_pin(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            depone_root = root / "depone"
            depone_root.mkdir()
            self._seed_git_repo(
                depone_root,
                {
                    "depone/__init__.py": "",
                    "README.md": "arbitrary depone checkout\n",
                },
            )
            home = root / "home"
            out = io.StringIO()
            err = io.StringIO()

            with redirect_stdout(out), redirect_stderr(err):
                code = main(
                    [
                        "orro",
                        "setup",
                        "--home",
                        str(home),
                        "--depone-root",
                        str(depone_root),
                        "--json",
                    ]
                )

            self.assertEqual(code, 2)
            self.assertEqual(err.getvalue(), "")
            self.assertEqual(
                json.loads(out.getvalue())["error"]["code"],
                "ERR_ORRO_SETUP_DEPONE_PIN_MISMATCH",
            )
            self.assertFalse((home / "orro-engine-lock.json").exists())

    def test_orro_setup_then_doctor_checks_default_engine_lock(self) -> None:
        depone_root = self._depone_root()
        depone_commit = subprocess.run(
            ["git", "-C", str(depone_root), "rev-parse", "HEAD"],
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            setup_out = io.StringIO()
            doctor_out = io.StringIO()

            with redirect_stdout(setup_out):
                setup_code = main(
                    [
                        "orro",
                        "setup",
                        "--home",
                        str(home),
                        "--depone-root",
                        str(depone_root),
                        "--depone-ref",
                        depone_commit,
                        "--json",
                    ]
                )
            with redirect_stdout(doctor_out):
                doctor_code = main(
                    [
                        "orro",
                        "doctor",
                        "--home",
                        str(home),
                        "--adapter",
                        "codex",
                        "--json",
                    ]
                )

            self.assertEqual(setup_code, 0, setup_out.getvalue())
            self.assertEqual(doctor_code, 0, doctor_out.getvalue())
            checks = {
                check["name"]: check
                for check in json.loads(doctor_out.getvalue())["checks"]
            }
            self.assertEqual(checks["engine_lock"]["status"], "pass")
            self.assertTrue(checks["engine_lock"]["locked"])

    def test_orro_setup_provision_failure_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_witnessd = root / "isolated" / "witnessd"
            fake_witnessd.mkdir(parents=True)
            self._seed_git_repo(fake_witnessd, {"README.md": "witnessd\n"})
            home = root / "home"
            out = io.StringIO()
            err = io.StringIO()

            with (
                patch.dict(os.environ, {"WITNESSD_DEPONE_ROOT": ""}),
                patch.object(
                    witnessd_cli,
                    "__file__",
                    str(fake_witnessd / "witnessd" / "__main__.py"),
                ),
            ):
                os.environ.pop("WITNESSD_DEPONE_ROOT", None)
                with redirect_stdout(out), redirect_stderr(err):
                    code = main(
                        [
                            "orro",
                            "setup",
                            "--home",
                            str(home),
                            "--depone-repository",
                            str(root / "missing-depone"),
                            "--json",
                        ]
                    )

            self.assertEqual(code, 2)
            payload = json.loads(out.getvalue())
            self.assertEqual(
                payload["error"]["code"], "ERR_WITNESSD_DEPONE_PROVISION_FAILED"
            )
            self.assertFalse((home / "orro-engine-lock.json").exists())


if __name__ == "__main__":
    unittest.main()
