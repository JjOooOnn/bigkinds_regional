from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field, model_validator

from src.regions import REGION_DISPLAY_ORDER, SUPPORTED_REGIONS


class JobCreateRequest(BaseModel):
    start_date: date
    end_date: date
    all_regions: bool = True
    regions: list[str] = Field(default_factory=list)
    headed: bool = False
    resume: bool = False
    resume_from_job_id: str | None = None
    max_issues: int | None = Field(default=None, ge=1)
    timeout_seconds: int = Field(default=30, ge=1, le=300)
    retries: int = Field(default=2, ge=0, le=2)
    link_delay_seconds: float = Field(default=0.5, ge=0, le=60)
    debug: bool = False

    @model_validator(mode="after")
    def validate_selection(self):
        if self.start_date > self.end_date:
            raise ValueError("시작일은 종료일보다 늦을 수 없습니다.")
        if self.all_regions:
            self.regions = list(REGION_DISPLAY_ORDER)
        else:
            self.regions = list(dict.fromkeys(self.regions))
            if not self.regions:
                raise ValueError("점검할 지역을 하나 이상 선택해 주세요.")
            unknown = [region for region in self.regions if region not in SUPPORTED_REGIONS]
            if unknown:
                raise ValueError(f"존재하지 않는 지역명입니다: {', '.join(unknown)}")
        return self

    def to_job_config(self) -> dict:
        return {
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "regions": self.regions,
            "headed": self.headed,
            "resume": self.resume,
            "resume_from_job_id": self.resume_from_job_id or "",
            "max_issues": self.max_issues,
            "timeout_seconds": self.timeout_seconds,
            "retries": self.retries,
            "link_delay_seconds": self.link_delay_seconds,
            "debug": self.debug,
        }


class MessageResponse(BaseModel):
    message: str

