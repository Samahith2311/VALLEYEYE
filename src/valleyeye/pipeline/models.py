from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

JobStatus = Literal["QUEUED", "RUNNING", "SUCCEEDED", "PARTIAL", "FAILED"]
StageStatus = Literal["QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "SKIPPED"]

PIPELINE_STAGES = (
    "DISCOVERY",
    "PAIRING",
    "PREPROCESSING",
    "INFERENCE",
    "HAZARD",
    "OSM",
    "INFRASTRUCTURE",
    "CONNECTIVITY",
    "RESULTS",
)


class StageRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    status: StageStatus = "QUEUED"
    progress: int = Field(default=0, ge=0, le=100)
    started_at: datetime | None = None
    ended_at: datetime | None = None
    duration_ms: int | None = None
    cache: Literal["hit", "miss", "bypass", "unavailable"] = "unavailable"
    warnings: list[str] = Field(default_factory=list)


class JobError(BaseModel):
    code: str
    message: str
    stage: str
    retryable: bool = False
    details: dict[str, Any] = Field(default_factory=dict)


class ArtifactDescriptor(BaseModel):
    path: str
    sha256: str
    size_bytes: int
    media_type: str


class JobRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str
    status: JobStatus = "QUEUED"
    current_stage: str | None = None
    progress: int = Field(default=0, ge=0, le=100)
    no_cache: bool = False
    created_at: datetime
    started_at: datetime | None = None
    ended_at: datetime | None = None
    warnings: list[str] = Field(default_factory=list)
    error: JobError | None = None
    stages: list[StageRecord] = Field(default_factory=list)
    artifacts: dict[str, ArtifactDescriptor] = Field(default_factory=dict)


class StageOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    data: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    partial: bool = False


class SceneSummary(BaseModel):
    scene_id: str
    platform: str | None = None
    acquired_at: datetime | None = None
    product_type: str | None = None
    instrument_mode: str | None = None
    polarizations: list[str] = Field(default_factory=list)
    orbit_direction: str | None = None
    relative_orbit: int | None = None
    aoi_coverage_fraction: float | None = None


class DiscoverySummary(BaseModel):
    initial_window_days: int | None = None
    scene_count: int | None = None


class AOISummary(BaseModel):
    aoi_area_km2: float | None = None
    aoi_bbox_wgs84: list[float] = Field(default_factory=list)
    coordinate_reference_system: str = "EPSG:4326"


class PairingSummary(BaseModel):
    pre: SceneSummary | None = None
    post: SceneSummary | None = None
    score: float | None = None
    criteria: dict[str, float] = Field(default_factory=dict)
    alternatives: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    search_window_days: int | None = None


class SceneAnalysis(BaseModel):
    discovery: DiscoverySummary = Field(default_factory=DiscoverySummary)
    pairing: PairingSummary = Field(default_factory=PairingSummary)


class PreprocessingSummary(BaseModel):
    crs: str | None = None
    processing_steps: list[str] = Field(default_factory=list)


class InferenceSummaryDocument(BaseModel):
    valid_pixel_fraction: float | None = None
    valid_pixels: int | None = None
    flooded_pixels: int | None = None
    flooded_area_km2: float | None = None
    model_sha256: str | None = None
    model_name: str | None = None
    model_version: str | None = None


class HazardProductsSummary(BaseModel):
    flood_polygon_count: int | None = None
    debris_candidate_count: int | None = None
    flood_layer: str | None = None
    debris_candidate_layer: str | None = None


class HazardAnalysis(BaseModel):
    preprocessing: PreprocessingSummary = Field(default_factory=PreprocessingSummary)
    inference: InferenceSummaryDocument = Field(default_factory=InferenceSummaryDocument)
    hazard: HazardProductsSummary = Field(default_factory=HazardProductsSummary)


class OSMSummary(BaseModel):
    provider: str | None = None
    snapshot_at: datetime | None = None
    feature_count: int | None = None
    attribution: str | None = None
    license: str | None = None
    quality: dict[str, int] = Field(default_factory=dict)
    layer: str | None = None
    provenance: dict[str, Any] = Field(default_factory=dict)


class BuildingExposureSummary(BaseModel):
    total_count: int | None = None
    affected_count: int | None = None
    total_area_km2: float | None = None
    affected_area_km2: float | None = None
    affected_fraction: float | None = None


class RoadExposureSummary(BaseModel):
    segment_count: int | None = None
    open_km: float | None = None
    potentially_blocked_km: float | None = None
    blocked_km: float | None = None
    unknown_coverage_km: float | None = None


class ExposureAnalysis(BaseModel):
    building_summary: BuildingExposureSummary = Field(default_factory=BuildingExposureSummary)
    bridge_affected_count: int | None = None
    bridge_count: int | None = None
    road_summary: RoadExposureSummary = Field(default_factory=RoadExposureSummary)
    segment_count: int | None = None
    road_layer: str | None = None
    building_layer: str | None = None
    bridge_layer: str | None = None
    coverage_available: bool | None = None


class InfrastructureAnalysis(BaseModel):
    osm: OSMSummary = Field(default_factory=OSMSummary)
    exposure: ExposureAnalysis = Field(default_factory=ExposureAnalysis)


class ConnectivitySummaryDocument(BaseModel):
    connected: int | None = None
    detour: int | None = None
    cut_off: int | None = None
    no_baseline_access: int | None = None


class AffectedSettlementSummary(BaseModel):
    settlement_id: str
    name: str | None = None
    status: Literal["DETOUR", "CUT_OFF"]
    before_distance_m: float | None = None
    after_distance_m: float | None = None
    before_time_s: float | None = None
    after_time_s: float | None = None
    detour_ratio: float | None = None
    nearest_source_before: str | None = None
    nearest_source_after: str | None = None
    blocking_road_ids: list[str] = Field(default_factory=list)


class ConnectivityAnalysis(BaseModel):
    summary: ConnectivitySummaryDocument = Field(default_factory=ConnectivitySummaryDocument)
    settlement_count: int | None = None
    layer: str | None = None
    most_affected_settlements: list[AffectedSettlementSummary] = Field(default_factory=list)


class AnalysisDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1.0.0"
    job_id: str
    status: Literal["SUCCEEDED", "PARTIAL"]
    aoi: dict[str, Any]
    aoi_summary: AOISummary = Field(default_factory=AOISummary)
    event_date: str
    scenes: SceneAnalysis = Field(default_factory=SceneAnalysis)
    hazards: HazardAnalysis = Field(default_factory=HazardAnalysis)
    infrastructure: InfrastructureAnalysis = Field(default_factory=InfrastructureAnalysis)
    connectivity: ConnectivityAnalysis = Field(default_factory=ConnectivityAnalysis)
    provenance: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
