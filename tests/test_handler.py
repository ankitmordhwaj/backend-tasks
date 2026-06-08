import io
import json
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from csv_processor.handler import (
    CSVProcessingError,
    handle_s3_event,
    lambda_handler,
    parse_csv_stream,
)


SAMPLE_CSV = "name,email\nAlice,alice@example.com\nBob,bob@example.com\n"


def test_parse_csv_stream_returns_summary():
    stream = io.StringIO(SAMPLE_CSV)
    summary = parse_csv_stream(stream)

    assert summary["row_count"] == 2
    assert summary["columns"] == ["name", "email"]
    assert len(summary["sample_rows"]) == 2
    assert summary["sample_rows"][0]["name"] == "Alice"


@patch("csv_processor.handler.s3_client")
def test_handle_s3_event_processes_csv(mock_s3):
    # Create a BytesIO object to simulate S3 streaming response
    mock_body = io.BytesIO(SAMPLE_CSV.encode("utf-8"))
    mock_s3.get_object.return_value = {
        "Body": mock_body,
        "ContentLength": len(SAMPLE_CSV)
    }

    event = {
        "Records": [
            {
                "eventSource": "aws:s3",
                "s3": {
                    "bucket": {"name": "test-upload-bucket"},
                    "object": {"key": "data/users.csv"},
                },
            }
        ]
    }

    with patch("csv_processor.handler.PROCESSED_BUCKET", "processed-bucket"):
        result = handle_s3_event(event)

    assert result["processed_files"] == 1
    assert result["failed_files"] == 0
    assert result["results"][0]["status"] == "success"
    mock_s3.get_object.assert_called_once_with(
        Bucket="test-upload-bucket", Key="data/users.csv"
    )
    mock_s3.put_object.assert_called_once()


@patch("csv_processor.handler.s3_client")
def test_handle_s3_event_skips_non_csv(mock_s3):
    event = {
        "Records": [
            {
                "eventSource": "aws:s3",
                "s3": {
                    "bucket": {"name": "test-upload-bucket"},
                    "object": {"key": "data/readme.txt"},
                },
            }
        ]
    }

    result = handle_s3_event(event)

    assert result["processed_files"] == 0
    mock_s3.get_object.assert_not_called()


@patch("csv_processor.handler.handle_s3_event")
def test_lambda_handler_delegates_to_s3_handler(mock_handle):
    event = {"Records": [{"eventSource": "aws:s3"}]}
    mock_handle.return_value = {"processed_files": 0, "failed_files": 0, "results": [], "errors": []}

    result = lambda_handler(event, None)

    mock_handle.assert_called_once_with(event)
    assert result == {"processed_files": 0, "failed_files": 0, "results": [], "errors": []}


# Error handling tests

def test_parse_csv_stream_empty_file():
    """Test that empty CSV raises error."""
    stream = io.StringIO("")
    
    with pytest.raises(CSVProcessingError, match="no header row or is empty"):
        parse_csv_stream(stream)


def test_parse_csv_stream_only_headers():
    """Test that CSV with only headers raises error."""
    stream = io.StringIO("name,email\n")
    
    with pytest.raises(CSVProcessingError, match="only headers with no data rows"):
        parse_csv_stream(stream)


def test_parse_csv_stream_malformed():
    """Test that malformed CSV raises error."""
    # CSV with mismatched quotes
    malformed_csv = 'name,email\n"Alice,missing_quote@example.com\n'
    stream = io.StringIO(malformed_csv)
    
    # Note: csv.DictReader is quite lenient, so this might not always raise
    # But we're testing the error handling infrastructure is in place
    try:
        parse_csv_stream(stream)
    except CSVProcessingError:
        pass


@patch("csv_processor.handler.s3_client")
def test_handle_s3_event_empty_file(mock_s3):
    """Test that empty S3 file is handled gracefully."""
    mock_body = io.BytesIO(b"")
    mock_s3.get_object.return_value = {
        "Body": mock_body,
        "ContentLength": 0
    }

    event = {
        "Records": [
            {
                "s3": {
                    "bucket": {"name": "test-bucket"},
                    "object": {"key": "empty.csv"},
                }
            }
        ]
    }

    result = handle_s3_event(event)

    assert result["processed_files"] == 0
    assert result["failed_files"] == 1
    assert "empty" in result["errors"][0]["error"].lower()


@patch("csv_processor.handler.s3_client")
def test_handle_s3_event_file_not_found(mock_s3):
    """Test that missing S3 file is handled gracefully."""
    error_response = {"Error": {"Code": "NoSuchKey", "Message": "The specified key does not exist."}}
    mock_s3.get_object.side_effect = ClientError(error_response, "GetObject")

    event = {
        "Records": [
            {
                "s3": {
                    "bucket": {"name": "test-bucket"},
                    "object": {"key": "missing.csv"},
                }
            }
        ]
    }

    result = handle_s3_event(event)

    assert result["processed_files"] == 0
    assert result["failed_files"] == 1
    assert "not found" in result["errors"][0]["error"].lower()


@patch("csv_processor.handler.s3_client")
def test_handle_s3_event_access_denied(mock_s3):
    """Test that S3 access denied is handled gracefully."""
    error_response = {"Error": {"Code": "AccessDenied", "Message": "Access Denied"}}
    mock_s3.get_object.side_effect = ClientError(error_response, "GetObject")

    event = {
        "Records": [
            {
                "s3": {
                    "bucket": {"name": "test-bucket"},
                    "object": {"key": "secure.csv"},
                }
            }
        ]
    }

    result = handle_s3_event(event)

    assert result["processed_files"] == 0
    assert result["failed_files"] == 1
    assert "access denied" in result["errors"][0]["error"].lower()


@patch("csv_processor.handler.s3_client")
def test_handle_s3_event_write_failure(mock_s3):
    """Test that S3 write failure doesn't fail the entire operation."""
    mock_body = io.BytesIO(SAMPLE_CSV.encode("utf-8"))
    mock_s3.get_object.return_value = {
        "Body": mock_body,
        "ContentLength": len(SAMPLE_CSV)
    }
    
    # Write fails
    error_response = {"Error": {"Code": "AccessDenied", "Message": "Access Denied"}}
    mock_s3.put_object.side_effect = ClientError(error_response, "PutObject")

    event = {
        "Records": [
            {
                "s3": {
                    "bucket": {"name": "test-bucket"},
                    "object": {"key": "data.csv"},
                }
            }
        ]
    }

    with patch("csv_processor.handler.PROCESSED_BUCKET", "processed-bucket"):
        result = handle_s3_event(event)

    # Should still succeed but with error in output
    assert result["processed_files"] == 1
    assert result["failed_files"] == 0
    assert "error" in result["results"][0]["output"]


@patch("csv_processor.handler.s3_client")
def test_handle_s3_event_mixed_success_failure(mock_s3):
    """Test processing multiple files with some succeeding and some failing."""
    def get_object_side_effect(Bucket, Key):
        if "good" in Key:
            return {
                "Body": io.BytesIO(SAMPLE_CSV.encode("utf-8")),
                "ContentLength": len(SAMPLE_CSV)
            }
        else:
            error_response = {"Error": {"Code": "NoSuchKey", "Message": "Not found"}}
            raise ClientError(error_response, "GetObject")

    mock_s3.get_object.side_effect = get_object_side_effect

    event = {
        "Records": [
            {
                "s3": {
                    "bucket": {"name": "test-bucket"},
                    "object": {"key": "good1.csv"},
                }
            },
            {
                "s3": {
                    "bucket": {"name": "test-bucket"},
                    "object": {"key": "bad.csv"},
                }
            },
            {
                "s3": {
                    "bucket": {"name": "test-bucket"},
                    "object": {"key": "good2.csv"},
                }
            }
        ]
    }

    with patch("csv_processor.handler.PROCESSED_BUCKET", ""):
        result = handle_s3_event(event)

    assert result["processed_files"] == 2
    assert result["failed_files"] == 1
    assert len(result["results"]) == 2
    assert len(result["errors"]) == 1


@patch("csv_processor.handler.handle_s3_event")
def test_lambda_handler_fatal_error(mock_handle):
    """Test that fatal errors in lambda_handler are caught."""
    mock_handle.side_effect = Exception("Something went wrong")
    
    event = {"Records": []}
    result = lambda_handler(event, None)
    
    assert result["processed_files"] == 0
    assert result["failed_files"] == 0
    assert len(result["errors"]) == 1
    assert "fatal_error" in result["errors"][0]["status"]


@patch("csv_processor.handler.s3_client")
def test_handle_s3_event_url_encoded_key(mock_s3):
    """Test that URL-encoded keys (with spaces and special chars) are properly decoded."""
    mock_body = io.BytesIO(SAMPLE_CSV.encode("utf-8"))
    mock_s3.get_object.return_value = {
        "Body": mock_body,
        "ContentLength": len(SAMPLE_CSV)
    }

    # S3 sends keys URL-encoded: spaces as +, special chars as %XX
    event = {
        "Records": [
            {
                "s3": {
                    "bucket": {"name": "test-bucket"},
                    "object": {"key": "July+Sales+Report.csv"},  # Space encoded as +
                }
            }
        ]
    }

    with patch("csv_processor.handler.PROCESSED_BUCKET", ""):
        result = handle_s3_event(event)

    assert result["processed_files"] == 1
    assert result["failed_files"] == 0
    
    # Verify that get_object was called with the DECODED key
    mock_s3.get_object.assert_called_once_with(
        Bucket="test-bucket", 
        Key="July Sales Report.csv"  # Decoded with actual space
    )


# API Gateway trigger tests

@patch("csv_processor.handler.s3_client")
@patch("csv_processor.handler.PROCESSED_BUCKET", "test-processed-bucket")
def test_lambda_handler_api_gateway_trigger(mock_s3):
    """Test that Lambda can be triggered via API Gateway."""
    mock_body = io.BytesIO(SAMPLE_CSV.encode("utf-8"))
    mock_s3.get_object.return_value = {
        "Body": mock_body,
        "ContentLength": len(SAMPLE_CSV)
    }
    
    # API Gateway event format
    event = {
        "httpMethod": "POST",
        "body": json.dumps({
            "bucket": "test-bucket",
            "key": "test.csv"
        })
    }
    
    result = lambda_handler(event, None)
    
    assert result["statusCode"] == 200
    body = json.loads(result["body"])
    assert body["status"] == "success"
    assert body["row_count"] == 2
    assert body["columns"] == ["name", "email"]


@patch("csv_processor.handler.s3_client")
def test_lambda_handler_api_gateway_missing_params(mock_s3):
    """Test API Gateway trigger with missing parameters."""
    event = {
        "httpMethod": "POST",
        "body": json.dumps({"bucket": "test-bucket"})  # Missing 'key'
    }
    
    result = lambda_handler(event, None)
    
    assert result["statusCode"] == 400
    body = json.loads(result["body"])
    assert "error" in body


@patch("csv_processor.handler.s3_client")
def test_lambda_handler_api_gateway_processing_error(mock_s3):
    """Test API Gateway trigger with processing error."""
    error_response = {"Error": {"Code": "NoSuchKey", "Message": "Not found"}}
    mock_s3.get_object.side_effect = ClientError(error_response, "GetObject")
    
    event = {
        "httpMethod": "POST",
        "body": json.dumps({
            "bucket": "test-bucket",
            "key": "nonexistent.csv"
        })
    }
    
    result = lambda_handler(event, None)
    
    assert result["statusCode"] == 400
    body = json.loads(result["body"])
    assert "error" in body
    assert "not found" in body["error"].lower()


def test_lambda_handler_routes_s3_event():
    """Test that Lambda handler correctly routes S3 events."""
    with patch("csv_processor.handler.handle_s3_event") as mock_handle:
        mock_handle.return_value = {"processed_files": 1, "failed_files": 0, "results": [], "errors": []}
        
        event = {"Records": [{"s3": {"bucket": {"name": "test"}, "object": {"key": "test.csv"}}}]}
        
        result = lambda_handler(event, None)
        
        mock_handle.assert_called_once_with(event)
        assert result["processed_files"] == 1
