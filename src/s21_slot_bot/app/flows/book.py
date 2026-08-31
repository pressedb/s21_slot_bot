from typing import override

from telegram import CallbackQuery

from s21_slot_bot.app.errors import BotRuntimeError, InvalidCallbackDataError
from s21_slot_bot.app.flows.actions import BookFlowAction
from s21_slot_bot.app.flows.base import Flow
from s21_slot_bot.app.models import CustomContext
from s21_slot_bot.client.errors import School21Error
from s21_slot_bot.common.logger import get_user_input_logger


class BookFlow(Flow):
    @override
    async def parse_callback(self, callback_data: list[str], query: CallbackQuery, context: CustomContext) -> None:
        logger = get_user_input_logger(query)
        action = callback_data.pop()
        match action:
            case BookFlowAction.BOOK_ATTEMPT_MANUAL:
                if query.message:
                    await self._messenger.safe_delete(query.message.message_id, logger)
                dry_run_id, bot_id = callback_data
                inst = self._bot_manager.get_bot(bot_id)
                inst.stats.attempts_total += 1
                cfg = inst.cfg
                dry_booking = self._booking_manager.pop_dry(dry_run_id)
                if not dry_booking:
                    raise BotRuntimeError(
                        f"бот #{bot_id} ({cfg.project_name}): не удалось найти сохраненную запись о найденном слоте"
                    )
                logger.info("Attempting to book answer_id `%s` at `%s`", dry_booking.answer_id, dry_booking.start)
                try:
                    are_review_points_left = await self._booking_manager.book(
                        inst=inst,
                        answer_id=dry_booking.answer_id,
                        start_time=dry_booking.start,
                        end_time=dry_booking.end,
                        logger=logger,
                        context=context,
                    )
                    if not are_review_points_left:
                        self._bot_manager.stop_bot(cfg.bot_id, context, logger)
                except School21Error as e:
                    raise BotRuntimeError(f"бот #{bot_id} ({cfg.project_name}): не удалось записаться") from e
            case _:
                raise InvalidCallbackDataError(f"неподдерживаемое действие '{action}' при попытке записаться")
