import csv
import io
import json
import logging
import os
from typing import Any

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3_client = boto3.client("s3")

PROCESSED_BUCKET = os.environ.get("PROCESSED_BUCKET", "")


def parse_csv_content(content: str) -> dict[str, Any]:
    """Parse CSV text and return a structured summary."""
    reader = csv.DictReader(io.StringIO(content))
    rows = list(reader)
    fieldnames = reader.fieldnames or []

    return {
        "row_count": len(rows),
        "columns": fieldnames,
        "sample_rows": rows[:5],
    }


def process_s3_object(bucket: str, key: str) -> dict[str, Any]:
    """Download a CSV from S3, parse it, and write a summary to the processed bucket."""
    logger.info("Processing s3://%s/%s", bucket, key)

    response = s3_client.get_object(Bucket=bucket, Key=key)
    body = response["Body"].read().decode("utf-8-sig")
    summary = parse_csv_content(body)
    summary["source"] = {"bucket": bucket, "key": key}

    if PROCESSED_BUCKET:
        output_key = f"processed/{key.rsplit('/', 1)[-1].replace('.csv', '')}_summary.json"
        s3_client.put_object(
            Bucket=PROCESSED_BUCKET,
            Key=output_key,
            Body=json.dumps(summary, indent=2).encode("utf-8"),
            ContentType="application/json",
        )
        summary["output"] = {"bucket": PROCESSED_BUCKET, "key": output_key}

    return summary


def handle_s3_event(event: dict[str, Any]) -> dict[str, Any]:
    results = []
    for record in event.get("Records", []):
        s3_info = record.get("s3", {})
        bucket = s3_info.get("bucket", {}).get("name")
        key = s3_info.get("object", {}).get("key", "")

        if not bucket or not key:
            continue

        if not key.lower().endswith(".csv"):
            logger.info("Skipping non-CSV object: %s", key)
            continue

        results.append(process_s3_object(bucket, key))

    return {"processed_files": len(results), "results": results}


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    logger.info("Received event: %s", json.dumps(event))
    return handle_s3_event(event)
