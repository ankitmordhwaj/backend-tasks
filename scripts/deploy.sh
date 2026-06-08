#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_DIR="${ROOT_DIR}/.build"
PACKAGE_DIR="${BUILD_DIR}/package"
ZIP_PATH="${BUILD_DIR}/csv-processor.zip"

PROJECT_NAME="${PROJECT_NAME:-csv-processor}"
ENVIRONMENT="${ENVIRONMENT:-dev}"
AWS_REGION="${AWS_REGION:-ap-south-1}"
STACK_NAME="${STACK_NAME:-${PROJECT_NAME}-${ENVIRONMENT}}"
ARTIFACTS_BUCKET="${ARTIFACTS_BUCKET:-${PROJECT_NAME}-${ENVIRONMENT}-artifacts-$(aws sts get-caller-identity --query Account --output text)}"
LAMBDA_S3_KEY="${LAMBDA_S3_KEY:-lambda/csv-processor.zip}"

echo "==> Building Lambda deployment package"
rm -rf "${BUILD_DIR}"
mkdir -p "${PACKAGE_DIR}"

python3 -m pip install -r "${ROOT_DIR}/requirements.txt" -t "${PACKAGE_DIR}" --quiet
cp -r "${ROOT_DIR}/src/csv_processor" "${PACKAGE_DIR}/"

(
  cd "${PACKAGE_DIR}"
  zip -r "${ZIP_PATH}" . -q
)

echo "==> Ensuring artifacts bucket exists: ${ARTIFACTS_BUCKET} (region: ${AWS_REGION})"
if ! aws s3api head-bucket --bucket "${ARTIFACTS_BUCKET}" 2>/dev/null; then
  if [[ "${AWS_REGION}" == "us-east-1" ]]; then
    aws s3api create-bucket --bucket "${ARTIFACTS_BUCKET}" --region "${AWS_REGION}"
  else
    aws s3api create-bucket --bucket "${ARTIFACTS_BUCKET}" --region "${AWS_REGION}" \
      --create-bucket-configuration "LocationConstraint=${AWS_REGION}"
  fi
fi

echo "==> Uploading Lambda artifact to s3://${ARTIFACTS_BUCKET}/${LAMBDA_S3_KEY}"
aws s3 cp "${ZIP_PATH}" "s3://${ARTIFACTS_BUCKET}/${LAMBDA_S3_KEY}" --region "${AWS_REGION}"

echo "==> Deploying CloudFormation stack: ${STACK_NAME}"
aws cloudformation deploy \
  --template-file "${ROOT_DIR}/infrastructure/template.yml" \
  --stack-name "${STACK_NAME}" \
  --parameter-overrides \
    ProjectName="${PROJECT_NAME}" \
    Environment="${ENVIRONMENT}" \
    LambdaCodeS3Bucket="${ARTIFACTS_BUCKET}" \
    LambdaCodeS3Key="${LAMBDA_S3_KEY}" \
  --capabilities CAPABILITY_NAMED_IAM \
  --region "${AWS_REGION}"

echo "==> Stack outputs"
aws cloudformation describe-stacks \
  --stack-name "${STACK_NAME}" \
  --region "${AWS_REGION}" \
  --query "Stacks[0].Outputs" \
  --output table

echo "Deployment complete."
