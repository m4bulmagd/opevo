from pydantic import BaseModel, Field


class DashboardMetricsResponse(BaseModel):
    timezone: str
    calls_today: int = Field(ge=0)
    calls_last_7_days: int = Field(ge=0)
    calls_previous_7_days: int = Field(ge=0)
    calls_change_from_previous_7_days: int
    follow_up_flagged_last_7_days: int = Field(ge=0)
    average_duration_seconds_last_7_days: int | None = Field(ge=0)
