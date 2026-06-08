# CSV Processor Backend

AWS backend for processing CSV files using **Lambda**, **S3**, and **API Gateway**, deployed with **CloudFormation**.

## Architecture

```
┌─────────────────┐
│   FastAPI       │──────┐
│   (Your APIs)   │      │
└─────────────────┘      │
                         ▼
                ┌─────────────────┐
                │  API Gateway    │
                │  POST /process  │
                └────────┬────────┘
                         │
    Upload *.csv ──►     │     ┌──────────────────┐
                   │     ▼     │                  │
                   │  ┌────────▼───────┐          │
                   │  │ Lambda         │          │
                   └─►│ CSV Processor  │          │
                      └────────┬───────┘          │
                               │                  │
                               ▼                  │
                      ┌──────────────────┐        │
                      │ S3 Processed     │        │
                      │ (JSON summaries) │        │
                      └──────────────────┘        │
                                                  │
                      S3 Event Trigger ───────────┘
```

**Two ways to trigger Lambda:**
1. **Automatic**: Upload CSV to S3 → Lambda auto-triggered via S3 event
2. **Manual**: POST to API Gateway `/process` endpoint → Lambda processes specified file

The Lambda streams CSV files, parses rows/columns with validation, and writes JSON summaries.

## Project structure

```
backend-assessment/
├── infrastructure/
│   └── template.yml          # CloudFormation stack
├── src/csv_processor/
│   └── handler.py              # Lambda entry point
├── scripts/
│   ├── build.sh                # Package Lambda zip locally
│   └── deploy.sh               # Build, upload, and deploy stack
├── tests/
│   └── test_handler.py
├── samples/
│   └── example.csv
└── requirements.txt
```

## CloudFormation resources

| Resource | Purpose |
|---|---|
| `CsvUploadBucket` | Receives CSV uploads; triggers Lambda on `*.csv` |
| `CsvProcessedBucket` | Stores JSON processing summaries |
| `CsvProcessorFunction` | Python Lambda handler (supports both S3 and API Gateway events) |
| `CsvProcessorRole` | IAM role with S3 + CloudWatch Logs permissions |
| `CsvProcessorLogGroup` | Explicit log group with configurable retention |
| `CsvProcessorApi` | API Gateway REST API for manual Lambda invocation |
| `ApiStage` | API Gateway deployment stage |

### API Gateway Endpoint

After deployment, you can manually trigger CSV processing via API Gateway:

```bash
# Get the API endpoint from CloudFormation outputs
API_URL=$(aws cloudformation describe-stacks \
  --stack-name csv-processor-dev \
  --query 'Stacks[0].Outputs[?OutputKey==`ApiGatewayUrl`].OutputValue' \
  --output text)

# Trigger processing for a specific file
curl -X POST "$API_URL" \
  -H "Content-Type: application/json" \
  -d '{
    "bucket": "csv-processor-dev-uploads-123456",
    "key": "myfile.csv"
  }'
```

**Response example:**
```json
{
  "status": "success",
  "row_count": 150,
  "columns": ["name", "email", "age"],
  "sample_rows": [...],
  "source": {"bucket": "...", "key": "..."},
  "output": {"bucket": "...", "key": "..."}
}
```

**Use Case:** Your FastAPI application can call this API Gateway endpoint to manually trigger processing for uploaded files, or reprocess existing files.

### IAM permissions

The Lambda execution role includes:

- **`AWSLambdaBasicExecutionRole`** — write execution logs to CloudWatch
- **Custom CloudWatch policy** — `logs:CreateLogStream`, `logs:PutLogEvents` on the dedicated log group
- **Custom S3 policy** — `s3:GetObject` on upload bucket, `s3:PutObject` on processed bucket

## Prerequisites

- Python 3.11+
- AWS CLI v2 configured (`aws configure`)
- IAM permissions to create CloudFormation stacks, Lambda, S3, IAM roles, and CloudWatch Logs

## Local development

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt

# Run unit tests
PYTHONPATH=src pytest tests/ -v
```

## Deploy to AWS

```bash
chmod +x scripts/*.sh

# Optional overrides
export AWS_REGION=us-east-1
export PROJECT_NAME=csv-processor
export ENVIRONMENT=dev

./scripts/deploy.sh
```

The deploy script:

1. Builds a Lambda zip (code + dependencies)
2. Creates an artifacts S3 bucket (if needed)
3. Uploads the zip
4. Runs `aws cloudformation deploy` with named IAM capabilities

## Manual CloudFormation deploy

```bash
./scripts/build.sh

aws s3 mb s3://my-artifacts-bucket
aws s3 cp .build/csv-processor.zip s3://my-artifacts-bucket/lambda/csv-processor.zip

aws cloudformation deploy \
  --template-file infrastructure/template.yml \
  --stack-name csv-processor-dev \
  --parameter-overrides \
    ProjectName=csv-processor \
    Environment=dev \
    LambdaCodeS3Bucket=my-artifacts-bucket \
    LambdaCodeS3Key=lambda/csv-processor.zip \
  --capabilities CAPABILITY_NAMED_IAM
```

## Test after deploy

```bash
UPLOAD_BUCKET=$(aws cloudformation describe-stacks \
  --stack-name csv-processor-dev \
  --query "Stacks[0].Outputs[?OutputKey=='UploadBucketName'].OutputValue" \
  --output text)

aws s3 cp samples/example.csv "s3://${UPLOAD_BUCKET}/incoming/example.csv"
```

Check CloudWatch Logs:

```bash
aws logs tail "/aws/lambda/csv-processor-dev-csv-processor" --follow
```

## Updating the stack

After code changes, re-run `./scripts/deploy.sh`. CloudFormation updates the Lambda function with the new S3 artifact.

## Next steps (full-stack assessment)

- Add **SQS** queue between S3 and Lambda for async/buffered processing
- Wire a **FastAPI** service with **PostgreSQL/SQLAlchemy** for metadata storage
- Build a **React Admin** front-end for uploads and job status
- Add **CodePipeline** CI/CD for automated build and deploy
