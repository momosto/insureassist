"""Runtime configuration (12-factor: everything from environment variables or a .env file)."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="IA_", extra="ignore")

    # --- LLM -------------------------------------------------------------------------------------------------
    # "offline" = deterministic planner (no API key, no cost; used for the public demo and CI evals)
    # "anthropic" = Claude via the Messages API with tool use
    llm_mode: str = "offline"
    main_model: str = "claude-sonnet-5-5"
    small_model: str = "claude-haiku-4-5"
    effort: str = "medium"
    use_server_fallbacks: bool = True
    max_tool_calls_per_turn: int = 5

    # --- budgets (G-08) ----------------------------------------------------------------------------------------
    daily_token_budget: int = 2_000_000
    max_turns_per_customer_per_day: int = 60

    # --- identity / sessions (IA-01) ---------------------------------------------------------------------------
    session_idle_minutes: int = 30
    otp_max_attempts: int = 3
    otp_lock_minutes: int = 30
    delegated_token_secret: str = "dev-only-insureassist-delegation-secret-change-me"
    delegated_token_minutes: int = 15
    confirmation_minutes: int = 5
    redis_url: str | None = None
    demo_show_otp: bool = True  # the simulator shows the "SMS"; never in production

    # --- storage -------------------------------------------------------------------------------------------------
    database_url: str = "sqlite:///./insureassist.db"

    # --- tools / core systems ------------------------------------------------------------------------------------
    # "local" = the agent calls the tool service in-process; "mcp" = over MCP streamable HTTP
    tool_transport: str = "local"
    mcp_url: str = "http://localhost:8101/mcp"
    # "fixtures" = seeded demo data that mirrors InsureHub/LendHub seeds; "http" = real core APIs
    backend: str = "fixtures"
    lendhub_url: str = "http://localhost:8080"
    lendhub_channel_user: str = "channel@lendhub.demo"
    lendhub_channel_password: str = "Demo123!"
    insurehub_url: str = "http://localhost:5080"
    payments_url: str = "http://localhost:5100"

    # --- WhatsApp Cloud API -------------------------------------------------------------------------------------
    whatsapp_app_secret: str = "dev-whatsapp-app-secret"
    whatsapp_verify_token: str = "dev-verify-token"
    whatsapp_access_token: str | None = None  # unset = outbound messages go to the simulator outbox
    whatsapp_phone_number_id: str | None = None

    # --- staff inbox (HTTP Basic until Keycloak, platform phase 3) -------------------------------------------------
    inbox_password: str = "Demo123!"

    # --- events (payment.succeeded, policy.lapsed, loan.arrears-changed) ------------------------------------------
    events_shared_secret: str = "dev-insurehub-callback-secret"
    amqp_url: str | None = None


@lru_cache
def settings() -> Settings:
    return Settings()
