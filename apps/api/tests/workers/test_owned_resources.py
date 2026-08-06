import asyncio
import logging
import traceback

import pytest

from app.workers.outbox._owned_resources import operation_owned_resources


class _Resource:
    def __init__(
        self,
        name: str,
        order: list[str],
        *,
        started: asyncio.Event | None = None,
        release: asyncio.Event | None = None,
        error: Exception | None = None,
    ) -> None:
        self.name = name
        self.order = order
        self.started = started
        self.release = release
        self.error = error

    async def aclose(self) -> None:
        self.order.append(f"{self.name}:start")
        if self.started is not None:
            self.started.set()
        if self.release is not None:
            await self.release.wait()
        self.order.append(f"{self.name}:end")
        if self.error is not None:
            raise self.error


@pytest.mark.anyio
async def test_owned_resources_close_after_successful_body() -> None:
    order: list[str] = []

    async with operation_owned_resources(operation="test_success") as own:
        own(_Resource("only", order))

    assert order == ["only:start", "only:end"]


@pytest.mark.anyio
async def test_owned_resources_close_every_resource_in_reverse_order() -> None:
    order: list[str] = []

    async with operation_owned_resources(operation="test_reverse") as own:
        own(_Resource("first", order))
        own(_Resource("second", order))
        own(_Resource("third", order))

    assert order == [
        "third:start",
        "third:end",
        "second:start",
        "second:end",
        "first:start",
        "first:end",
    ]


@pytest.mark.anyio
async def test_cleanup_error_after_successful_body_is_raised_after_all_cleanup(
    caplog: pytest.LogCaptureFixture,
) -> None:
    order: list[str] = []
    close_error = RuntimeError("CLEANUP_SECRET_SENTINEL")

    with caplog.at_level(logging.WARNING), pytest.raises(RuntimeError) as caught:
        async with operation_owned_resources(operation="test_cleanup_error") as own:
            own(_Resource("first", order))
            own(_Resource("failing", order, error=close_error))

    assert caught.value is close_error
    assert order == [
        "failing:start",
        "failing:end",
        "first:start",
        "first:end",
    ]
    assert "event=operation_resource_close_failed" in caplog.text
    assert "CLEANUP_SECRET_SENTINEL" not in caplog.text


@pytest.mark.anyio
async def test_cleanup_error_never_replaces_existing_body_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    order: list[str] = []
    body_error = ValueError("BODY_SECRET_SENTINEL")
    close_error = RuntimeError("CLEANUP_SECRET_SENTINEL")

    with caplog.at_level(logging.WARNING), pytest.raises(ValueError) as caught:
        async with operation_owned_resources(operation="test_body_error") as own:
            own(_Resource("first", order))
            own(_Resource("failing", order, error=close_error))
            raise body_error

    assert caught.value is body_error
    assert order == [
        "failing:start",
        "failing:end",
        "first:start",
        "first:end",
    ]
    assert "event=operation_resource_close_failed" in caplog.text
    assert "BODY_SECRET_SENTINEL" not in caplog.text
    assert "CLEANUP_SECRET_SENTINEL" not in caplog.text


@pytest.mark.anyio
async def test_body_cancellation_preserves_identity_after_cleanup() -> None:
    order: list[str] = []
    cancellation = asyncio.CancelledError("body-cancelled")
    close_error = RuntimeError("CLEANUP_PRIVATE_SENTINEL")

    with pytest.raises(asyncio.CancelledError) as caught:
        async with operation_owned_resources(operation="test_body_cancel") as own:
            own(_Resource("only", order, error=close_error))
            raise cancellation

    assert order == ["only:start", "only:end"]
    assert caught.value is cancellation
    assert caught.value.args == ("body-cancelled",)
    assert "CLEANUP_PRIVATE_SENTINEL" not in "".join(
        traceback.format_exception(caught.value)
    )


@pytest.mark.anyio
async def test_body_cancellation_precedes_closer_cancellation() -> None:
    order: list[str] = []
    body_cancellation = asyncio.CancelledError("body-cancelled-first")
    cleanup_cancellation = asyncio.CancelledError("cleanup-cancelled-second")

    class CancelledResource:
        async def aclose(self) -> None:
            order.append("cancelled:start")
            raise cleanup_cancellation

    with pytest.raises(asyncio.CancelledError) as caught:
        async with operation_owned_resources(operation="test_cancel_precedence") as own:
            own(_Resource("first", order))
            own(CancelledResource())
            raise body_cancellation

    assert caught.value is body_cancellation
    assert caught.value.args == ("body-cancelled-first",)
    assert order == ["cancelled:start", "first:start", "first:end"]


@pytest.mark.anyio
async def test_cancelled_closer_is_not_logged_and_remaining_resources_close(
    caplog: pytest.LogCaptureFixture,
) -> None:
    order: list[str] = []

    cancellation = asyncio.CancelledError("cleanup-cancelled")

    class CancelledResource:
        async def aclose(self) -> None:
            order.append("cancelled:start")
            raise cancellation

    with caplog.at_level(logging.WARNING), pytest.raises(asyncio.CancelledError) as caught:
        async with operation_owned_resources(operation="test_cancelled_closer") as own:
            own(_Resource("first", order))
            own(CancelledResource())
            raise ValueError("BODY_PRIVATE_SENTINEL")

    assert order == ["cancelled:start", "first:start", "first:end"]
    assert caught.value is cancellation
    assert caught.value.args == ("cleanup-cancelled",)
    assert "BODY_PRIVATE_SENTINEL" not in "".join(
        traceback.format_exception(caught.value)
    )
    assert "operation_resource_close_failed" not in caplog.text


@pytest.mark.anyio
async def test_cancellation_during_cleanup_waits_for_all_reverse_order_cleanup(
    caplog: pytest.LogCaptureFixture,
) -> None:
    order: list[str] = []
    started = asyncio.Event()
    release = asyncio.Event()

    async def run_scope() -> None:
        async with operation_owned_resources(operation="test_cleanup_cancel") as own:
            own(_Resource("first", order))
            own(_Resource("second", order, started=started, release=release))

    task = asyncio.create_task(run_scope())
    await asyncio.wait_for(started.wait(), timeout=0.5)
    with caplog.at_level(logging.WARNING):
        task.cancel("first-cleanup-cancellation")
        await asyncio.sleep(0)
        assert not task.done()
        task.cancel("second-cleanup-cancellation")
        await asyncio.sleep(0)
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError) as caught:
            await task

    assert order == [
        "second:start",
        "second:end",
        "first:start",
        "first:end",
    ]
    assert caught.value.args == ("first-cleanup-cancellation",)
    assert "operation_resource_close_failed" not in caplog.text


@pytest.mark.anyio
async def test_cancellation_during_failing_cleanup_overrides_body_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    order: list[str] = []
    started = asyncio.Event()
    release = asyncio.Event()
    close_error = RuntimeError("CLEANUP_SECRET_SENTINEL")

    cancellation_seen_by_scope: asyncio.CancelledError | None = None

    async def run_scope() -> None:
        nonlocal cancellation_seen_by_scope
        try:
            async with operation_owned_resources(operation="test_error_cancel") as own:
                own(_Resource("first", order))
                own(
                    _Resource(
                        "failing",
                        order,
                        started=started,
                        release=release,
                        error=close_error,
                    )
                )
                raise ValueError("BODY_SECRET_SENTINEL")
        except asyncio.CancelledError as error:
            cancellation_seen_by_scope = error
            raise

    task = asyncio.create_task(run_scope())
    await asyncio.wait_for(started.wait(), timeout=0.5)
    with caplog.at_level(logging.WARNING):
        task.cancel("outer-cleanup-cancelled")
        await asyncio.sleep(0)
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError) as caught:
            await task

    assert order == [
        "failing:start",
        "failing:end",
        "first:start",
        "first:end",
    ]
    assert "event=operation_resource_close_failed" in caplog.text
    assert "BODY_SECRET_SENTINEL" not in caplog.text
    assert "CLEANUP_SECRET_SENTINEL" not in caplog.text
    assert caught.value is cancellation_seen_by_scope
    assert caught.value.args == ("outer-cleanup-cancelled",)
    formatted = "".join(traceback.format_exception(caught.value))
    assert "BODY_SECRET_SENTINEL" not in formatted
    assert "CLEANUP_SECRET_SENTINEL" not in formatted
