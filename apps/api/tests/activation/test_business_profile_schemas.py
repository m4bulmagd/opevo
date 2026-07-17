from collections.abc import Callable
from datetime import UTC, time

import pytest
from pydantic import ValidationError

from app.schemas.business_profile import (
    BUSINESS_TYPE_MAX_LENGTH,
    ESCALATION_NOTES_MAX_LENGTH,
    FAQ_ANSWER_MAX_LENGTH,
    FAQ_MAX_ITEMS,
    FAQ_QUESTION_MAX_LENGTH,
    INSTRUCTIONS_MAX_LENGTH,
    NAME_MAX_LENGTH,
    PUBLIC_DESCRIPTION_MAX_LENGTH,
    BusinessHours,
    BusinessProfileConstraints,
    BusinessProfileDraft,
    WEEKDAYS,
)


def complete_business_hours() -> dict[str, dict[str, object]]:
    return {
        day: {
            "closed": day in {"saturday", "sunday"},
            "intervals": []
            if day in {"saturday", "sunday"}
            else [{"start": "09:00", "end": "18:00"}],
        }
        for day in WEEKDAYS
    }


def complete_profile_payload(**overrides: object) -> dict[str, object]:
    return {
        "owner_name": "Camille Martin",
        "business_name": "Atelier Martin",
        "business_type": "Plomberie",
        "public_description": "Dépannage et installation de plomberie.",
        "timezone": "Europe/Paris",
        "business_hours": complete_business_hours(),
        "existing_phone_e164": "+33 6 12 34 56 78",
        "confirmed_carrier": "orange",
        "receptionist_name": "Léa",
        "faqs": [
            {
                "question": "Intervenez-vous le week-end ?",
                "answer": "Oui, uniquement pour les urgences.",
            }
        ],
        "special_instructions": "Toujours demander le code postal.",
        "escalation_notes": "Transférer les urgences au propriétaire.",
    } | overrides


def complete_profile_draft(**overrides: object) -> BusinessProfileDraft:
    return BusinessProfileDraft.model_validate(complete_profile_payload(**overrides))


def test_business_hours_accept_split_day() -> None:
    payload = BusinessHours.model_validate(
        complete_business_hours()
        | {
            "monday": {
                "closed": False,
                "intervals": [
                    {"start": "09:00", "end": "12:00"},
                    {"start": "14:00", "end": "18:00"},
                ],
            }
        }
    )

    assert len(payload.root["monday"].intervals) == 2


def test_business_hours_require_every_weekday() -> None:
    hours = complete_business_hours()
    del hours["sunday"]

    with pytest.raises(ValidationError, match="seven weekdays"):
        BusinessHours.model_validate(hours)


def test_business_hours_reject_unknown_day() -> None:
    hours = complete_business_hours() | {"holiday": {"closed": True, "intervals": []}}

    with pytest.raises(ValidationError, match="seven weekdays"):
        BusinessHours.model_validate(hours)


def test_business_hours_reject_overlapping_intervals() -> None:
    hours = complete_business_hours()
    hours["monday"] = {
        "closed": False,
        "intervals": [
            {"start": "09:00", "end": "14:00"},
            {"start": "13:00", "end": "18:00"},
        ],
    }

    with pytest.raises(ValidationError, match="cannot overlap"):
        BusinessHours.model_validate(hours)


def test_business_hours_allow_touching_intervals_and_sort_them() -> None:
    hours = complete_business_hours()
    hours["monday"] = {
        "closed": False,
        "intervals": [
            {"start": "12:00", "end": "18:00"},
            {"start": "09:00", "end": "12:00"},
        ],
    }

    validated = BusinessHours.model_validate(hours)

    assert [
        interval.start.strftime("%H:%M")
        for interval in validated.root["monday"].intervals
    ] == [
        "09:00",
        "12:00",
    ]


def test_business_hours_reject_more_than_two_intervals() -> None:
    hours = complete_business_hours()
    hours["monday"] = {
        "closed": False,
        "intervals": [
            {"start": "08:00", "end": "10:00"},
            {"start": "11:00", "end": "13:00"},
            {"start": "14:00", "end": "18:00"},
        ],
    }

    with pytest.raises(ValidationError, match="at most 2"):
        BusinessHours.model_validate(hours)


def test_business_hours_reject_intervals_on_closed_day() -> None:
    hours = complete_business_hours()
    hours["saturday"] = {
        "closed": True,
        "intervals": [{"start": "09:00", "end": "12:00"}],
    }

    with pytest.raises(ValidationError, match="Closed days"):
        BusinessHours.model_validate(hours)


def test_business_hours_require_interval_on_open_day() -> None:
    hours = complete_business_hours()
    hours["monday"] = {"closed": False, "intervals": []}

    with pytest.raises(ValidationError, match="Open days"):
        BusinessHours.model_validate(hours)


def test_business_hours_require_forward_intervals() -> None:
    hours = complete_business_hours()
    hours["monday"] = {
        "closed": False,
        "intervals": [{"start": "18:00", "end": "09:00"}],
    }

    with pytest.raises(ValidationError, match="end after"):
        BusinessHours.model_validate(hours)


@pytest.mark.parametrize(
    ("start", "end"),
    [
        ("09:00:30", "09:00:45"),
        (time(9, 0, 0, 1), time(10, 0, 0, 1)),
    ],
    ids=["seconds", "microseconds"],
)
def test_business_hours_reject_sub_minute_precision(
    start: str | time,
    end: str | time,
) -> None:
    hours = complete_business_hours()
    hours["monday"] = {
        "closed": False,
        "intervals": [{"start": start, "end": end}],
    }

    with pytest.raises(ValidationError, match="whole minutes"):
        BusinessHours.model_validate(hours)


def test_business_hours_reject_offset_aware_times() -> None:
    hours = complete_business_hours()
    hours["monday"] = {
        "closed": False,
        "intervals": [{"start": time(9, tzinfo=UTC), "end": time(18, tzinfo=UTC)}],
    }

    with pytest.raises(ValidationError, match="timezone-naive"):
        BusinessHours.model_validate(hours)


@pytest.mark.parametrize("number", ["0612345678", "+33 6 12 34 56 78"])
def test_profile_normalizes_french_number(number: str) -> None:
    draft = complete_profile_draft(existing_phone_e164=number)

    assert draft.existing_phone_e164 == "+33612345678"


def test_profile_rejects_non_french_number() -> None:
    with pytest.raises(ValidationError, match="valid French phone number"):
        complete_profile_draft(existing_phone_e164="+44 20 7946 0958")


def test_profile_rejects_unknown_timezone() -> None:
    with pytest.raises(ValidationError, match="IANA timezone"):
        complete_profile_draft(timezone="Europe/Atlantis")


def test_profile_rejects_twenty_one_faqs() -> None:
    faqs = [
        {"question": f"Question {index}", "answer": "Réponse"}
        for index in range(FAQ_MAX_ITEMS + 1)
    ]

    with pytest.raises(ValidationError, match="at most 20"):
        complete_profile_draft(faqs=faqs)


@pytest.mark.parametrize(
    "field",
    [
        "owner_name",
        "business_name",
        "business_type",
        "public_description",
        "receptionist_name",
    ],
)
def test_required_profile_text_rejects_whitespace_only(field: str) -> None:
    with pytest.raises(ValidationError, match="must not be blank"):
        complete_profile_draft(**{field: " \t\n "})


@pytest.mark.parametrize(
    ("field", "limit"),
    [
        ("owner_name", NAME_MAX_LENGTH),
        ("business_name", NAME_MAX_LENGTH),
        ("receptionist_name", NAME_MAX_LENGTH),
        ("business_type", BUSINESS_TYPE_MAX_LENGTH),
        ("public_description", PUBLIC_DESCRIPTION_MAX_LENGTH),
    ],
)
def test_required_profile_text_trims_before_enforcing_maximum(
    field: str,
    limit: int,
) -> None:
    normalized = "x" * limit

    assert (
        getattr(complete_profile_draft(**{field: f"  {normalized}  "}), field)
        == normalized
    )

    with pytest.raises(ValidationError, match=f"at most {limit}"):
        complete_profile_draft(**{field: f"  {normalized}x  "})


@pytest.mark.parametrize("field", ["question", "answer"])
def test_faq_required_text_rejects_whitespace_only(field: str) -> None:
    faq = {"question": "Question", "answer": "Réponse"} | {field: " \t\n "}

    with pytest.raises(ValidationError, match="must not be blank"):
        complete_profile_draft(faqs=[faq])


@pytest.mark.parametrize(
    ("field", "limit"),
    [
        ("question", FAQ_QUESTION_MAX_LENGTH),
        ("answer", FAQ_ANSWER_MAX_LENGTH),
    ],
)
def test_faq_required_text_trims_before_enforcing_maximum(
    field: str,
    limit: int,
) -> None:
    normalized = "x" * limit
    faq = {"question": "Question", "answer": "Réponse"} | {field: f"  {normalized}  "}

    assert getattr(complete_profile_draft(faqs=[faq]).faqs[0], field) == normalized

    faq[field] = f"  {normalized}x  "
    with pytest.raises(ValidationError, match=f"at most {limit}"):
        complete_profile_draft(faqs=[faq])


@pytest.mark.parametrize(
    ("field", "limit"),
    [
        ("owner_name", NAME_MAX_LENGTH),
        ("business_name", NAME_MAX_LENGTH),
        ("receptionist_name", NAME_MAX_LENGTH),
        ("business_type", BUSINESS_TYPE_MAX_LENGTH),
        ("public_description", PUBLIC_DESCRIPTION_MAX_LENGTH),
        ("special_instructions", INSTRUCTIONS_MAX_LENGTH),
        ("escalation_notes", ESCALATION_NOTES_MAX_LENGTH),
    ],
)
def test_profile_text_fields_enforce_documented_maximum(field: str, limit: int) -> None:
    assert getattr(complete_profile_draft(**{field: "x" * limit}), field) == "x" * limit

    with pytest.raises(ValidationError, match=f"at most {limit}"):
        complete_profile_draft(**{field: "x" * (limit + 1)})


@pytest.mark.parametrize(
    ("faq_factory", "limit", "field"),
    [
        (
            lambda length: {"question": "x" * length, "answer": "Réponse"},
            FAQ_QUESTION_MAX_LENGTH,
            "question",
        ),
        (
            lambda length: {"question": "Question", "answer": "x" * length},
            FAQ_ANSWER_MAX_LENGTH,
            "answer",
        ),
    ],
)
def test_faq_fields_enforce_documented_maximum(
    faq_factory: Callable[[int], dict[str, str]],
    limit: int,
    field: str,
) -> None:
    assert (
        getattr(complete_profile_draft(faqs=[faq_factory(limit)]).faqs[0], field)
        == "x" * limit
    )

    with pytest.raises(ValidationError, match=f"at most {limit}"):
        complete_profile_draft(faqs=[faq_factory(limit + 1)])


def test_profile_storage_serializes_times_as_hours_and_minutes() -> None:
    stored_hours = complete_profile_draft().to_storage_dict()["business_hours"]

    assert stored_hours["monday"]["intervals"] == [{"start": "09:00", "end": "18:00"}]


def test_constraints_expose_the_schema_limits() -> None:
    assert BusinessProfileConstraints().model_dump() == {
        "name_max_length": 100,
        "business_type_max_length": 100,
        "public_description_max_length": 1_000,
        "faq_max_items": 20,
        "faq_question_max_length": 200,
        "faq_answer_max_length": 800,
        "special_instructions_max_length": 2_000,
        "escalation_notes_max_length": 2_000,
        "max_intervals_per_day": 2,
        "phone_country": "FR",
    }
