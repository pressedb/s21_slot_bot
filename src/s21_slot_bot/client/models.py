from enum import StrEnum
from typing import Annotated

from pydantic import (
    AliasPath,
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    NonNegativeInt,
    PositiveInt,
    computed_field,
)
from pydantic.alias_generators import to_camel

from s21_slot_bot.client.consts import MAX_REQUIRED_REVIEWS, PLATFORM_URL

type CoercedStr = Annotated[str, BeforeValidator(lambda val: str(val) if isinstance(val, int) else val)]
type RequiredReviews = Annotated[PositiveInt, Field(le=MAX_REQUIRED_REVIEWS)]
type BookedReviews = Annotated[NonNegativeInt, Field(le=MAX_REQUIRED_REVIEWS)]
type ActualBooking = RevieweeBooking | VerifierBooking


class ContentType(StrEnum):
    APPLICATION_JSON = "application/json"
    APPLICATION_FORM_URL_ENCODED = "application/x-www-form-urlencoded"


class GrantType(StrEnum):
    AUTHORIZATION_CODE = "authorization_code"
    REFRESH_TOKEN = "refresh_token"


class Tokens(BaseModel):
    access_token: str
    refresh_token: str
    expires_at_epoch: float


class OperationName(StrEnum):
    BOOK = "calendarAddBookingToEventSlot"
    GET_USER = "getCurrentUser"
    GET_REVIEWEE_BOOKINGS = "calendarGetMyBookings"
    GET_VERIFIER_BOOKINGS = "calendarGetEvents"
    GET_CUR_PROJECTS = "getStudentCurrentProjects"
    GET_LOCAL_COURSE_GOALS = "getLocalCourseGoals"
    GET_MODULE = "calendarGetModule"
    GET_PROJECT_INFO = "getProjectInfo"
    GET_SLOTS = "calendarGetNameLessStudentTimeslotsForReview"


class ProjectStatus(StrEnum):
    UNAVAILABLE = "UNAVAILABLE"
    REGISTRATION_IS_OPEN = "REGISTRATION_IS_OPEN"
    READY_TO_START = "READY_TO_START"
    IN_PROGRESS = "IN_PROGRESS"
    P2P_EVALUATIONS = "P2P_EVALUATIONS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class S21Model(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class Project(S21Model):
    id: CoercedStr | None = Field(
        default=None, description="Project ID, may be missing for course projects", alias="goalId"
    )
    name: str = Field(description="Project name", alias="goalName")
    course_id: CoercedStr | None = Field(default=None, description="Course ID", alias="localCourseId")
    course_status: ProjectStatus | None = Field(
        default=None, description="Current course status", alias="displayedCourseStatus"
    )
    status: ProjectStatus | None = Field(default=None, description="Current project status", alias="goalStatus")


class ProjectExtended(Project):
    id: str
    review_info: ReviewInfo


class TimeSlot(S21Model):
    start: AwareDatetime = Field(description="Start time of the available slot")
    end: AwareDatetime = Field(description="End time of the available slot")
    valid_start_times: list[AwareDatetime] = Field(
        description="List of valid time slots that can be booked between start "
        "and end time, considering the duration of the review"
    )
    staff_slot: bool = Field(description="Flag showing whether this is a staff or peer slot")


class ReviewInfo(S21Model):
    required: RequiredReviews = Field(
        description="Number of reviews required for the project", alias="reviewByStudentCount"
    )
    booked: BookedReviews = Field(
        description="Number of reviews already booked for the project", alias="relevantReviewByStudentsCount"
    )


class BookingDirection(StrEnum):
    REVIEWEE = "reviewee"
    VERIFIER = "verifier"


class NotificationKey(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: str = Field(description="Booking ID, taken from S21 or generated for dry-runs")
    direction: BookingDirection


class BookingBase(S21Model):
    id: str = Field(description="Booking ID, taken from S21 or generated for dry-runs")
    start: AwareDatetime = Field(
        description="Start time of the booked slot", validation_alias=AliasPath("eventSlot", "start")
    )
    end: AwareDatetime = Field(
        description="End time of the booked slot", validation_alias=AliasPath("eventSlot", "end")
    )
    call_url: str | None = Field(default=None, description="URL of the online review call", alias="vcLinkUrl")
    checklist_id: str | None = Field(
        default=None,
        description="ID for the checklist URL",
        validation_alias=AliasPath("additionalChecklist", "filledChecklistId"),
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def checklist_url(self) -> str | None:
        return f"{PLATFORM_URL}/checklist/{self.checklist_id}" if self.checklist_id else None


class RevieweeBooking(BookingBase):
    answer_id: str = Field(description="ID required to book a slot for a given project")
    project_id: str = Field(description="Project ID", validation_alias=AliasPath("task", "goalId"))
    project_name: str = Field(description="Project name", validation_alias=AliasPath("task", "goalName"))
    is_online: bool = Field(default=True, description="Whether the review takes place online or not")
    student_login: str | None = Field(
        default=None, description="Verifier student login", validation_alias=AliasPath("verifierUser", "login")
    )


class DryRevieweeBooking(BookingBase):
    answer_id: str = Field(description="ID required to book a slot for a given project")
    project_id: str = Field(description="Project ID", validation_alias=AliasPath("task", "goalId"))
    project_name: str = Field(description="Project name", validation_alias=AliasPath("task", "goalName"))


class VerifierBooking(BookingBase):
    project_id: str | None = Field(default=None, description="Project ID", validation_alias=AliasPath("task", "goalId"))
    project_name: str | None = Field(
        default=None, description="Project name", validation_alias=AliasPath("task", "goalName")
    )
    student_login: str | None = Field(
        default=None,
        description="Reviewee student login",
        validation_alias=AliasPath("verifiableInfo", "verifiableStudents", 0, "login"),
    )


class BookingChanges[T: BookingBase](BaseModel):
    new: list[T]
    cancelled: list[T]
    active: list[T]


class SlotsInfo(S21Model):
    check_duration: int = Field(description="Duration of a review in minutes")
    review_info: ReviewInfo = Field(alias="projectReviewsInfo")
    time_slots: list[TimeSlot]
