from __future__ import annotations

from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

if TYPE_CHECKING:
    from valleyeye.core.settings import Settings


class SceneMetadata(BaseModel):
    model_config = ConfigDict(frozen=True)

    scene_id: str
    platform: str
    acquired_at: datetime
    product_type: str | None
    instrument_mode: str | None
    polarizations: tuple[str, ...]
    orbit_direction: str | None
    relative_orbit: int | None
    geometry: dict[str, Any]
    aoi_coverage_fraction: float = Field(ge=0, le=1)
    cloud_cover: float | None = Field(default=None, ge=0, le=100)
    asset_href: str | None = None

    @field_validator("acquired_at")
    @classmethod
    def require_aware_datetime(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("scene acquisition datetime must include a timezone")
        return value.astimezone(UTC)

    @field_validator("polarizations")
    @classmethod
    def normalize_polarizations(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(sorted({item.upper() for item in value}))


class PairAlternative(BaseModel):
    pre_scene_id: str
    post_scene_id: str
    score: float | None = None
    rejection_reasons: tuple[str, ...] = ()


class PairSelection(BaseModel):
    pre: SceneMetadata
    post: SceneMetadata
    score: float = Field(ge=0, le=1)
    criteria: dict[str, float]
    alternatives: tuple[PairAlternative, ...]
    warnings: tuple[str, ...]
    search_window_days: int


class PairingPolicy(BaseModel):
    initial_window_days: int = Field(ge=1)
    max_window_days: int = Field(ge=1)
    window_multiplier: int = Field(ge=2)
    minimum_aoi_coverage: float = Field(gt=0, le=1)
    long_gap_warning_days: int = Field(ge=1)
    weight_overlap: float = Field(ge=0)
    weight_pre_closeness: float = Field(ge=0)
    weight_post_closeness: float = Field(ge=0)
    weight_same_platform: float = Field(ge=0)
    weight_coverage: float = Field(ge=0)

    @field_validator("max_window_days")
    @classmethod
    def max_not_less_than_initial(cls, value: int, info: Any) -> int:
        initial = info.data.get("initial_window_days")
        if initial is not None and value < initial:
            raise ValueError("maximum search window cannot be smaller than initial window")
        return value

    @field_validator("weight_coverage")
    @classmethod
    def score_weights_sum_to_one(cls, value: float, info: Any) -> float:
        weights = [
            info.data.get("weight_overlap", 0),
            info.data.get("weight_pre_closeness", 0),
            info.data.get("weight_post_closeness", 0),
            info.data.get("weight_same_platform", 0),
            value,
        ]
        if abs(sum(weights) - 1.0) > 1e-9:
            raise ValueError("pair score weights must sum to one")
        return value

    @classmethod
    def from_settings(cls, settings: Settings) -> PairingPolicy:
        return cls(
            initial_window_days=settings.s1_initial_window_days,
            max_window_days=settings.s1_max_window_days,
            window_multiplier=settings.s1_window_multiplier,
            minimum_aoi_coverage=settings.min_s1_aoi_coverage,
            long_gap_warning_days=settings.pair_long_gap_warning_days,
            weight_overlap=settings.pair_weight_overlap,
            weight_pre_closeness=settings.pair_weight_pre_closeness,
            weight_post_closeness=settings.pair_weight_post_closeness,
            weight_same_platform=settings.pair_weight_same_platform,
            weight_coverage=settings.pair_weight_coverage,
        )


def event_day(value: date | datetime) -> date:
    return value.date() if isinstance(value, datetime) else value
