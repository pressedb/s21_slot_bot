from collections.abc import Callable
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from telegram.ext import Job, JobQueue

from s21_slot_bot.app.booking_manager import BookingManager
from s21_slot_bot.app.bot_manager import BotManager
from s21_slot_bot.app.errors import BotNotFoundError, BotRuntimeError, InternalError, TooManyBotsError
from s21_slot_bot.app.messenger import Messenger
from s21_slot_bot.app.models import BotInstance, CustomContext, Lifecycle, Mode
from s21_slot_bot.client.models import SlotsInfo, TimeSlot
from s21_slot_bot.client.s21_client import School21Client
from s21_slot_bot.common.logger import LoggerLike


class TestBotManager:
    def test_limits_get_and_list(
        self,
        bot_manager: BotManager,
        bot_instance_factory: Callable[..., BotInstance],
    ) -> None:
        running = bot_instance_factory(bot_id="a", project_id="2", state=Lifecycle.RUNNING)
        stopped = bot_instance_factory(bot_id="b", project_id="1", state=Lifecycle.STOPPED)
        failed = bot_instance_factory(bot_id="c", project_id="1", state=Lifecycle.FAILED)
        bot_manager._bots = {"a": running, "b": stopped, "c": failed}

        assert bot_manager.get_bot("a") is running
        assert bot_manager.list_all() == [running, stopped, failed]
        assert bot_manager.list_all(states={Lifecycle.STOPPED}) == [stopped]

        bot_manager._bot_config.max_bots = 3
        with pytest.raises(TooManyBotsError):
            bot_manager.check_bot_limits()
        with pytest.raises(BotNotFoundError):
            bot_manager.get_bot(None)
        with pytest.raises(BotNotFoundError):
            bot_manager.get_bot("missing")

    async def test_start_bot(
        self,
        bot_manager: BotManager,
        s21_client: School21Client,
        bot_instance_factory: Callable[..., BotInstance],
        context: CustomContext,
        job_queue_mock: JobQueue,
        job_mock: Job,
        logger_mock: LoggerLike,
    ) -> None:
        inst = bot_instance_factory()
        s21_client.get_task_and_answer = AsyncMock(return_value=("task-1", "answer-1"))
        job_queue_mock.run_repeating.return_value = job_mock

        await bot_manager.start_bot(inst, context, logger_mock)

        assert inst.state == Lifecycle.RUNNING
        assert bot_manager.get_bot(inst.cfg.bot_id) is inst
        job_mock.run.assert_awaited_once_with(context.application)

    async def test_start_bot_wraps_setup_failure(
        self,
        bot_manager: BotManager,
        s21_client: School21Client,
        bot_instance_factory: Callable[..., BotInstance],
        context: CustomContext,
        logger_mock: LoggerLike,
    ) -> None:
        s21_client.get_task_and_answer = AsyncMock(side_effect=RuntimeError("boom"))
        with pytest.raises(BotRuntimeError):
            await bot_manager.start_bot(bot_instance_factory(), context, logger_mock)

    def test_stop_missing_bot(
        self,
        bot_manager: BotManager,
        context: CustomContext,
        logger_mock: LoggerLike,
    ) -> None:
        assert bot_manager.stop_bot("missing", context, logger_mock) is False

    def test_stop_bot_job_edge_cases(
        self,
        bot_manager: BotManager,
        booking_manager: BookingManager,
        bot_instance_factory: Callable[..., BotInstance],
        context: CustomContext,
        job_queue_mock: JobQueue,
        logger_mock: LoggerLike,
    ) -> None:
        inst = bot_instance_factory(state=Lifecycle.RUNNING)
        bot_manager._bots[inst.cfg.bot_id] = inst

        job_queue_mock.get_jobs_by_name.return_value = []
        assert bot_manager.stop_bot(inst.cfg.bot_id, context, logger_mock)

        inst.state = Lifecycle.RUNNING
        other = bot_instance_factory(bot_id="other", state=Lifecycle.RUNNING)
        bot_manager._bots[other.cfg.bot_id] = other
        jobs = [MagicMock(spec=Job), MagicMock(spec=Job)]
        job_queue_mock.get_jobs_by_name.return_value = jobs
        assert bot_manager.stop_bot(inst.cfg.bot_id, context, logger_mock)
        for job in jobs:
            job.schedule_removal.assert_called_once()

    def test_stop_all_delete_and_delete_all(
        self,
        bot_manager: BotManager,
        bot_instance_factory: Callable[..., BotInstance],
        context: CustomContext,
        job_queue_mock: JobQueue,
        job_mock: Job,
        logger_mock: LoggerLike,
    ) -> None:
        running = bot_instance_factory(bot_id="run", state=Lifecycle.RUNNING)
        stopped = bot_instance_factory(bot_id="stop", state=Lifecycle.STOPPED)
        bot_manager._bots = {"run": running, "stop": stopped}
        job_queue_mock.get_jobs_by_name.return_value = [job_mock]

        bot_manager.stop_all(context, logger_mock)
        assert running.state == Lifecycle.STOPPED

        assert bot_manager.delete_bot("missing", context, logger_mock) is False
        assert bot_manager.delete_all(context, logger_mock, states={Lifecycle.STOPPED}) == 2
        assert not bot_manager.list_all()

    async def test_search_requires_job(self, bot_manager: BotManager, context: CustomContext) -> None:
        context.job = None
        with pytest.raises(InternalError):
            await bot_manager._search(context)

    async def test_search_stops_non_running_job(
        self,
        bot_manager: BotManager,
        bot_instance_factory: Callable[..., BotInstance],
        context: CustomContext,
        job_mock: Job,
    ) -> None:
        inst = bot_instance_factory(state=Lifecycle.STOPPED)
        job_mock.data = {"inst": inst, "task_id": "task", "answer_id": "answer"}
        job_mock.name = inst.cfg.bot_id
        context.job = job_mock
        bot_manager.stop_bot = MagicMock()
        await bot_manager._search(context)
        bot_manager.stop_bot.assert_called_once()

    async def test_search_deletes_expired_bot(
        self,
        bot_manager: BotManager,
        messenger: Messenger,
        bot_instance_factory: Callable[..., BotInstance],
        context: CustomContext,
        job_mock: Job,
        now: datetime,
    ) -> None:
        inst = bot_instance_factory(state=Lifecycle.RUNNING, to_dt=now)
        job_mock.data = {"inst": inst, "task_id": "task", "answer_id": "answer"}
        job_mock.name = inst.cfg.bot_id
        context.job = job_mock
        bot_manager.delete_bot = MagicMock(return_value=True)
        messenger.send = AsyncMock()
        with patch("s21_slot_bot.app.bot_manager.datetime") as datetime_mock:
            datetime_mock.now.return_value = now
            await bot_manager._search(context)
        bot_manager.delete_bot.assert_called_once()
        messenger.send.assert_awaited_once()

    async def test_search_find_and_book_with_enough_bookings_does_not_book(
        self,
        bot_manager: BotManager,
        s21_client: School21Client,
        booking_manager: BookingManager,
        bot_instance_factory: Callable[..., BotInstance],
        slots_info_factory: Callable[..., SlotsInfo],
        context: CustomContext,
        job_mock: Job,
        now: datetime,
    ) -> None:
        inst = bot_instance_factory(
            state=Lifecycle.RUNNING, required_reviews=2, to_dt=now + timedelta(hours=1), mode=Mode.FIND_AND_BOOK
        )
        job_mock.data = {"inst": inst, "task_id": "task-1", "answer_id": "answer-1"}
        job_mock.name = inst.cfg.bot_id
        context.job = job_mock
        s21_client.get_slots_info = AsyncMock(return_value=slots_info_factory(booked=2))
        booking_manager.book = AsyncMock()
        booking_manager.book_dry = AsyncMock()
        with patch("s21_slot_bot.app.bot_manager.datetime") as datetime_mock:
            datetime_mock.now.return_value = now
            await bot_manager._search(context)
        assert inst.stats.currently_booked == 2
        booking_manager.book.assert_not_awaited()

    async def test_search_only_find_with_enough_bookings_finds_a_booking(
        self,
        bot_manager: BotManager,
        s21_client: School21Client,
        booking_manager: BookingManager,
        bot_instance_factory: Callable[..., BotInstance],
        slots_info_factory: Callable[..., SlotsInfo],
        context: CustomContext,
        job_mock: Job,
        now: datetime,
    ) -> None:
        inst = bot_instance_factory(
            state=Lifecycle.RUNNING, required_reviews=1, to_dt=now + timedelta(hours=1), mode=Mode.ONLY_FIND
        )
        job_mock.data = {"inst": inst, "task_id": "task-1", "answer_id": "answer-1"}
        job_mock.name = inst.cfg.bot_id
        context.job = job_mock
        s21_client.get_slots_info = AsyncMock(return_value=slots_info_factory(booked=1))
        booking_manager.book = AsyncMock()
        booking_manager.book_dry = AsyncMock()
        with patch("s21_slot_bot.app.bot_manager.datetime") as datetime_mock:
            datetime_mock.now.return_value = now
            await bot_manager._search(context)
        assert inst.stats.currently_booked == 1
        booking_manager.book_dry.assert_awaited_once()

    async def test_search_only_find_with_max_currently_booked_does_nothing(
        self,
        bot_manager: BotManager,
        s21_client: School21Client,
        booking_manager: BookingManager,
        bot_instance_factory: Callable[..., BotInstance],
        slots_info_factory: Callable[..., SlotsInfo],
        context: CustomContext,
        job_mock: Job,
        now: datetime,
    ) -> None:
        inst = bot_instance_factory(
            state=Lifecycle.RUNNING, required_reviews=1, to_dt=now + timedelta(hours=1), mode=Mode.ONLY_FIND
        )
        job_mock.data = {"inst": inst, "task_id": "task-1", "answer_id": "answer-1"}
        job_mock.name = inst.cfg.bot_id
        context.job = job_mock
        s21_client.get_slots_info = AsyncMock(return_value=slots_info_factory(booked=2, required=2))
        booking_manager.book = AsyncMock()
        booking_manager.book_dry = AsyncMock()
        with patch("s21_slot_bot.app.bot_manager.datetime") as datetime_mock:
            datetime_mock.now.return_value = now
            await bot_manager._search(context)
        assert inst.stats.currently_booked == 2
        booking_manager.book_dry.assert_not_awaited()

    async def test_search_no_candidate(
        self,
        bot_manager: BotManager,
        s21_client: School21Client,
        booking_manager: BookingManager,
        bot_instance_factory: Callable[..., BotInstance],
        slots_info_factory: Callable[..., SlotsInfo],
        context: CustomContext,
        job_mock: Job,
        now: datetime,
    ) -> None:
        inst = bot_instance_factory(state=Lifecycle.RUNNING, to_dt=now + timedelta(hours=1))
        job_mock.data = {"inst": inst, "task_id": "task", "answer_id": "answer"}
        context.job = job_mock
        s21_client.get_slots_info = AsyncMock(return_value=slots_info_factory(booked=0, time_slots=[]))
        booking_manager.book = AsyncMock()
        with patch("s21_slot_bot.app.bot_manager.datetime") as datetime_mock:
            datetime_mock.now.return_value = now
            await bot_manager._search(context)
        booking_manager.book.assert_not_awaited()

    async def test_search_only_find(
        self,
        bot_manager: BotManager,
        s21_client: School21Client,
        booking_manager: BookingManager,
        bot_instance_factory: Callable[..., BotInstance],
        slots_info_factory: Callable[..., SlotsInfo],
        timeslot_factory: Callable[..., TimeSlot],
        context: CustomContext,
        job_mock: Job,
        job_queue_mock: JobQueue,
        now: datetime,
    ) -> None:
        start = now + timedelta(minutes=20)
        inst = bot_instance_factory(state=Lifecycle.RUNNING, mode=Mode.ONLY_FIND, to_dt=now + timedelta(hours=1))
        job_mock.data = {"inst": inst, "task_id": "task-1", "answer_id": "answer-1"}
        job_mock.name = inst.cfg.bot_id
        context.job = job_mock
        bot_manager._bots[inst.cfg.bot_id] = inst
        job_queue_mock.get_jobs_by_name.return_value = [job_mock]
        s21_client.get_slots_info = AsyncMock(
            return_value=slots_info_factory(
                booked=0,
                time_slots=[timeslot_factory(valid_start_times=[start], staff_slot=False)],
            )
        )
        booking_manager.book_dry = AsyncMock()
        booking_manager.stop_refreshing = MagicMock()
        with patch("s21_slot_bot.app.bot_manager.datetime") as datetime_mock:
            datetime_mock.now.return_value = now
            await bot_manager._search(context)
        booking_manager.book_dry.assert_awaited_once()
        assert inst.state == Lifecycle.STOPPED

    @pytest.mark.parametrize("points_left", [True, False])
    async def test_search_find_and_book(
        self,
        bot_manager: BotManager,
        s21_client: School21Client,
        booking_manager: BookingManager,
        bot_instance_factory: Callable[..., BotInstance],
        slots_info_factory: Callable[..., SlotsInfo],
        timeslot_factory: Callable[..., TimeSlot],
        context: CustomContext,
        job_mock: Job,
        now: datetime,
        points_left: bool,
    ) -> None:
        inst = bot_instance_factory(state=Lifecycle.RUNNING, mode=Mode.FIND_AND_BOOK, to_dt=now + timedelta(hours=1))
        bot_manager._bots[inst.cfg.bot_id] = inst
        job_mock.data = {"inst": inst, "task_id": "task", "answer_id": "answer"}
        job_mock.name = inst.cfg.bot_id
        context.job = job_mock
        s21_client.get_slots_info = AsyncMock(return_value=slots_info_factory(time_slots=[timeslot_factory()]))
        booking_manager.book = AsyncMock(return_value=points_left)
        bot_manager.stop_bot = MagicMock()
        with patch("s21_slot_bot.app.bot_manager.datetime") as datetime_mock:
            datetime_mock.now.return_value = now
            await bot_manager._search(context)
        booking_manager.book.assert_awaited_once()
        if points_left:
            bot_manager.stop_bot.assert_not_called()
        else:
            bot_manager.stop_bot.assert_called_once()

    async def test_search_failure_is_wrapped(
        self,
        bot_manager: BotManager,
        s21_client: School21Client,
        bot_instance_factory: Callable[..., BotInstance],
        context: CustomContext,
        job_mock: Job,
        now: datetime,
    ) -> None:
        inst = bot_instance_factory(state=Lifecycle.RUNNING, to_dt=now + timedelta(hours=1))
        job_mock.data = {"inst": inst, "task_id": "task-1", "answer_id": "answer-1"}
        job_mock.name = inst.cfg.bot_id
        context.job = job_mock
        s21_client.get_slots_info = AsyncMock(side_effect=RuntimeError("boom"))
        with (
            patch("s21_slot_bot.app.bot_manager.datetime") as datetime_mock,
            pytest.raises(BotRuntimeError),
        ):
            datetime_mock.now.return_value = now
            await bot_manager._search(context)
        assert inst.stats.attempts_failed == 1

    def test_pick_candidate_start(
        self,
        bot_manager: BotManager,
        timeslot_factory: Callable[..., TimeSlot],
        now: datetime,
    ) -> None:
        earlier_time, later_time = now + timedelta(minutes=10), now + timedelta(hours=1)
        later = timeslot_factory(valid_start_times=[later_time], staff_slot=False)
        earlier = timeslot_factory(valid_start_times=[earlier_time], staff_slot=True)
        assert bot_manager._pick_candidate_start([later, earlier]) == later_time
        assert bot_manager._pick_candidate_start([]) is None
