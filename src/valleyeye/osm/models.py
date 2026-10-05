from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

from shapely.geometry.base import BaseGeometry

OSMCategory = Literal["building", "road", "settlement", "healthcare"]


@dataclass(frozen=True)
class OSMFeature:
    feature_id: str
    osm_type: str
    osm_id: int
    category: OSMCategory
    geometry: BaseGeometry
    tags: dict[str, str]
    snapshot_date: date


@dataclass(frozen=True)
class OSMQualitySummary:
    extracted_rows: int
    retained_features: int
    repaired_geometries: int
    dropped_empty_geometries: int
    ignored_features: int
    clipped_features: int


@dataclass(frozen=True)
class OSMDataset:
    provider: str
    snapshot_at: datetime
    response_sha256: str
    query_sha256: str
    query: dict[str, object]
    features: tuple[OSMFeature, ...]
    quality: OSMQualitySummary

    def of_category(self, category: OSMCategory) -> tuple[OSMFeature, ...]:
        return tuple(feature for feature in self.features if feature.category == category)

    @property
    def roads(self) -> tuple[OSMFeature, ...]:
        return self.of_category("road")

    @property
    def buildings(self) -> tuple[OSMFeature, ...]:
        return self.of_category("building")

    @property
    def settlements(self) -> tuple[OSMFeature, ...]:
        return self.of_category("settlement")

    @property
    def healthcare(self) -> tuple[OSMFeature, ...]:
        return self.of_category("healthcare")

    @property
    def bridges(self) -> tuple[OSMFeature, ...]:
        return tuple(
            feature
            for feature in self.roads
            if feature.tags.get("bridge", "no") not in {"no", "false", "0"}
            and feature.tags.get("tunnel") not in {"yes", "true", "1"}
        )

    @property
    def connectivity_sources(self) -> tuple[OSMFeature, ...]:
        town_values = {"city", "town"}
        towns = tuple(
            feature for feature in self.settlements if feature.tags.get("place") in town_values
        )
        return (*self.healthcare, *towns)
