# Deviations

## Task 2

- The plan listed `tests/test_orro_workstyle.py`'s `witnessd.__main__.subprocess.run` patch for migration to `witnessd.cli.advisory.subprocess.run`. That test exercises `orro doctor`, whose `_cmd_orro_doctor` handler remains in `witnessd.__main__` for PR3. The patch therefore remains at `witnessd.__main__.subprocess.run` so it continues to follow the actual usage-site namespace.

## Task 4

- The lifecycle extraction stops because `_cmd_team_kill` remains in `witnessd.__main__` and directly calls `_cmd_kill`. Moving `_cmd_kill` to `witnessd.cli.runtime_ops` would require editing the `_cmd_team_kill` body or re-exporting `_cmd_kill` from `witnessd.__main__`; both are forbidden by the move-only and no-re-export rules. No Task 4 handler was moved.
