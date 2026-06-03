#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_DIR="${ROOT_DIR}/.build"
PACKAGE_DIR="${BUILD_DIR}/package"

echo "==> Building Lambda package"
rm -rf "${BUILD_DIR}"
mkdir -p "${PACKAGE_DIR}"

python3 -m pip install -r "${ROOT_DIR}/requirements.txt" -t "${PACKAGE_DIR}" --quiet
cp -r "${ROOT_DIR}/src/csv_processor" "${PACKAGE_DIR}/"

(
  cd "${PACKAGE_DIR}"
  zip -r "${BUILD_DIR}/csv-processor.zip" . -q
)

echo "Package written to ${BUILD_DIR}/csv-processor.zip"
