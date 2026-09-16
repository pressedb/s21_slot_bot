AUTH_URL = "https://auth.21-school.ru/auth/realms/EduPowerKeycloak/protocol/openid-connect"
PLATFORM_URL = "https://platform.21-school.ru"
GRAPHQL_URL = f"{PLATFORM_URL}/services/graphql"
PUBLIC_API_URL = f"{PLATFORM_URL}/services/21-school/api"
DEFAULT_TOKEN_EXPIRATION_SEC = 32400  # 9 hours (default from s21 is 36000 or 10 hours)
CLIENT_ID = "school21"
USER_ROLE = "STUDENT"
X_EDU_PRODUCT_ID = "96098f4b-5708-4c42-a62c-6893419169b3"

GRAPHQL_QUERIES_MODULE = "s21_slot_bot.client.queries"

MIN_REQUIRED_REVIEWS = 1
MAX_REQUIRED_REVIEWS = 3
