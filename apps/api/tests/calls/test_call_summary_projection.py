import pytest

from app.schemas.call_summary_projection import CallSummaryProjection


def valid_summary_data() -> dict[str, object]:
    return {
        "summary_text": "A caller wants to arrange an appointment.",
        "caller_intent": "Book a consultation",
        "action_items": ["Return the call"],
        "sentiment": "positive",
        "follow_up_required": True,
    }


def test_summary_projection_bounds_customer_facing_fields() -> None:
    projection = CallSummaryProjection.from_stored(valid_summary_data())

    assert projection is not None
    assert projection.caller_intent == "Book a consultation"
    assert projection.action_items == ["Return the call"]
    assert projection.sentiment == "positive"
    assert projection.follow_up_required is True


@pytest.mark.parametrize(
    "stored",
    [
        None,
        "not-json",
        {"caller_intent": "Missing the other customer-facing fields"},
        {**valid_summary_data(), "caller_intent": "x" * 201},
        {**valid_summary_data(), "action_items": ["Reply"] * 11},
        {**valid_summary_data(), "action_items": ["x" * 301]},
        {**valid_summary_data(), "sentiment": "x" * 33},
    ],
)
def test_summary_projection_ignores_malformed_and_oversized_legacy_data(
    stored: object,
) -> None:
    assert CallSummaryProjection.from_stored(stored) is None


def test_summary_projection_ignores_extra_legacy_fields() -> None:
    projection = CallSummaryProjection.from_stored(
        {**valid_summary_data(), "provider_debug_payload": "do not expose"}
    )

    assert projection is not None
    assert "provider_debug_payload" not in projection.model_dump()
