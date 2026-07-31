import logging

from agent.safe_logging import install_safe_http_client_logging, report_contract_failure


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


def test_contract_failure_logger_bounds_every_unrecognized_field(caplog) -> None:
    logger = logging.getLogger("agent.contract-test")

    with caplog.at_level(logging.WARNING):
        report_contract_failure(
            logger,
            operation="CALL_ID_11111111-1111-4111-8111-111111111111",
            contract_name="TRANSCRIPT_SENTINEL",
            code="TOKEN_SENTINEL",
            transport="PAYLOAD_SENTINEL",
        )

    assert "operation=unknown contract_name=unknown code=unknown transport=unknown" \
        in caplog.text
    for forbidden in (
        "11111111-1111-4111-8111-111111111111",
        "TRANSCRIPT_SENTINEL",
        "TOKEN_SENTINEL",
        "PAYLOAD_SENTINEL",
    ):
        assert forbidden not in caplog.text
