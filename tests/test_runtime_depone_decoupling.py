import ast
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import venv
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WITNESSD_ROOT = ROOT / "witnessd"
IGNORED_SCAN_DIRS = {".git", ".omx", ".pytest_cache", ".ruff_cache", "__pycache__"}


def _witnessd_python_files() -> list[Path]:
    return sorted(path for path in WITNESSD_ROOT.rglob("*.py") if path.is_file())


def _source_python_files() -> list[Path]:
    files: list[Path] = []
    for path in sorted(ROOT.rglob("*.py")):
        rel = path.relative_to(ROOT)
        if any(part in IGNORED_SCAN_DIRS for part in rel.parts):
            continue
        files.append(path)
    return files


def _top_level(name: str) -> str:
    return name.split(".", 1)[0]


class TestRuntimeDeponeDecoupling(unittest.TestCase):
    def test_witnessd_runtime_has_no_depone_imports(self):
        offenders: list[str] = []
        for path in _witnessd_python_files():
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if _top_level(alias.name) == "depone":
                            offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")
                elif isinstance(node, ast.ImportFrom):
                    if node.module and _top_level(node.module) == "depone":
                        offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")

        self.assertEqual(offenders, [])

    def test_depone_imports_are_limited_to_tests_and_revalidation_scripts(self):
        offenders: list[str] = []
        for path in _source_python_files():
            rel = path.relative_to(ROOT)
            if rel.parts[0] == "tests":
                continue
            if rel.parts[0] == "scripts" and rel.name.startswith("revalidate_"):
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if _top_level(alias.name) == "depone":
                            offenders.append(f"{rel}:{node.lineno}")
                elif isinstance(node, ast.ImportFrom):
                    if node.module and _top_level(node.module) == "depone":
                        offenders.append(f"{rel}:{node.lineno}")

        self.assertEqual(offenders, [])

    def test_witnessd_runtime_imports_are_stdlib_or_witnessd(self):
        allowed = set(sys.stdlib_module_names) | {"witnessd"}
        offenders: list[str] = []
        for path in _witnessd_python_files():
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported = [_top_level(alias.name) for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    if node.level:
                        continue
                    imported = [_top_level(node.module)] if node.module else []
                else:
                    continue
                for name in imported:
                    if name and name not in allowed:
                        offenders.append(
                            f"{path.relative_to(ROOT)}:{node.lineno}: {name}"
                        )

        self.assertEqual(offenders, [])


@unittest.skipIf(shutil.which("openssl") is None, "openssl required for E2E signing")
class TestRuntimeWithoutDeponeInstalled(unittest.TestCase):
    def _venv_python(self, tmp: str) -> str:
        env_dir = Path(tmp) / "venv"
        venv.EnvBuilder(with_pip=False).create(env_dir)
        return str(env_dir / "bin" / "python")

    def test_imports_work_when_depone_is_not_importable(self):
        with tempfile.TemporaryDirectory() as tmp:
            python = self._venv_python(tmp)
            env = os.environ.copy()
            env["PYTHONPATH"] = str(ROOT)
            completed = subprocess.run(
                [
                    python,
                    "-c",
                    (
                        "import importlib.util; "
                        "assert importlib.util.find_spec('depone') is None; "
                        "import witnessd.emitter, witnessd.__main__"
                    ),
                ],
                cwd="/tmp",
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_shell_adapter_e2e_emits_pending_without_depone(self):
        with tempfile.TemporaryDirectory() as tmp:
            python = self._venv_python(tmp)
            sandbox = Path(tmp) / "sandbox"
            evidence = Path(tmp) / "evidence"
            sandbox.mkdir()
            evidence.mkdir()
            env = os.environ.copy()
            env["PYTHONPATH"] = str(ROOT)

            completed = subprocess.run(
                [
                    python,
                    "-m",
                    "witnessd",
                    "run",
                    "--adapter",
                    "shell",
                    "--runner-sandbox",
                    str(sandbox),
                    "--out",
                    str(evidence / "capture-manifest.json"),
                    "--log",
                    str(evidence / "runlog.jsonl"),
                    "--allow",
                    "out.txt",
                    "--",
                    "sh",
                    "-c",
                    "echo hi > out.txt",
                ],
                cwd="/tmp",
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn("evidence-pending", completed.stdout)
            self.assertNotIn("VERIFIED", completed.stdout)
            self.assertNotIn("DONE", completed.stdout)
            self.assertNotIn("COMPLETE", completed.stdout)
            self.assertTrue((evidence / "capture-manifest.json").exists())


if __name__ == "__main__":
    unittest.main()
