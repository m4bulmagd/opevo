from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.models.business_profile import BusinessProfile
from app.models.call import Call
from app.models.customer_activation import CustomerActivation
from app.models.user import User
from app.services.dashboard_metrics_service import DashboardMetricsService


@dataclass(frozen=True)
class DashboardOwners:
    owner_id: UUID
    other_owner_id: UUID


def _call(
    *,
    started_at: datetime | None,
    owner: str = "owner",
    status: str = "completed",
    duration_seconds: int | None = None,
    summary_data: dict[str, Any] | None = None,
    deleted_at: datetime | None = None,
) -> dict[str, object]:
    return {
        "started_at": started_at,
        "owner": owner,
        "status": status,
        "duration_seconds": duration_seconds,
        "summary_data": summary_data,
        "deleted_at": deleted_at,
    }


async def _seed_dashboard(
    database_url: str,
    *,
    calls: list[dict[str, object]] | None = None,
    profile_timezone: str | None = None,
    profile_revision: int = 1,
    confirmation_revision: int | None = None,
    confirmed_at: datetime | None = None,
    owner_clerk_user_id: str | None = None,
    owner_status: str = "active",
) -> DashboardOwners:
    engine = create_async_engine(database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    unique = uuid4().hex
    try:
        async with session_factory() as session:
            owner = User(
                clerk_user_id=owner_clerk_user_id or f"dashboard-owner-{unique}",
                email=f"dashboard-owner-{unique}@example.com",
                status=owner_status,
            )
            other_owner = User(
                clerk_user_id=f"dashboard-other-{unique}",
                email=f"dashboard-other-{unique}@example.com",
            )
            session.add_all([owner, other_owner])
            await session.flush()

            if profile_timezone is not None:
                session.add(
                    BusinessProfile(
                        user_id=owner.id,
                        timezone=profile_timezone,
                        content_revision=profile_revision,
                    )
                )
                session.add(
                    CustomerActivation(
                        user_id=owner.id,
                        profile_confirmed_revision=confirmation_revision,
                        profile_confirmed_at=confirmed_at,
                    )
                )

            for values in calls or []:
                status = str(values["status"])
                session.add(
                    Call(
                        user_id=(
                            owner.id
                            if values["owner"] == "owner"
                            else other_owner.id
                        ),
                        status=status,
                        failure_code="legacy_failure" if status == "failed" else None,
                        started_at=values["started_at"],
                        duration_seconds=values["duration_seconds"],
                        summary_data=values["summary_data"],
                        deleted_at=values["deleted_at"],
                    )
                )
            await session.commit()
            return DashboardOwners(
                owner_id=owner.id,
                other_owner_id=other_owner.id,
            )
    finally:
        await engine.dispose()


async def _get_metrics(
    database_url: str,
    *,
    user_id: UUID,
    now: datetime,
):
    engine = create_async_engine(database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as session:
            return await DashboardMetricsService(session).get_metrics(
                user_id,
                now=now,
            )
    finally:
        await engine.dispose()


@pytest.mark.anyio
async def test_dashboard_metrics_requires_authentication(async_client) -> None:
    response = await async_client.get("/api/dashboard/metrics")

    assert response.status_code == 401


@pytest.mark.anyio
async def test_dashboard_metrics_resolve_owner_and_return_exact_contract(
    async_client,
    client_database_url: str,
    rs256_clerk_token_for,
    test_app,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clerk_user_id = "dashboard-api-owner"
    current = datetime(2026, 7, 15, 12, tzinfo=UTC)
    previous = current - timedelta(days=8)
    original_get_metrics = DashboardMetricsService.get_metrics
    observed_now: datetime | None = None

    async def get_metrics_at_configured_time(
        service: DashboardMetricsService,
        user_id: UUID,
        *,
        now: datetime | None = None,
    ):
        nonlocal observed_now
        observed_now = now
        return await original_get_metrics(service, user_id, now=now)

    monkeypatch.setattr(
        DashboardMetricsService,
        "get_metrics",
        get_metrics_at_configured_time,
    )
    configured_settings = get_settings().model_copy(
        update={"dashboard_metrics_reference_time": current}
    )
    runtime = test_app.state.runtime
    previous_settings = runtime.settings
    runtime.settings = configured_settings
    try:
        await _seed_dashboard(
            client_database_url,
            owner_clerk_user_id=clerk_user_id,
            calls=[
                _call(
                    started_at=current,
                    duration_seconds=30,
                    summary_data={
                        "caller_intent": "Book a table",
                        "action_items": ["Call the guest"],
                        "sentiment": "positive",
                        "follow_up_required": True,
                    },
                ),
                _call(started_at=previous, duration_seconds=90),
            ],
        )

        response = await async_client.get(
            "/api/dashboard/metrics",
            headers={
                "Authorization": f"Bearer {rs256_clerk_token_for(clerk_user_id)}"
            },
        )
    finally:
        runtime.settings = previous_settings

    assert response.status_code == 200
    assert response.json() == {
        "timezone": "Europe/Paris",
        "calls_today": 1,
        "calls_last_7_days": 1,
        "calls_previous_7_days": 1,
        "calls_change_from_previous_7_days": 0,
        "follow_up_flagged_last_7_days": 1,
        "average_duration_seconds_last_7_days": 30,
        "daily_activity": [
            {"date": "2026-07-09", "label": "Thu", "calls": 0},
            {"date": "2026-07-10", "label": "Fri", "calls": 0},
            {"date": "2026-07-11", "label": "Sat", "calls": 0},
            {"date": "2026-07-12", "label": "Sun", "calls": 0},
            {"date": "2026-07-13", "label": "Mon", "calls": 0},
            {"date": "2026-07-14", "label": "Tue", "calls": 0},
            {"date": "2026-07-15", "label": "Wed", "calls": 1},
        ],
    }
    assert observed_now == current


@pytest.mark.anyio
async def test_dashboard_metrics_exclude_calls_owned_by_another_user(
    async_client,
    client_database_url: str,
    rs256_clerk_token_for,
) -> None:
    clerk_user_id = "dashboard-api-isolated-owner"
    await _seed_dashboard(
        client_database_url,
        owner_clerk_user_id=clerk_user_id,
        calls=[
            _call(
                owner="other",
                started_at=datetime.now(UTC),
                duration_seconds=600,
            )
        ],
    )

    response = await async_client.get(
        "/api/dashboard/metrics",
        headers={
            "Authorization": f"Bearer {rs256_clerk_token_for(clerk_user_id)}"
        },
    )

    assert response.status_code == 200
    payload = response.json()
    activity = payload.pop("daily_activity")
    assert len(activity) == 7
    assert all(point["calls"] == 0 for point in activity)
    assert payload == {
        "timezone": "Europe/Paris",
        "calls_today": 0,
        "calls_last_7_days": 0,
        "calls_previous_7_days": 0,
        "calls_change_from_previous_7_days": 0,
        "follow_up_flagged_last_7_days": 0,
        "average_duration_seconds_last_7_days": None,
    }


@pytest.mark.anyio
async def test_dashboard_metrics_serialize_an_empty_average_as_json_null(
    async_client,
    client_database_url: str,
    rs256_clerk_token_for,
) -> None:
    clerk_user_id = "dashboard-api-empty-average-owner"
    await _seed_dashboard(
        client_database_url,
        owner_clerk_user_id=clerk_user_id,
        calls=[
            _call(
                started_at=datetime.now(UTC),
                status="pending",
                duration_seconds=999,
            )
        ],
    )

    response = await async_client.get(
        "/api/dashboard/metrics",
        headers={
            "Authorization": f"Bearer {rs256_clerk_token_for(clerk_user_id)}"
        },
    )

    assert response.status_code == 200
    assert response.json()["average_duration_seconds_last_7_days"] is None


@pytest.mark.anyio
async def test_inactive_owner_can_read_dashboard_metrics_but_cannot_mutate_route(
    async_client,
    client_database_url: str,
    rs256_clerk_token_for,
) -> None:
    clerk_user_id = "dashboard-api-inactive-owner"
    await _seed_dashboard(
        client_database_url,
        owner_clerk_user_id=clerk_user_id,
        owner_status="inactive",
        calls=[
            _call(started_at=datetime.now(UTC)),
        ],
    )
    headers = {
        "Authorization": f"Bearer {rs256_clerk_token_for(clerk_user_id)}"
    }

    response = await async_client.get("/api/dashboard/metrics", headers=headers)
    mutation = await async_client.post(
        "/api/dashboard/metrics",
        headers=headers,
    )

    assert response.status_code == 200
    assert response.json()["calls_last_7_days"] == 1
    assert mutation.status_code == 405


@pytest.mark.anyio
async def test_metrics_use_confirmed_business_timezone_and_local_day_boundaries(
    client_database_url: str,
) -> None:
    now = datetime.fromisoformat("2026-07-25T00:30:00+02:00")
    owners = await _seed_dashboard(
        client_database_url,
        profile_timezone="Europe/Paris",
        confirmation_revision=1,
        confirmed_at=datetime(2026, 7, 1, tzinfo=UTC),
        calls=[
            _call(started_at=datetime(2026, 7, 24, 21, 59, 59, tzinfo=UTC)),
            _call(started_at=datetime(2026, 7, 24, 22, 0, tzinfo=UTC)),
        ],
    )

    metrics = await _get_metrics(
        client_database_url,
        user_id=owners.owner_id,
        now=now,
    )

    assert metrics.timezone == "Europe/Paris"
    assert metrics.calls_today == 1


@pytest.mark.anyio
async def test_metrics_return_seven_ascending_local_day_activity_points(
    client_database_url: str,
) -> None:
    owners = await _seed_dashboard(
        client_database_url,
        calls=[
            _call(started_at=datetime(2026, 7, 18, 21, 59, 59, tzinfo=UTC)),
            _call(started_at=datetime(2026, 7, 18, 22, 0, tzinfo=UTC)),
            _call(started_at=datetime(2026, 7, 20, 10, 0, tzinfo=UTC)),
            _call(started_at=datetime(2026, 7, 20, 12, 0, tzinfo=UTC)),
            _call(started_at=datetime(2026, 7, 24, 22, 29, 59, tzinfo=UTC)),
            _call(started_at=datetime(2026, 7, 24, 22, 30, 1, tzinfo=UTC)),
            _call(
                started_at=datetime(2026, 7, 20, 13, 0, tzinfo=UTC),
                owner="other",
            ),
            _call(
                started_at=datetime(2026, 7, 20, 14, 0, tzinfo=UTC),
                deleted_at=datetime(2026, 7, 20, 15, 0, tzinfo=UTC),
            ),
        ],
    )

    metrics = await _get_metrics(
        client_database_url,
        user_id=owners.owner_id,
        now=datetime.fromisoformat("2026-07-25T00:30:00+02:00"),
    )

    assert [
        (point.date, point.label, point.calls)
        for point in metrics.daily_activity
    ] == [
        ("2026-07-19", "Sun", 1),
        ("2026-07-20", "Mon", 2),
        ("2026-07-21", "Tue", 0),
        ("2026-07-22", "Wed", 0),
        ("2026-07-23", "Thu", 0),
        ("2026-07-24", "Fri", 0),
        ("2026-07-25", "Sat", 1),
    ]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("profile_timezone", "profile_revision", "confirmation_revision", "confirmed_at"),
    [
        (None, 1, None, None),
        ("America/New_York", 1, None, None),
        (
            "America/New_York",
            2,
            1,
            datetime(2026, 7, 1, tzinfo=UTC),
        ),
    ],
    ids=["no-profile", "draft-profile", "stale-confirmation"],
)
async def test_metrics_fall_back_to_europe_paris_without_a_confirmed_profile(
    client_database_url: str,
    profile_timezone: str | None,
    profile_revision: int,
    confirmation_revision: int | None,
    confirmed_at: datetime | None,
) -> None:
    owners = await _seed_dashboard(
        client_database_url,
        profile_timezone=profile_timezone,
        profile_revision=profile_revision,
        confirmation_revision=confirmation_revision,
        confirmed_at=confirmed_at,
    )

    metrics = await _get_metrics(
        client_database_url,
        user_id=owners.owner_id,
        now=datetime.fromisoformat("2026-07-25T00:30:00+02:00"),
    )

    assert metrics.timezone == "Europe/Paris"


@pytest.mark.anyio
async def test_metrics_compare_adjacent_seven_local_day_windows(
    client_database_url: str,
) -> None:
    owners = await _seed_dashboard(
        client_database_url,
        calls=[
            _call(started_at=datetime(2026, 7, 11, 21, 59, 59, tzinfo=UTC)),
            _call(started_at=datetime(2026, 7, 11, 22, 0, tzinfo=UTC)),
            _call(started_at=datetime(2026, 7, 18, 21, 59, 59, tzinfo=UTC)),
            _call(started_at=datetime(2026, 7, 18, 22, 0, tzinfo=UTC)),
            _call(started_at=datetime(2026, 7, 18, 22, 0, 1, tzinfo=UTC)),
            _call(started_at=datetime(2026, 7, 24, 22, 30, tzinfo=UTC)),
            _call(started_at=datetime(2026, 7, 24, 22, 30, 1, tzinfo=UTC)),
        ],
    )

    metrics = await _get_metrics(
        client_database_url,
        user_id=owners.owner_id,
        now=datetime.fromisoformat("2026-07-25T00:30:00+02:00"),
    )

    assert metrics.calls_last_7_days == 3
    assert metrics.calls_previous_7_days == 2
    assert metrics.calls_change_from_previous_7_days == 1


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("now", "current_start_utc"),
    [
        (
            datetime.fromisoformat("2026-04-04T12:00:00+02:00"),
            datetime(2026, 3, 28, 23, 0, tzinfo=UTC),
        ),
        (
            datetime.fromisoformat("2026-10-31T12:00:00+01:00"),
            datetime(2026, 10, 24, 22, 0, tzinfo=UTC),
        ),
    ],
    ids=["spring-forward-2026-03-29", "fall-back-2026-10-25"],
)
async def test_metrics_handle_paris_dst_boundaries(
    client_database_url: str,
    now: datetime,
    current_start_utc: datetime,
) -> None:
    owners = await _seed_dashboard(
        client_database_url,
        calls=[
            _call(started_at=current_start_utc - timedelta(microseconds=1)),
            _call(started_at=current_start_utc),
        ],
    )

    metrics = await _get_metrics(
        client_database_url,
        user_id=owners.owner_id,
        now=now,
    )

    assert metrics.calls_last_7_days == 1
    assert metrics.daily_activity[0].date == (
        now.date() - timedelta(days=6)
    ).isoformat()
    assert metrics.daily_activity[0].calls == 1
    assert len(metrics.daily_activity) == 7


@pytest.mark.anyio
async def test_metrics_exclude_other_owner_deleted_and_null_started_calls(
    client_database_url: str,
) -> None:
    current = datetime(2026, 7, 24, 22, 5, tzinfo=UTC)
    owners = await _seed_dashboard(
        client_database_url,
        calls=[
            _call(started_at=current),
            _call(started_at=current, owner="other"),
            _call(
                started_at=current,
                deleted_at=datetime(2026, 7, 24, 22, 10, tzinfo=UTC),
            ),
            _call(started_at=None),
        ],
    )

    metrics = await _get_metrics(
        client_database_url,
        user_id=owners.owner_id,
        now=datetime.fromisoformat("2026-07-25T00:30:00+02:00"),
    )

    assert metrics.calls_today == 1
    assert metrics.calls_last_7_days == 1
    assert metrics.calls_previous_7_days == 0


@pytest.mark.anyio
async def test_metrics_count_only_valid_true_follow_up_summaries(
    client_database_url: str,
) -> None:
    started_at = datetime(2026, 7, 24, 20, 0, tzinfo=UTC)
    valid_summary = {
        "caller_intent": "Book a table",
        "action_items": ["Call the guest"],
        "sentiment": "positive",
        "follow_up_required": True,
    }
    owners = await _seed_dashboard(
        client_database_url,
        calls=[
            _call(started_at=started_at, summary_data=valid_summary),
            _call(
                started_at=started_at,
                summary_data={**valid_summary, "follow_up_required": False},
            ),
            _call(
                started_at=started_at,
                summary_data={"follow_up_required": True},
            ),
            _call(
                started_at=started_at,
                summary_data={
                    "caller_intent": "x" * 201,
                    "action_items": valid_summary["action_items"],
                    "sentiment": valid_summary["sentiment"],
                    "follow_up_required": True,
                },
            ),
            _call(
                started_at=started_at,
                summary_data={
                    **valid_summary,
                    "action_items": ["x" * 301],
                },
            ),
            _call(
                started_at=started_at,
                summary_data={
                    **valid_summary,
                    "sentiment": "x" * 33,
                },
            ),
            _call(
                started_at=started_at,
                summary_data={
                    **valid_summary,
                    "action_items": [1],
                },
            ),
            _call(
                started_at=started_at,
                summary_data={
                    **valid_summary,
                    "action_items": ["item"] * 11,
                },
            ),
            _call(
                started_at=started_at,
                summary_data={
                    **valid_summary,
                    "caller_intent": " ",
                },
            ),
            _call(
                started_at=started_at,
                summary_data={
                    **valid_summary,
                    "action_items": [" "],
                },
            ),
            _call(
                started_at=started_at,
                summary_data={
                    **valid_summary,
                    "sentiment": " ",
                },
            ),
            _call(
                started_at=started_at,
                summary_data={
                    **valid_summary,
                    "follow_up_required": "true",
                },
            ),
        ],
    )

    metrics = await _get_metrics(
        client_database_url,
        user_id=owners.owner_id,
        now=datetime.fromisoformat("2026-07-25T00:30:00+02:00"),
    )

    assert metrics.follow_up_flagged_last_7_days == 1


@pytest.mark.anyio
async def test_metrics_apply_follow_up_string_limits_before_trimming(
    client_database_url: str,
) -> None:
    started_at = datetime(2026, 7, 24, 20, 0, tzinfo=UTC)
    exact_boundary_summary = {
        "caller_intent": "i" * 200,
        "action_items": ["a" * 300],
        "sentiment": "s" * 32,
        "follow_up_required": True,
    }
    owners = await _seed_dashboard(
        client_database_url,
        calls=[
            _call(
                started_at=started_at,
                summary_data=exact_boundary_summary,
            ),
            _call(
                started_at=started_at,
                summary_data={
                    **exact_boundary_summary,
                    "caller_intent": f"{'i' * 200} ",
                },
            ),
            _call(
                started_at=started_at,
                summary_data={
                    **exact_boundary_summary,
                    "action_items": [f"{'a' * 300} "],
                },
            ),
            _call(
                started_at=started_at,
                summary_data={
                    **exact_boundary_summary,
                    "sentiment": f"{'s' * 32} ",
                },
            ),
        ],
    )

    metrics = await _get_metrics(
        client_database_url,
        user_id=owners.owner_id,
        now=datetime.fromisoformat("2026-07-25T00:30:00+02:00"),
    )

    assert metrics.follow_up_flagged_last_7_days == 1


@pytest.mark.anyio
@pytest.mark.parametrize(
    "whitespace",
    ["\t\n", "\u00a0\u2003"],
    ids=["ascii-control-whitespace", "non-ascii-whitespace"],
)
async def test_metrics_reject_follow_up_whitespace_only_strings(
    client_database_url: str,
    whitespace: str,
) -> None:
    started_at = datetime(2026, 7, 24, 20, 0, tzinfo=UTC)
    valid_summary = {
        "caller_intent": "Book a table",
        "action_items": ["Call the guest"],
        "sentiment": "positive",
        "follow_up_required": True,
    }
    owners = await _seed_dashboard(
        client_database_url,
        calls=[
            _call(started_at=started_at, summary_data=valid_summary),
            _call(
                started_at=started_at,
                summary_data={
                    **valid_summary,
                    "caller_intent": whitespace,
                },
            ),
            _call(
                started_at=started_at,
                summary_data={
                    **valid_summary,
                    "action_items": [whitespace],
                },
            ),
            _call(
                started_at=started_at,
                summary_data={
                    **valid_summary,
                    "sentiment": whitespace,
                },
            ),
        ],
    )

    metrics = await _get_metrics(
        client_database_url,
        user_id=owners.owner_id,
        now=datetime.fromisoformat("2026-07-25T00:30:00+02:00"),
    )

    assert metrics.follow_up_flagged_last_7_days == 1


@pytest.mark.anyio
async def test_metrics_average_only_terminal_calls_with_durations(
    client_database_url: str,
) -> None:
    started_at = datetime(2026, 7, 24, 20, 0, tzinfo=UTC)
    owners = await _seed_dashboard(
        client_database_url,
        calls=[
            _call(
                started_at=started_at,
                status="completed",
                duration_seconds=10,
            ),
            _call(
                started_at=started_at,
                status="failed",
                duration_seconds=11,
            ),
            _call(
                started_at=started_at,
                status="pending",
                duration_seconds=999,
            ),
            _call(
                started_at=started_at,
                status="completed",
                duration_seconds=None,
            ),
            _call(
                started_at=started_at,
                status="completed",
                duration_seconds=500,
                deleted_at=datetime(2026, 7, 24, 21, 0, tzinfo=UTC),
            ),
        ],
    )
    empty_owners = await _seed_dashboard(client_database_url)

    metrics = await _get_metrics(
        client_database_url,
        user_id=owners.owner_id,
        now=datetime.fromisoformat("2026-07-25T00:30:00+02:00"),
    )
    empty_metrics = await _get_metrics(
        client_database_url,
        user_id=empty_owners.owner_id,
        now=datetime.fromisoformat("2026-07-25T00:30:00+02:00"),
    )

    assert metrics.average_duration_seconds_last_7_days == 11
    assert empty_metrics.average_duration_seconds_last_7_days is None
