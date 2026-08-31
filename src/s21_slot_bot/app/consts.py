from datetime import timedelta

PYDANTIC_DATETIME_DOCS_URL = (
    "https://pydantic.dev/docs/validation/2.0/usage/types/datetime/#validation-of-datetime-types"
)

MIN_INTERVAL_SEC = 10
MAX_INTERVAL_SEC = 3600
MIN_NUM_BOTS = 1
MAX_NUM_BOTS = 10

BOOKING_REFRESHER_JOB_NAME = "booking_refresher"
CURRENT_BOOKINGS_SEARCH_WINDOW = timedelta(weeks=4)
UPCOMING_REVIEW_REMINDER_WINDOW = timedelta(minutes=15)

STATUS_LINE_INDENT = 5
