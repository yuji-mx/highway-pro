"""API の入出力モデル（pydantic v2）。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class QueryDSL(BaseModel):
    target: str = "ramp"
    where: dict[str, Any] = Field(default_factory=dict)
    range: dict[str, Any] | None = None
    sort: list[dict[str, Any]] = Field(default_factory=list)
    limit: int = 500


class PipelineRun(BaseModel):
    region: str
    from_step: str | None = None
    to_step: str | None = None


class RegionCreate(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9_\-]+$")
    label: str
    country: Literal["JP", "US", "CA"] = "JP"
    range: dict[str, Any]
    dem: str = "jp"
    pbf: dict[str, Any] = Field(default_factory=dict)


class RouteRequest(BaseModel):
    from_facility_id: str | None = None
    to_facility_id: str | None = None
    from_point: list[float] | None = None
    to_point: list[float] | None = None
    via_facility_ids: list[str] = Field(default_factory=list)


class CompareFacilities(BaseModel):
    a: str
    b: str


class StatsCompare(BaseModel):
    range_a: dict[str, Any]
    range_b: dict[str, Any]
    metrics: list[str] | None = None
    overlap: Literal["keep_warn", "exclude_both", "keep_in_a"] = "keep_warn"
    include_toll_link: bool = False
    section_direction: Literal["down", "both", "up"] = "down"
    hit_query: dict[str, Any] | None = None
    bootstrap: int = 2000
    seed: int = 42


class EditCreate(BaseModel):
    target_type: Literal["facility", "ramp", "section"]
    target_id: str
    field: str
    new_value: Any
    comment: str | None = None


class EditRevert(BaseModel):
    comment: str | None = None


class ExportRequest(BaseModel):
    dataset: str = "ramps"
    format: str = "csv"
    query: dict[str, Any] | None = None
    range: dict[str, Any] | None = None
    ids: list[str] | None = None


class ImageRequest(BaseModel):
    bbox: list[float] | None = None
    center: list[float] | None = None
    radius_m: float = 1500
    scale_m_per_px: float | None = None
    width: int = Field(1920, ge=100, le=8000)
    height: int = Field(1080, ge=100, le=8000)
    format: Literal["png", "jpeg", "jpg", "svg"] = "png"
    background: str = "#000000"
    line_scale: float = Field(1.0, gt=0, le=10)
    metric: str = "road_type"
    labels: bool = True
    layers: list[str] | None = None
    query: dict[str, Any] | None = None
    facility_ids: list[str] | None = None
    batch: bool = False


class ReportRequest(BaseModel):
    query: dict[str, Any] | None = None
    ids: list[str] | None = None
    limit: int = Field(20, ge=1, le=500)
    pdf: bool = False


class SavedQuery(BaseModel):
    name: str
    target: str = "ramp"
    query: dict[str, Any]


class SavedRange(BaseModel):
    name: str
    spec: dict[str, Any]
