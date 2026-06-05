import json
from unittest.mock import MagicMock, patch

from csv_processor.handler import handle_s3_event, lambda_handler, parse_csv_content


SAMPLE_CSV = "name,email\nAlice,alice@example.com\nBob,bob@example.com\n"


def test_parse_csv_content_returns_summary():
    summary = parse_csv_content(SAMPLE_CSV)

    assert summary["row_count"] == 2
    assert summary["columns"] == ["name", "email"]
    assert len(summary["sample_rows"]) == 2
    assert summary["sample_rows"][0]["name"] == "Alice"


@patch("csv_processor.handler.s3_client")
def test_handle_s3_event_processes_csv(mock_s3):
    mock_s3.get_object.return_value = {
        "Body": MagicMock(read=lambda: SAMPLE_CSV.encode("utf-8"))
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
    mock_handle.return_value = {"processed_files": 0, "results": []}

    result = lambda_handler(event, None)

    mock_handle.assert_called_once_with(event)
    assert result == {"processed_files": 0, "results": []}
