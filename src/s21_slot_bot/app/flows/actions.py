import enum
from enum import StrEnum


class FlowAction(StrEnum): ...


class InputFlowAction(FlowAction):
    PICK_MODE = enum.auto()
    PICK_NUM_REVIEWS = enum.auto()
    CONFIRM_NUM_REVIEWS = enum.auto()
    PICK_FROM = enum.auto()
    PICK_TO = enum.auto()
    BACK = enum.auto()


class BookFlowAction(FlowAction):
    BOOK_ATTEMPT_MANUAL = enum.auto()


class DeleteFlowAction(FlowAction):
    DELETE_MENU = enum.auto()
    DELETE_ONE = enum.auto()
    DELETE_ALL = enum.auto()
    DELETE_ALL_STOPPED = enum.auto()


class EditFlowAction(FlowAction):
    LIST_BOTS = enum.auto()
    SHOW_MENU = enum.auto()
    PICK_BOT = enum.auto()
    MENU_FROM = enum.auto()
    MENU_TO = enum.auto()
    PICK_INTERVAL = enum.auto()
    SET_INTERVAL = enum.auto()
    MENU_MODE = enum.auto()
    SET_MODE = enum.auto()
    MENU_NUM_REVIEWS = enum.auto()
    SET_NUM_REVIEWS = enum.auto()
    RESTART = enum.auto()


class StartFlowAction(FlowAction):
    LIST_PROJECTS = enum.auto()
    PICK_PROJECT = enum.auto()
    CONFIRM = enum.auto()
    FINALIZE = enum.auto()


class StatusFlowAction(FlowAction):
    REFRESH = enum.auto()
    START_BOOKING_REFRESHER = enum.auto()
    STOP_BOOKING_REFRESHER = enum.auto()


class StopFlowAction(FlowAction):
    STOP_MENU = enum.auto()
    STOP_ONE = enum.auto()
    STOP_ALL = enum.auto()
