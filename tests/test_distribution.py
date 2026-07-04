import io
import json
import os
import stat
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from witnessd.__main__ import main
from witnessd.distribution import (
    ERR_WITNESSD_DEPONE_PIN_MISMATCH,
    InitConfig,
    ProvisionError,
    init_witnessd_home,
    validate_depone_pin,
)


class DistributionInitTests(unittest.TestCase):
    def test_init_records_config_keys_and_repo_hashes(self) -> None:
        witnessd_root = Path(__file__).resolve().parents[1]
        depone_root = witnessd_root.parent / "depone"
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

    def test_validate_depone_pin_rejects_forged_hash(self) -> None:
        witnessd_root = Path(__file__).resolve().parents[1]
        depone_root = witnessd_root.parent / "depone"
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

    def test_cli_init_writes_home_and_prints_config_path(self) -> None:
        witnessd_root = Path(__file__).resolve().parents[1]
        depone_root = witnessd_root.parent / "depone"
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

    def test_cli_init_auto_detects_sibling_depone_checkout(self) -> None:
        witnessd_root = Path(__file__).resolve().parents[1]
        depone_root = witnessd_root.parent / "depone"
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            out = io.StringIO()
            err = io.StringIO()

            with redirect_stdout(out), redirect_stderr(err):
                code = main(["init", "--home", str(home)])

            self.assertEqual(code, 0, err.getvalue())
            provision = json.loads((home / "provision.json").read_text(encoding="utf-8"))
            self.assertEqual(provision["depone"]["root"], str(depone_root.resolve()))
            self.assertEqual(provision["depone"]["source"], "sibling-checkout")
            self.assertFalse(provision["depone"]["network_used"])


if __name__ == "__main__":
    unittest.main()
