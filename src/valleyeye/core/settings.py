from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

from pydantic import AliasChoices, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="VALLEYEYE_",
        extra="ignore",
    )

    cdse_username: str | None = Field(
        default=None,
        validation_alias=AliasChoices("CDSE_USERNAME", "VALLEYEYE_CDSE_USERNAME"),
    )
    cdse_password: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("CDSE_PASSWORD", "VALLEYEYE_CDSE_PASSWORD"),
    )
    cdse_totp: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("CDSE_TOTP", "VALLEYEYE_CDSE_TOTP"),
    )
    ohsome_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("OHSOME_API_KEY", "VALLEYEYE_OHSOME_API_KEY"),
    )
    cdse_identity_url: str = (
        "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
    )
    cdse_client_id: str = "cdse-public"
    stac_api_url: str = "https://stac.dataspace.copernicus.eu/v1"
    s1_collection: str = "sentinel-1-grd"
    s2_collection: str = "sentinel-2-l2a"
    ohsome_api_url: str = "https://api.heigit.org/ohsome-api/v2-rc"
    ohsome_timeout_seconds: float = 240.0
    osm_aoi_buffer_m: float = 5000.0
    http_timeout_seconds: float = 30.0
    retry_max_attempts: int = 4
    token_refresh_skew_seconds: int = 60
    s1_initial_window_days: int = 12
    s1_max_window_days: int = 96
    s1_window_multiplier: int = 2
    min_aoi_area_km2: float = 0.1
    max_aoi_area_km2: float = 5000.0
    min_s1_aoi_coverage: float = 0.8
    pair_long_gap_warning_days: int = 24
    pair_weight_overlap: float = 0.25
    pair_weight_pre_closeness: float = 0.25
    pair_weight_post_closeness: float = 0.25
    pair_weight_same_platform: float = 0.10
    pair_weight_coverage: float = 0.15
    model_weights_path: Path = Path("weights/kuro-siwo-snunet.pt")
    inference_device: str = "auto"
    flood_probability_threshold: float = 0.5
    inference_tile_size: int = 224
    inference_overlap: int = 32
    snap_gpt_path: Path | None = None
    snap_pixel_spacing_m: float = 10.0
    snap_aoi_buffer_m: float = 500.0
    minimum_flood_polygon_area_km2: float = 0.001
    candidate_backscatter_change_threshold_db: float = 3.0
    minimum_candidate_polygon_area_km2: float = 0.001
    building_affected_fraction_threshold: float = 0.1
    road_potentially_blocked_fraction: float = 0.01
    road_blocked_fraction: float = 0.5
    bridge_hazard_buffer_m: float = 20.0
    bridge_block_on_intersection: bool = True
    network_max_snap_distance_m: float = 500.0
    network_detour_ratio_threshold: float = 1.25
    remove_potentially_blocked_edges: bool = False
    road_speed_defaults_kmh: dict[str, float] = Field(
        default_factory=lambda: {
            "motorway": 90.0,
            "trunk": 70.0,
            "primary": 60.0,
            "secondary": 50.0,
            "tertiary": 40.0,
            "unclassified": 30.0,
            "residential": 30.0,
            "service": 20.0,
            "track": 15.0,
        }
    )
    job_data_dir: Path = Path("data/jobs")
    stage_cache_dir: Path = Path("data/cache")
    max_concurrent_jobs: int = 2
    max_queued_jobs: int = 50
    stage_cache_ttl_seconds: int = 86400

    @model_validator(mode="after")
    def validate_settings(self) -> Settings:
        if bool(self.cdse_username) != bool(self.cdse_password):
            raise ValueError("CDSE_USERNAME and CDSE_PASSWORD must be set together")
        for field_name in ("cdse_identity_url", "stac_api_url", "ohsome_api_url"):
            parsed = urlparse(getattr(self, field_name))
            if parsed.scheme != "https" or not parsed.netloc:
                raise ValueError(f"{field_name} must be an HTTPS URL")
        if self.http_timeout_seconds <= 0:
            raise ValueError("HTTP timeout must be positive")
        if self.retry_max_attempts < 1:
            raise ValueError("Retry attempts must be at least one")
        if self.s1_initial_window_days < 1 or self.s1_max_window_days < self.s1_initial_window_days:
            raise ValueError("S1 search window limits are inconsistent")
        if self.s1_window_multiplier < 2:
            raise ValueError("S1 search window multiplier must be at least two")
        if self.min_aoi_area_km2 <= 0 or self.max_aoi_area_km2 <= self.min_aoi_area_km2:
            raise ValueError("AOI area limits are inconsistent")
        if not 0 < self.min_s1_aoi_coverage <= 1:
            raise ValueError("Minimum S1 AOI coverage must be in (0, 1]")
        weights = (
            self.pair_weight_overlap,
            self.pair_weight_pre_closeness,
            self.pair_weight_post_closeness,
            self.pair_weight_same_platform,
            self.pair_weight_coverage,
        )
        if any(weight < 0 for weight in weights) or abs(sum(weights) - 1.0) > 1e-9:
            raise ValueError("Pair score weights must be non-negative and sum to one")
        if not 0 < self.flood_probability_threshold <= 1:
            raise ValueError("Flood probability threshold must be in (0, 1]")
        if self.inference_tile_size < 32 or self.inference_tile_size % 16:
            raise ValueError("Inference tile size must be at least 32 and divisible by 16")
        if self.inference_overlap < 0 or self.inference_overlap * 2 >= self.inference_tile_size:
            raise ValueError(
                "Inference overlap must be non-negative and less than half the tile size"
            )
        if self.snap_pixel_spacing_m <= 0:
            raise ValueError("SNAP pixel spacing must be positive")
        if self.snap_aoi_buffer_m < 0:
            raise ValueError("SNAP AOI buffer must be non-negative")
        if self.minimum_flood_polygon_area_km2 < 0 or self.minimum_candidate_polygon_area_km2 < 0:
            raise ValueError("Minimum polygon areas must be non-negative")
        if self.candidate_backscatter_change_threshold_db <= 0:
            raise ValueError("Candidate backscatter-change threshold must be positive")
        if self.ohsome_timeout_seconds <= 0 or self.osm_aoi_buffer_m < 0:
            raise ValueError("ohsome timeout must be positive and OSM buffer non-negative")
        if not 0 <= self.building_affected_fraction_threshold <= 1:
            raise ValueError("Building affected fraction threshold must be in [0, 1]")
        if not 0 <= self.road_potentially_blocked_fraction < self.road_blocked_fraction <= 1:
            raise ValueError("Road impact fractions must satisfy 0 <= potential < blocked <= 1")
        if self.bridge_hazard_buffer_m < 0 or self.network_max_snap_distance_m <= 0:
            raise ValueError(
                "Bridge buffer must be non-negative and network snap distance positive"
            )
        if self.network_detour_ratio_threshold < 1:
            raise ValueError("Network detour ratio threshold must be at least one")
        if not self.road_speed_defaults_kmh or any(
            speed <= 0 for speed in self.road_speed_defaults_kmh.values()
        ):
            raise ValueError("Road default speeds must be positive")
        if self.max_concurrent_jobs < 1 or self.max_queued_jobs < 1:
            raise ValueError("Job concurrency and queue limits must be positive")
        if self.stage_cache_ttl_seconds < 0:
            raise ValueError("Stage cache TTL must be non-negative")
        return self

    def public_config(self) -> dict[str, object]:
        values = self.model_dump(
            mode="json",
            exclude={"cdse_username", "cdse_password", "cdse_totp", "ohsome_api_key"},
        )
        values["cdse_username_configured"] = self.cdse_username is not None
        return values
