from __future__ import annotations

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
    cdse_identity_url: str = (
        "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
    )
    cdse_client_id: str = "cdse-public"
    stac_api_url: str = "https://stac.dataspace.copernicus.eu/v1"
    s1_collection: str = "sentinel-1-grd"
    s2_collection: str = "sentinel-2-l2a"
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

    @model_validator(mode="after")
    def validate_settings(self) -> Settings:
        if bool(self.cdse_username) != bool(self.cdse_password):
            raise ValueError("CDSE_USERNAME and CDSE_PASSWORD must be set together")
        for field_name in ("cdse_identity_url", "stac_api_url"):
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
        return self

    def public_config(self) -> dict[str, object]:
        values = self.model_dump(
            mode="json", exclude={"cdse_username", "cdse_password", "cdse_totp"}
        )
        values["cdse_username_configured"] = self.cdse_username is not None
        return values
