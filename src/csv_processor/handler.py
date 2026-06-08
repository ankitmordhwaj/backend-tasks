import csv
import io
import json
import logging
import os
from typing import Any, Iterator
from urllib.parse import unquote_plus

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3_client = boto3.client("s3")

PROCESSED_BUCKET = os.environ.get("PROCESSED_BUCKET", "")


class CSVProcessingError(Exception):
    """Custom exception for CSV processing errors."""
    pass


def parse_csv_stream(stream: Iterator[str]) -> dict[str, Any]:
    """Parse CSV from a stream and return a structured summary without loading all rows into memory."""
    try:
        reader = csv.DictReader(stream)
        
        row_count = 0
        sample_rows = []
        
        for row in reader:
            row_count += 1
            
            if len(sample_rows) < 5:
                sample_rows.append(row)
        
        # Get fieldnames after iteration (fieldnames are set after first row is read)
        fieldnames = list(reader.fieldnames) if reader.fieldnames else []
        
        # Validate that we have columns
        if not fieldnames:
            raise CSVProcessingError("CSV file has no header row or is empty")
        
        # Validate that we have at least one data row
        if row_count == 0:
            raise CSVProcessingError("CSV file contains only headers with no data rows")
        
        return {
            "row_count": row_count,
            "columns": fieldnames,
            "sample_rows": sample_rows,
        }
    except csv.Error as e:
        raise CSVProcessingError(f"Malformed CSV file: {str(e)}") from e
    except UnicodeDecodeError as e:
        raise CSVProcessingError(f"Invalid file encoding: {str(e)}") from e


def process_s3_object(bucket: str, key: str) -> dict[str, Any]:
    """Stream a CSV from S3, parse it, and write a summary to the processed bucket."""
    logger.info("Processing s3://%s/%s", bucket, key)

    try:
        # Attempt to read from S3
        response = s3_client.get_object(Bucket=bucket, Key=key)
        
        # Check if file is empty
        content_length = response.get("ContentLength", 0)
        if content_length == 0:
            raise CSVProcessingError("File is empty (0 bytes)")
        
        # Stream the file line by line instead of loading it all into memory
        stream = io.TextIOWrapper(response["Body"], encoding="utf-8-sig")
        summary = parse_csv_stream(stream)
        summary["source"] = {"bucket": bucket, "key": key}
        summary["status"] = "success"

    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "Unknown")
        error_msg = e.response.get("Error", {}).get("Message", str(e))
        
        logger.error("S3 read error for s3://%s/%s: %s - %s", bucket, key, error_code, error_msg)
        
        if error_code == "NoSuchKey":
            raise CSVProcessingError(f"File not found: s3://{bucket}/{key}") from e
        elif error_code == "AccessDenied":
            raise CSVProcessingError(f"Access denied to s3://{bucket}/{key}") from e
        else:
            raise CSVProcessingError(f"S3 read error ({error_code}): {error_msg}") from e
    
    except CSVProcessingError:
        raise
    
    except Exception as e:
        logger.error("Unexpected error processing s3://%s/%s: %s", bucket, key, str(e))
        raise CSVProcessingError(f"Unexpected processing error: {str(e)}") from e

    # Write summary to processed bucket
    if PROCESSED_BUCKET:
        try:
            output_key = f"processed/{key.rsplit('/', 1)[-1].replace('.csv', '')}_summary.json"
            s3_client.put_object(
                Bucket=PROCESSED_BUCKET,
                Key=output_key,
                Body=json.dumps(summary, indent=2).encode("utf-8"),
                ContentType="application/json",
            )
            summary["output"] = {"bucket": PROCESSED_BUCKET, "key": output_key}
            logger.info("Successfully wrote summary to s3://%s/%s", PROCESSED_BUCKET, output_key)
        
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "Unknown")
            error_msg = e.response.get("Error", {}).get("Message", str(e))
            logger.error("S3 write error for s3://%s/%s: %s - %s", PROCESSED_BUCKET, output_key, error_code, error_msg)
            
            # Don't fail the entire operation if write fails, but log it
            summary["output"] = {
                "bucket": PROCESSED_BUCKET,
                "key": output_key,
                "error": f"Failed to write output: {error_code} - {error_msg}"
            }

    return summary


def handle_s3_event(event: dict[str, Any]) -> dict[str, Any]:
    results = []
    errors = []
    
    for record in event.get("Records", []):
        s3_info = record.get("s3", {})
        bucket = s3_info.get("bucket", {}).get("name")
        key = s3_info.get("object", {}).get("key", "")

        if not bucket or not key:
            logger.warning("Skipping record with missing bucket or key")
            continue

        # Decode URL-encoded key (S3 encodes spaces as + and special chars as %XX)
        key = unquote_plus(key)

        if not key.lower().endswith(".csv"):
            logger.info("Skipping non-CSV object: %s", key)
            continue

        try:
            result = process_s3_object(bucket, key)
            results.append(result)
        except CSVProcessingError as e:
            error_info = {
                "bucket": bucket,
                "key": key,
                "status": "error",
                "error": str(e)
            }
            errors.append(error_info)
            logger.error("Failed to process s3://%s/%s: %s", bucket, key, str(e))
        except Exception as e:
            error_info = {
                "bucket": bucket,
                "key": key,
                "status": "error",
                "error": f"Unexpected error: {str(e)}"
            }
            errors.append(error_info)
            logger.exception("Unexpected error processing s3://%s/%s", bucket, key)

    return {
        "processed_files": len(results),
        "failed_files": len(errors),
        "results": results,
        "errors": errors
    }


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Main Lambda handler - supports both S3 events and API Gateway triggers."""
    try:
        logger.info("Received event: %s", json.dumps(event))
        
        # Check if it's an API Gateway event
        if "httpMethod" in event and "body" in event:
            logger.info("Processing API Gateway event")
            
            # Parse the request body
            body = json.loads(event["body"]) if event["body"] else {}
            bucket = body.get("bucket")
            key = body.get("key")
            
            if not bucket or not key:
                return {
                    "statusCode": 400,
                    "headers": {"Content-Type": "application/json"},
                    "body": json.dumps({
                        "error": "Missing required fields: 'bucket' and 'key'"
                    })
                }
            
            # Process the file
            try:
                result = process_s3_object(bucket, key)
                return {
                    "statusCode": 200,
                    "headers": {"Content-Type": "application/json"},
                    "body": json.dumps(result)
                }
            except CSVProcessingError as e:
                return {
                    "statusCode": 400,
                    "headers": {"Content-Type": "application/json"},
                    "body": json.dumps({"error": str(e)})
                }
        
        # Otherwise, treat as S3 event
        else:
            logger.info("Processing S3 event")
            return handle_s3_event(event)
            
    except Exception as e:
        logger.exception("Fatal error in lambda_handler")
        
        # Return appropriate format based on event type
        if "httpMethod" in event:
            return {
                "statusCode": 500,
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps({"error": f"Internal server error: {str(e)}"})
            }
        else:
            return {
                "processed_files": 0,
                "failed_files": 0,
                "results": [],
                "errors": [{
                    "status": "fatal_error",
                    "error": f"Lambda handler failed: {str(e)}"
                }]
            }
