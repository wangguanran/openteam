#!/bin/bash
set -euo pipefail

# Source: https://raw.githubusercontent.com/temporalio/samples-server/main/compose/scripts/create-namespace.sh
# Copied for reproducibility; treat as external input, review before use.

TEMPORAL_ADDRESS=${TEMPORAL_ADDRESS:-temporal:7233}
DEFAULT_NAMESPACE=${DEFAULT_NAMESPACE:-default}

set +e
temporal operator namespace describe -n "${DEFAULT_NAMESPACE}" --address "${TEMPORAL_ADDRESS}"
RET=$?
set -e

if [[ ${RET} -ne 0 ]]; then
  echo "Namespace ${DEFAULT_NAMESPACE} does not exist. Creating..."
  temporal operator namespace create -n "${DEFAULT_NAMESPACE}" --address "${TEMPORAL_ADDRESS}"
  echo "Namespace ${DEFAULT_NAMESPACE} created."
else
  echo "Namespace ${DEFAULT_NAMESPACE} already exists."
fi

