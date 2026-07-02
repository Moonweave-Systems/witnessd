import os
import tempfile
import unittest

from witnessd.installer import (
    ERR_WITNESSD_CONFIG_UNREADABLE,
    InstallerError,
    atomic_install,
    list_orphan_shims,
)


class TestInstaller(unittest.TestCase):
    def test_unreadable_config_fail_safe_no_overwrite(self):
        with tempfile.TemporaryDirectory() as d:
            dest = os.path.join(d, "dest")
            shim = os.path.join(d, "bin")
            os.makedirs(dest)
            os.makedirs(shim)
            existing = os.path.join(dest, "v1.txt")
            with open(existing, "w", encoding="utf-8") as handle:
                handle.write("ORIGINAL")
            payload = os.path.join(d, "payload.txt")
            with open(payload, "w", encoding="utf-8") as handle:
                handle.write("NEW")
            bad_config = os.path.join(d, "config.bin")
            with open(bad_config, "wb") as handle:
                handle.write(b"\x00\xff not json")

            with self.assertRaises(InstallerError) as cm:
                atomic_install(
                    payload_path=payload,
                    dest_dir=dest,
                    config_path=bad_config,
                    shim_dir=shim,
                    version="v2",
                )

            self.assertEqual(cm.exception.code, ERR_WITNESSD_CONFIG_UNREADABLE)
            with open(existing, encoding="utf-8") as handle:
                self.assertEqual(handle.read(), "ORIGINAL")
            self.assertEqual(os.listdir(shim), [])
            self.assertEqual(list_orphan_shims(shim, dest), [])

    def test_valid_install_atomic_and_no_orphan(self):
        with tempfile.TemporaryDirectory() as d:
            dest = os.path.join(d, "dest")
            shim = os.path.join(d, "bin")
            os.makedirs(dest)
            os.makedirs(shim)
            payload = os.path.join(d, "payload.txt")
            with open(payload, "w", encoding="utf-8") as handle:
                handle.write("NEW")
            config = os.path.join(d, "config.json")
            with open(config, "w", encoding="utf-8") as handle:
                handle.write('{"ok": true}')

            result = atomic_install(
                payload_path=payload,
                dest_dir=dest,
                config_path=config,
                shim_dir=shim,
                version="v2",
            )

            self.assertTrue(result["installed"])
            self.assertEqual(list_orphan_shims(shim, dest), [])


if __name__ == "__main__":
    unittest.main()
