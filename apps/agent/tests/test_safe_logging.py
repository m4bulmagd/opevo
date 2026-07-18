import logging

from agent.safe_logging import install_safe_http_client_logging


def test_safe_http_client_factory_preserves_unnamed_log_records() -> None:
    original_factory = logging.getLogRecordFactory()
    try:
        install_safe_http_client_logging()

        record = logging.makeLogRecord(
            {
                "msg": "provider.operation.completed",
                "status": "accepted",
            }
        )
    finally:
        logging.setLogRecordFactory(original_factory)

    assert record.name is None
    assert record.getMessage() == "provider.operation.completed"
    assert record.status == "accepted"
