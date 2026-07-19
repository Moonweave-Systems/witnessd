# Keyless live smoke (human-gated)

This acceptance step must be run manually. It requires an interactive GitHub
login through Sigstore's out-of-band OAuth flow and permanently publishes the
signing identity and evidence hash to the public Rekor transparency log. Rekor
entries cannot be deleted. Do not run this from CI, cron, or an automated agent.

## 1. Produce a real public-good Sigstore attestation

Choose the exact witnessd evidence file whose bytes Depone will receive. The
subject digest in the Sigstore statement binds those bytes, so do not rewrite
the file after signing.

```bash
cd /home/ubuntu/moonweave/witnessd
EVIDENCE=/absolute/path/to/capture-manifest.json
PREDICATE=/tmp/witnessd-keyless-predicate.json
BUNDLE=/tmp/witnessd-keyless-bundle.json

printf '%s\n' '{"kind":"witnessd-keyless-live-smoke","raises_assurance":false}' > "$PREDICATE"
~/.local/bin/sigstore attest "$EVIDENCE" \
  --predicate "$PREDICATE" \
  --predicate-type https://depone.dev/attestations/evidence/v1 \
  --oauth-force-oob \
  --oidc-disable-ambient-providers \
  --bundle "$BUNDLE"
```

Open the URL printed by Sigstore, enter the displayed code, and complete the
GitHub login. Success requires `$BUNDLE` to contain a Sigstore v0.3 DSSE bundle;
terminal narration alone is not acceptance evidence.

## 2. Verify the exact bytes offline with Depone

Prepare an identity policy containing the exact issuer and subject from the
Fulcio certificate, and use the pinned production trusted root matching the
public-good bundle.

```bash
cd /home/ubuntu/moonweave/witnessd
EVIDENCE=/absolute/path/to/capture-manifest.json \
BUNDLE=/tmp/witnessd-keyless-bundle.json \
POLICY=/absolute/path/to/identity-policy.json \
TRUSTED_ROOT=/absolute/path/to/prod-trusted-root.json \
PYTHONPATH=../depone PYTHONNOUSERSITE=1 /usr/bin/python3 - <<'PY'
import json
import os
from pathlib import Path

from depone.agent_fabric.keyless_verify import verify_keyless_bundle

verdict = verify_keyless_bundle(
    json.loads(Path(os.environ["BUNDLE"]).read_text(encoding="utf-8")),
    Path(os.environ["EVIDENCE"]).read_bytes(),
    json.loads(Path(os.environ["POLICY"]).read_text(encoding="utf-8")),
    json.loads(Path(os.environ["TRUSTED_ROOT"]).read_text(encoding="utf-8")),
)
print(json.dumps(verdict, indent=2, sort_keys=True))
raise SystemExit(
    0
    if verdict.get("decision") == "pass"
    and verdict.get("anchor_class") == "keyless-transparency-logged"
    else 1
)
PY
```

The human-gated acceptance result is Depone reporting `decision: pass` and
`anchor_class: keyless-transparency-logged`. This verifies the signing anchor;
it does not raise or alter the A0/A1/A2 observation-assurance level.
