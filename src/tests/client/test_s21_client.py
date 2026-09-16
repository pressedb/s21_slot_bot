from collections.abc import Callable
from datetime import datetime, timedelta
from http import HTTPStatus
from unittest.mock import AsyncMock, patch

import aiohttp
import pytest

from s21_slot_bot.app.errors import AppNotInitializedError
from s21_slot_bot.client.errors import (
    School21Error,
    School21NoPointsError,
    School21ParsingError,
    School21SlotNotFoundError,
)
from s21_slot_bot.client.models import OperationName, Project, ProjectStatus
from s21_slot_bot.client.s21_client import School21Client
from s21_slot_bot.common.logger import LoggerLike
from tests.conftest import response_context


class TestSchool21Client:
    def test_session_guard(self, s21_client: School21Client) -> None:
        with pytest.raises(AppNotInitializedError):
            _ = s21_client._session

    async def test_start_and_stop_session(
        self, s21_client: School21Client, session_mock: aiohttp.ClientSession
    ) -> None:
        with patch(
            "s21_slot_bot.client.s21_client.aiohttp.ClientSession", return_value=session_mock
        ) as session_factory:
            await s21_client.start()
            await s21_client.start()
        session_factory.assert_called_once()
        assert s21_client._session is session_mock
        await s21_client.stop()
        session_mock.close.assert_awaited_once()

    async def test_stop_without_session(self, s21_client: School21Client) -> None:
        await s21_client.stop()

    async def test_get_user_and_student_id_is_cached(
        self,
        s21_client: School21Client,
        logger_mock: LoggerLike,
    ) -> None:
        s21_client._graphql = AsyncMock(
            return_value={"user": {OperationName.GET_USER: {"id": "user-1", "currentSchoolStudentId": "student-1"}}}
        )
        assert await s21_client.get_user_and_student_id(logger_mock) == ("user-1", "student-1")
        assert await s21_client.get_user_and_student_id(logger_mock) == ("user-1", "student-1")
        s21_client._graphql.assert_awaited_once()

    async def test_get_reviewed_projects(
        self,
        s21_client: School21Client,
        logger_mock: LoggerLike,
    ) -> None:
        s21_client._graphql = AsyncMock(
            return_value={
                "student": {
                    OperationName.GET_CUR_PROJECTS: [
                        {"goalId": "p1", "goalName": "Direct", "goalStatus": "P2P_EVALUATIONS"},
                        {
                            "goalName": "Course",
                            "displayedCourseStatus": "IN_PROGRESS",
                            "localCourseId": "course-1",
                        },
                        {"goalId": "p3", "goalName": "Done", "goalStatus": "COMPLETED"},
                    ]
                }
            }
        )
        s21_client.get_local_course_goals = AsyncMock(
            return_value=[
                Project(id="p2", name="Nested", status=ProjectStatus.P2P_EVALUATIONS),
                Project(id="p4", name="Nested done", status=ProjectStatus.COMPLETED),
            ]
        )
        projects = await s21_client.get_reviewed_projects("user-1", logger_mock)
        assert [project.id for project in projects] == ["p1", "p2"]

    async def test_get_reviewed_projects_reraises_school21_error(
        self,
        s21_client: School21Client,
        logger_mock: LoggerLike,
    ) -> None:
        s21_client._graphql = AsyncMock(
            return_value={
                "student": {
                    OperationName.GET_CUR_PROJECTS: [
                        {
                            "goalName": "Course",
                            "displayedCourseStatus": "IN_PROGRESS",
                            "localCourseId": "course-1",
                        }
                    ]
                }
            }
        )
        s21_client.get_local_course_goals = AsyncMock(side_effect=School21Error("boom"))
        with pytest.raises(School21Error):
            await s21_client.get_reviewed_projects("user", logger_mock)

    async def test_get_local_course_goals(
        self,
        s21_client: School21Client,
        logger_mock: LoggerLike,
    ) -> None:
        s21_client._graphql = AsyncMock(
            return_value={
                "course": {
                    OperationName.GET_LOCAL_COURSE_GOALS: {
                        "localCourseGoals": [{"goalId": "p1", "goalName": "One", "goalStatus": "P2P_EVALUATIONS"}]
                    }
                }
            }
        )
        projects = await s21_client.get_local_course_goals("course-1", logger_mock)
        assert [project.id for project in projects] == ["p1"]

    async def test_get_review_info(
        self,
        s21_client: School21Client,
        logger_mock: LoggerLike,
    ) -> None:
        s21_client._graphql = AsyncMock(
            return_value={
                "school21": {
                    "getP2PChecksInfo": {
                        "projectReviewsInfo": {
                            "reviewByStudentCount": 3,
                            "relevantReviewByStudentsCount": 1,
                        }
                    }
                }
            }
        )
        info = await s21_client.get_review_info("p1", "s1", logger_mock)
        assert (info.booked, info.required) == (1, 3)

    async def test_get_task_and_answer(
        self,
        s21_client: School21Client,
        logger_mock: LoggerLike,
    ) -> None:
        s21_client._graphql = AsyncMock(
            return_value={
                "student": {"getModuleById": {"currentTask": {"taskId": "task-1", "lastAnswer": {"id": "answer-1"}}}}
            }
        )
        assert await s21_client.get_task_and_answer("project-1", logger_mock) == ("task-1", "answer-1")

    async def test_get_slots_info(
        self,
        s21_client: School21Client,
        logger_mock: LoggerLike,
        now: datetime,
    ) -> None:
        end = now + timedelta(hours=1)
        s21_client._graphql = AsyncMock(
            return_value={
                "student": {
                    "getNameLessStudentTimeslotsForReview": {
                        "checkDuration": 45,
                        "projectReviewsInfo": {
                            "reviewByStudentCount": 3,
                            "relevantReviewByStudentsCount": 1,
                        },
                        "timeSlots": [
                            {
                                "start": now.isoformat(),
                                "end": end.isoformat(),
                                "validStartTimes": [now.isoformat()],
                                "staffSlot": False,
                            }
                        ],
                    }
                }
            }
        )
        info = await s21_client.get_slots_info("task-1", now, end, logger_mock)
        assert info.review_info.booked == 1

    async def test_get_reviewee_bookings(
        self,
        s21_client: School21Client,
        logger_mock: LoggerLike,
        now: datetime,
    ) -> None:
        end = now + timedelta(hours=1)
        s21_client._graphql = AsyncMock(
            return_value={
                "student": {
                    "getMyCalendarBookings": [
                        {
                            "id": "booking-1",
                            "answerId": "answer-1",
                            "task": {"goalId": "project-1", "goalName": "Project 1"},
                            "eventSlot": {"start": now.isoformat(), "end": end},
                        }
                    ]
                }
            }
        )
        reviewee_bookings = await s21_client.get_reviewee_bookings(now, end, logger_mock)
        assert reviewee_bookings["booking-1"].project_id == "project-1"

    async def test_get_verifier_bookings(
        self,
        s21_client: School21Client,
        logger_mock: LoggerLike,
        now: datetime,
    ) -> None:
        end = now + timedelta(hours=1)
        s21_client._graphql = AsyncMock(
            return_value={
                "calendarEventS21": {
                    "getMyCalendarEvents": [
                        {
                            "id": "review_slot-1",
                            "eventCode": "student_check",
                            "bookings": [
                                {
                                    "id": "booking-1",
                                    "answerId": "answer-1",
                                    "task": {"goalId": "project-1", "goalName": "Project 1"},
                                    "eventSlot": {"start": now.isoformat(), "end": end},
                                }
                            ],
                        },
                        {
                            "id": "something else",
                            "eventCode": "not_student_check",
                        },
                    ]
                }
            }
        )
        verifier_bookings = await s21_client.get_verifier_bookings(now, end, logger_mock)
        assert verifier_bookings["booking-1"].project_id == "project-1"

    async def test_book_disables_retry_middleware(
        self,
        s21_client: School21Client,
        logger_mock: LoggerLike,
        now: datetime,
    ) -> None:
        s21_client._graphql = AsyncMock(return_value={"student": {"addBookingP2PToEventSlot": {"id": "booking-1"}}})
        assert await s21_client.book("answer-1", now, logger_mock) == "booking-1"
        assert s21_client._graphql.await_args.kwargs["overridden_middleware"] == (s21_client._auth_middleware,)

    @pytest.mark.parametrize(
        ("method_name", "args"),
        [
            ("get_user_and_student_id", ()),
            ("get_local_course_goals", ("course-1",)),
            ("get_review_info", ("project-1", "student-1")),
            ("get_task_and_answer", ("module-1",)),
        ],
    )
    async def test_parser_methods_wrap_invalid_responses(
        self,
        s21_client: School21Client,
        logger_mock: LoggerLike,
        method_name: str,
        args: tuple[str, ...],
    ) -> None:
        s21_client._graphql = AsyncMock(return_value={})
        with pytest.raises(School21ParsingError):
            await getattr(s21_client, method_name)(*args, logger_mock)

    async def test_time_based_methods_wrap_invalid_responses(
        self,
        s21_client: School21Client,
        logger_mock: LoggerLike,
        now: datetime,
    ) -> None:
        s21_client._graphql = AsyncMock(return_value={})
        with pytest.raises(School21ParsingError):
            await s21_client.get_slots_info("task", now, now + timedelta(hours=1), logger_mock)
        with pytest.raises(School21ParsingError):
            await s21_client.get_reviewee_bookings(now, now + timedelta(hours=1), logger_mock)
        with pytest.raises(School21ParsingError):
            await s21_client.get_verifier_bookings(now, now + timedelta(hours=1), logger_mock)
        with pytest.raises(School21ParsingError):
            await s21_client.book("answer", now, logger_mock)

    @pytest.mark.parametrize(
        ("code", "error_type"),
        [
            ("TEAM_MEMBER_HAS_NOT_ENOUGH_PEER_REVIEW_POINTS", School21NoPointsError),
            ("TIMETABLE_TIMESLOTS_NOT_FOUND", School21SlotNotFoundError),
            ("OTHER", School21Error),
        ],
    )
    def test_graphql_error_mapping(
        self,
        s21_client: School21Client,
        code: str,
        error_type: type[School21Error],
    ) -> None:
        with pytest.raises(error_type):
            s21_client._raise_error_from_response(
                OperationName.BOOK,
                {"answerId": "answer-1"},
                [{"extensions": {"uiErrorCode": code}}],
            )

    async def test_graphql_returns_data(
        self,
        s21_client: School21Client,
        logger_mock: LoggerLike,
        session_mock: aiohttp.ClientSession,
        response_factory: Callable[..., aiohttp.ClientResponse],
    ) -> None:
        response_mock = response_factory(json={"data": {"user": {"id": "x"}}})
        session_mock.post.return_value = response_context(response_mock)
        s21_client._session_internal = session_mock
        s21_client._campus_id = "cid"
        assert await s21_client._graphql(OperationName.GET_USER, {}, logger_mock) == {"user": {"id": "x"}}

    async def test_graphql_raises_typed_error(
        self,
        s21_client: School21Client,
        logger_mock: LoggerLike,
        session_mock: aiohttp.ClientSession,
        response_factory: Callable[..., aiohttp.ClientResponse],
    ) -> None:
        response_mock = response_factory(
            json={"errors": [{"extensions": {"uiErrorCode": "TIMETABLE_TIMESLOTS_NOT_FOUND"}}]}
        )
        session_mock.post.return_value = response_context(response_mock)
        s21_client._session_internal = session_mock
        s21_client._campus_id = "cid"
        with pytest.raises(School21SlotNotFoundError):
            await s21_client._graphql(OperationName.GET_SLOTS, {}, logger_mock)

    async def test_graphql_http_failure(
        self,
        s21_client: School21Client,
        logger_mock: LoggerLike,
        session_mock: aiohttp.ClientSession,
        response_factory: Callable[..., aiohttp.ClientResponse],
    ) -> None:
        response_mock = response_factory(status=HTTPStatus.BAD_GATEWAY, reason="Bad Gateway", text="upstream")
        session_mock.post.return_value = response_context(response_mock)
        s21_client._session_internal = session_mock
        s21_client._campus_id = "cid"
        with pytest.raises(School21Error) as exc_info:
            await s21_client._graphql(OperationName.GET_USER, {}, logger_mock)
        assert exc_info.value.effective_status == HTTPStatus.BAD_GATEWAY

    async def test_get_campus_id(
        self,
        s21_client: School21Client,
        logger_mock: LoggerLike,
        session_mock: aiohttp.ClientSession,
        response_factory: Callable[..., aiohttp.ClientResponse],
    ) -> None:
        campus_id = "ff19a3a7-12f5-4332-9582-624519c3eaea"
        response_mock = response_factory(
            json={
                "login": "bibikov-lukyan",
                "className": "ADONIS",
                "parallelName": "Core program",
                "expValue": 100,
                "level": 0,
                "expToNextLevel": 399,
                "campus": {"id": campus_id, "shortName": "Hogwarts"},
                "status": "Active",
            }
        )
        session_mock.get.return_value = response_context(response_mock)
        s21_client._session_internal = session_mock

        actual_campus_id = await s21_client._get_campus_id(logger_mock)
        _ = await s21_client._get_campus_id(logger_mock)

        assert campus_id == actual_campus_id == s21_client._campus_id
        session_mock.get.assert_called_once()
