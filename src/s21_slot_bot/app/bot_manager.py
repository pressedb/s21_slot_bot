from datetime import datetime, timedelta

from s21_slot_bot.app.booking_manager import BookingManager
from s21_slot_bot.app.config import BotConfig
from s21_slot_bot.app.errors import (
    BotNotFoundError,
    BotRuntimeError,
    InternalError,
    TooManyBotsError,
)
from s21_slot_bot.app.messenger import Messenger
from s21_slot_bot.app.models import (
    BotInstance,
    CustomContext,
    IntervalSec,
    JobData,
    Lifecycle,
    Mode,
    NumBots,
)
from s21_slot_bot.app.utils import get_tzinfo
from s21_slot_bot.client.models import TimeSlot
from s21_slot_bot.client.s21_client import School21Client
from s21_slot_bot.common.logger import LoggerLike


class BotManager:
    def __init__(
        self,
        bot_config: BotConfig,
        chat_id: int,
        messenger: Messenger,
        s21_client: School21Client,
        booking_manager: BookingManager,
    ) -> None:
        self._bot_config = bot_config
        self._chat_id = chat_id
        self._messenger = messenger
        self._s21_client = s21_client
        self._booking_manager = booking_manager
        self._bots: dict[str, BotInstance] = {}

    @property
    def max_bots(self) -> NumBots:
        return self._bot_config.max_bots

    @property
    def poll_interval_sec(self) -> IntervalSec:
        return self._bot_config.poll_interval_sec

    def check_bot_limits(self) -> None:
        if len(self.list_all()) >= self._bot_config.max_bots:
            raise TooManyBotsError(f"Максимальное количество ботов достигнуто ({self._bot_config.max_bots})")

    def get_bot(self, bot_id: str | None) -> BotInstance:
        bot = self._bots.get(bot_id) if bot_id else None
        if not bot:
            raise BotNotFoundError(f"бот #{bot_id} не найден")
        return bot

    def list_all(self, states: set[Lifecycle] | None = None) -> list[BotInstance]:
        all_bots = [b for b in self._bots.values() if not states or b.state in states]

        def key(x: BotInstance) -> tuple[int, str, str]:
            prioritized_states = {Lifecycle.RUNNING: 0, Lifecycle.STOPPED: 1, Lifecycle.FAILED: 2}
            priority = prioritized_states.get(x.state, len(Lifecycle) + 1)
            return priority, x.cfg.project_id, x.cfg.bot_id

        return sorted(all_bots, key=key)

    async def start_bot(self, inst: BotInstance, context: CustomContext, logger: LoggerLike) -> None:
        cfg = inst.cfg
        try:
            task_id, answer_id = await self._s21_client.get_task_and_answer(cfg.project_id, logger)
        except Exception as e:
            raise BotRuntimeError(
                f"бот #{cfg.bot_id}: не удалось получить необходимую информацию для начала поиска"
            ) from e

        inst.state = Lifecycle.RUNNING
        self._bots[cfg.bot_id] = inst
        job_data = JobData(inst=inst, task_id=task_id, answer_id=answer_id)
        job = context.ensured_job_queue.run_repeating(
            self._search, cfg.interval_sec, data=job_data, chat_id=self._chat_id, name=cfg.bot_id
        )
        logger.info("Started bot #%s", cfg.bot_id)
        await job.run(context.application)  # type: ignore [arg-type]

    def stop_bot(
        self,
        bot_id: str,
        context: CustomContext,
        logger: LoggerLike,
        state: Lifecycle = Lifecycle.STOPPED,
    ) -> bool:
        inst = self._bots.get(bot_id)
        if not inst:
            logger.warning("Unable to find bot `%s`", bot_id)
            return False
        inst.state = state
        jobs = context.ensured_job_queue.get_jobs_by_name(bot_id)
        if not jobs:
            logger.info("Unable to find job for bot `%s`", bot_id)
        if len(jobs) > 1:
            logger.warning("%d jobs found with ID `%s` - there may be a name collision", len(jobs), bot_id)
        for job in jobs:
            job.schedule_removal()
            logger.info("Stopped job ID `%s`", job.name)
        return True

    def stop_all(self, context: CustomContext, logger: LoggerLike) -> None:
        for inst in self.list_all(states={Lifecycle.RUNNING}):
            self.stop_bot(inst.cfg.bot_id, context, logger)

    def delete_bot(self, bot_id: str, context: CustomContext, logger: LoggerLike) -> bool:
        if not self.stop_bot(bot_id, context, logger):
            return False
        inst = self._bots.pop(bot_id, None)
        is_deleted = bool(inst)
        return is_deleted

    def delete_all(self, context: CustomContext, logger: LoggerLike, states: set[Lifecycle] | None = None) -> int:
        deleted_counter = 0
        for inst in self.list_all(states=states):
            if self.delete_bot(inst.cfg.bot_id, context, logger):
                deleted_counter += 1
        return deleted_counter

    async def _search(self, context: CustomContext) -> None:
        job = context.job
        if not job:
            raise InternalError("задача на поиск не найдена")
        job_data = JobData.model_validate(job.data)
        inst, answer_id, task_id = job_data.inst, job_data.answer_id, job_data.task_id
        logger = inst.logger()
        cfg = inst.cfg
        tz = get_tzinfo(context)

        if inst.state != Lifecycle.RUNNING:
            logger.warning("Bot `%s` is not currently running, stopping job `%s`", cfg.bot_id, job.name)
            self.stop_bot(cfg.bot_id, context, logger)
            return

        if datetime.now(tz=tz) >= cfg.to_dt:
            logger.info("Removing the current bot search due to expiration")
            self.delete_bot(cfg.bot_id, context, logger)
            await self._messenger.send(
                context,
                f"⌛️ бот #{cfg.bot_id} ({cfg.project_name}): удален, окно поиска истекло",
            )
            return

        inst.stats.attempts_total += 1
        inst.stats.last_ping = datetime.now(tz=tz)

        try:
            slots_info = await self._s21_client.get_slots_info(task_id, cfg.from_dt, cfg.to_dt, logger)
            currently_booked = slots_info.review_info.booked
            inst.stats.currently_booked = currently_booked
            missing = cfg.required_reviews - currently_booked
            if currently_booked >= slots_info.review_info.required or (missing < 1 and cfg.mode == Mode.FIND_AND_BOOK):
                logger.info(
                    "No more reviews required (current %d / configured %d / max %d), finishing current search",
                    currently_booked,
                    cfg.required_reviews,
                    slots_info.review_info.required,
                )
                return

            picked_start_time = self._pick_candidate_start(slots_info.time_slots)
            if not picked_start_time:
                logger.info("No suitable timeslots found")
                return
            end_time = picked_start_time + timedelta(minutes=slots_info.check_duration)
            match cfg.mode:
                case Mode.ONLY_FIND:
                    await self._booking_manager.book_dry(
                        inst=inst,
                        answer_id=answer_id,
                        start_time=picked_start_time,
                        end_time=end_time,
                        context=context,
                    )
                    self.stop_bot(cfg.bot_id, context, logger)
                case Mode.FIND_AND_BOOK:
                    are_review_points_left = await self._booking_manager.book(
                        inst=inst,
                        answer_id=answer_id,
                        start_time=picked_start_time,
                        end_time=end_time,
                        logger=logger,
                        context=context,
                    )
                    if not are_review_points_left:
                        self.stop_bot(cfg.bot_id, context, logger)
        except Exception as e:
            inst.stats.attempts_failed += 1
            logger.exception(
                "Failed attempt %d running bot %s",
                inst.stats.attempts_failed,
                cfg.bot_id,
            )
            raise BotRuntimeError(f"бот #{cfg.bot_id} ({cfg.project_name}): ошибка поиска") from e

    def _pick_candidate_start(self, timeslots: list[TimeSlot]) -> datetime | None:
        candidates: set[datetime] = {
            time for slot in timeslots for time in slot.valid_start_times if not slot.staff_slot
        }
        candidate = min(candidates) if candidates else None
        return candidate
