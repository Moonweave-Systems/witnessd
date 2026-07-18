# Deviations

## Task 2

- The plan listed `tests/test_orro_workstyle.py`'s `witnessd.__main__.subprocess.run` patch for migration to `witnessd.cli.advisory.subprocess.run`. That test exercises `orro doctor`, whose `_cmd_orro_doctor` handler remains in `witnessd.__main__` for PR3. The patch therefore remains at `witnessd.__main__.subprocess.run` so it continues to follow the actual usage-site namespace.
