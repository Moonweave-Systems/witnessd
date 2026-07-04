# W18 operator checkpoints

The agent prepares files and local commits only. The operator owns the following
external actions.

## Depone reverse-conformance PAT

Secret name expected by Depone CI:

```text
WITNESSD_REVERSE_CONFORMANCE_PAT
```

Create a GitHub fine-grained personal access token:

- Resource owner: `Moonweave-Systems`
- Repository access: selected repository `witnessd` only
- Repository permissions: `Contents: Read-only`
- Metadata: read-only, automatically included by GitHub
- No write permissions
- Expiration: operator policy, recommended 90 days or less

Register it in the Depone repository:

1. Open `Moonweave-Systems/Depone`.
2. Go to Settings -> Secrets and variables -> Actions.
3. Add repository secret `WITNESSD_REVERSE_CONFORMANCE_PAT`.
4. Paste the fine-grained token value.
5. Re-run the `witnessd reverse conformance` CI job.

This token is only for Depone CI to read the private witnessd repository until
W22 publication. Do not commit the token or print it in logs.

## Clean-machine quickstart

After the W18 branch is pushed, validate on a new Linux environment and a macOS
environment:

```bash
git clone <witnessd repo>
cd witnessd
python3 -m witnessd init --home .witnessd --depone-root <depone checkout>
WITNESSD_DEPONE_ROOT=<depone checkout> scripts/quickstart_check.sh
```

Record the platform, Python version, witnessd commit, Depone commit, and command
output in the release checklist.

## Release publication

Draft file: `docs/releases/v2.3.0-draft.md`.

Publication and tag push are operator-only.
