#!/usr/bin/env bash
set -euo pipefail

# W12 real A2 host preparation.
#
# This script is intentionally privileged host setup. Codex must not run it.
# Operator command:
#
#   sudo bash scripts/setup_a2_host.sh
#
# Defaults match the Moonweave W12 proof host:
# - runner user: ubuntu, expected uid: 1001
# - observer user: witnessd-observer
# - observer dir: /var/lib/witnessd/a2-observer, mode 0700
#
# Minimal sudoers installed for the fixed proof run:
#
#   witnessd-observer ALL=(ubuntu) NOPASSWD: /usr/bin/true
#
# That entry lets the observer uid launch only the no-op runner command used by
# the W12 proof fixture. Broader lane commands require an explicit operator
# review and a different sudoers entry.

OBSERVER_USER="${OBSERVER_USER:-witnessd-observer}"
OBSERVER_DIR="${OBSERVER_DIR:-/var/lib/witnessd/a2-observer}"
RUNNER_USER="${RUNNER_USER:-ubuntu}"
EXPECTED_RUNNER_UID="${EXPECTED_RUNNER_UID:-1001}"
RUNNER_COMMAND="${RUNNER_COMMAND:-/usr/bin/true}"
SUDOERS_FILE="${SUDOERS_FILE:-/etc/sudoers.d/witnessd-a2-observer}"

for required in id install mktemp sudo useradd visudo; do
  if ! command -v "${required}" >/dev/null 2>&1; then
    echo "ERR_REQUIRED_COMMAND_MISSING: ${required}" >&2
    exit 2
  fi
done

if [[ "$(id -u)" -ne 0 ]]; then
  echo "ERR_ROOT_REQUIRED: run with sudo" >&2
  exit 2
fi

if ! id "${RUNNER_USER}" >/dev/null 2>&1; then
  echo "ERR_RUNNER_USER_MISSING: ${RUNNER_USER}" >&2
  exit 2
fi

actual_runner_uid="$(id -u "${RUNNER_USER}")"
if [[ "${actual_runner_uid}" != "${EXPECTED_RUNNER_UID}" ]]; then
  echo "ERR_RUNNER_UID_MISMATCH: ${RUNNER_USER} uid is ${actual_runner_uid}, expected ${EXPECTED_RUNNER_UID}" >&2
  exit 2
fi

if [[ ! -x "${RUNNER_COMMAND}" ]]; then
  echo "ERR_RUNNER_COMMAND_NOT_EXECUTABLE: ${RUNNER_COMMAND}" >&2
  exit 2
fi

if ! id "${OBSERVER_USER}" >/dev/null 2>&1; then
  useradd \
    --system \
    --home-dir "${OBSERVER_DIR}" \
    --shell /usr/sbin/nologin \
    --no-create-home \
    "${OBSERVER_USER}"
fi

install -d -o "${OBSERVER_USER}" -g "${OBSERVER_USER}" -m 0700 "${OBSERVER_DIR}"

observer_uid="$(id -u "${OBSERVER_USER}")"
if [[ "${observer_uid}" == "${actual_runner_uid}" ]]; then
  echo "ERR_OBSERVER_RUNNER_UID_EQUAL: ${OBSERVER_USER} and ${RUNNER_USER} share uid ${observer_uid}" >&2
  exit 2
fi

if sudo -n -u "${RUNNER_USER}" test -w "${OBSERVER_DIR}" 2>/dev/null; then
  echo "ERR_OBSERVER_DIR_WRITABLE_BY_RUNNER: ${RUNNER_USER} can write ${OBSERVER_DIR}" >&2
  exit 2
fi

tmp_sudoers="$(mktemp)"
cat >"${tmp_sudoers}" <<EOF
# W12 witnessd real A2 proof: allow the dedicated observer uid to launch only
# the fixed no-op runner command as the runner uid.
${OBSERVER_USER} ALL=(${RUNNER_USER}) NOPASSWD: ${RUNNER_COMMAND}
EOF
visudo -cf "${tmp_sudoers}" >/dev/null
install -o root -g root -m 0440 "${tmp_sudoers}" "${SUDOERS_FILE}"
rm -f "${tmp_sudoers}"
visudo -cf "${SUDOERS_FILE}" >/dev/null

echo "W12 A2 host prep complete"
echo "observer_user=${OBSERVER_USER}"
echo "observer_uid=${observer_uid}"
echo "observer_dir=${OBSERVER_DIR}"
echo "observer_dir_mode=0700"
echo "runner_user=${RUNNER_USER}"
echo "runner_uid=${actual_runner_uid}"
echo "runner_cannot_write_observer_dir=true"
echo "sudoers_file=${SUDOERS_FILE}"
echo "sudoers_entry=${OBSERVER_USER} ALL=(${RUNNER_USER}) NOPASSWD: ${RUNNER_COMMAND}"
