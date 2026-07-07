from datetime import date, datetime
from typing import Optional
from pydantic import BaseModel


class WeeklyReportCreate(BaseModel):
    start_date: date; end_date: date


class WeeklyReportOut(BaseModel):
    id: int; start_date: date; end_date: date
    report_text: str; best_topics: Optional[dict] = None
    worst_topics: Optional[dict] = None
    angle_performance: Optional[dict] = None
    content_type_performance: Optional[dict] = None
    template_performance: Optional[dict] = None
    performance_summary: Optional[dict] = None
    prediction_calibration: Optional[dict] = None
    recommendations: Optional[dict] = None
    created_at: datetime
    model_config = {"from_attributes": True}
