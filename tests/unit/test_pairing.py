from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from shapely.geometry import box, mapping

from valleyeye.cdse.models import PairingPolicy, SceneMetadata
from valleyeye.cdse.pairing import select_s1_pair, select_s1_pair_adaptively
from valleyeye.core.errors import ErrorCode, ValleyeyeError

EVENT = date(2026, 9, 10)
GEOMETRY: dict[str, Any] = mapping(box(0, 0, 1, 1))


def _policy(**updates: object) -> PairingPolicy:
    values: dict[str, object] = {
        "initial_window_days": 12,
        "max_window_days": 96,
        "window_multiplier": 2,
        "minimum_aoi_coverage": 0.8,
        "long_gap_warning_days": 24,
        "weight_overlap": 0.25,
        "weight_pre_closeness": 0.25,
        "weight_post_closeness": 0.25,
        "weight_same_platform": 0.1,
        "weight_coverage": 0.15,
    }
    values.update(updates)
    return PairingPolicy(**values)


def _scene(
    scene_id: str,
    days_from_event: int,
    *,
    relative_orbit: int = 55,
    polarizations: tuple[str, ...] = ("VV", "VH"),
    coverage: float = 0.95,
    platform: str = "sentinel-1a",
    geometry: dict[str, Any] = GEOMETRY,
) -> SceneMetadata:
    return SceneMetadata(
        scene_id=scene_id,
        platform=platform,
        acquired_at=datetime.combine(
            EVENT + timedelta(days=days_from_event), datetime.min.time(), tzinfo=UTC
        ),
        product_type="GRD",
        instrument_mode="IW",
        polarizations=polarizations,
        orbit_direction="ASCENDING",
        relative_orbit=relative_orbit,
        geometry=geometry,
        aoi_coverage_fraction=coverage,
    )


def test_ideal_pair_selected_with_score_breakdown() -> None:
    pre, post = _scene("pre", -2), _scene("post", 1)

    result = select_s1_pair([pre, post], EVENT, _policy(), 12)

    assert result.pre.scene_id == "pre"
    assert result.post.scene_id == "post"
    assert 0 <= result.score <= 1
    assert result.criteria["footprint_overlap"] == pytest.approx(1.0)
    assert result.alternatives == ()


@pytest.mark.parametrize(
    ("pre", "post", "reason"),
    [
        (_scene("pre", -2, relative_orbit=10), _scene("post", 1), "mismatched_relative_orbit"),
        (
            _scene("pre", -2, polarizations=("VV",)),
            _scene("post", 1),
            "mismatched_polarizations",
        ),
    ],
)
def test_hard_pair_criteria_are_not_relaxed(
    pre: SceneMetadata, post: SceneMetadata, reason: str
) -> None:
    with pytest.raises(ValleyeyeError) as caught:
        select_s1_pair([pre, post], EVENT, _policy(), 12)

    assert caught.value.code == ErrorCode.S1_NO_VALID_PAIR
    assert reason in caught.value.details["alternatives"][0]["rejection_reasons"]


def test_insufficient_coverage_has_specific_error() -> None:
    with pytest.raises(ValleyeyeError) as caught:
        select_s1_pair([_scene("pre", -2, coverage=0.2), _scene("post", 1)], EVENT, _policy(), 12)

    assert caught.value.code == ErrorCode.S1_INSUFFICIENT_AOI_COVERAGE


def test_asymmetric_time_gaps_choose_closer_combined_pair() -> None:
    pre_far = _scene("pre-far", -10, relative_orbit=11)
    post_near = _scene("post-near", 2, relative_orbit=11)
    pre_near = _scene("pre-near", -1, relative_orbit=22)
    post_far = _scene("post-far", 20, relative_orbit=22)

    result = select_s1_pair([pre_far, post_near, pre_near, post_far], EVENT, _policy(), 24)

    assert (result.pre.scene_id, result.post.scene_id) == ("pre-far", "post-near")


def test_tie_breaking_is_deterministic() -> None:
    scenes = [_scene("z-pre", -2), _scene("a-pre", -2), _scene("z-post", 2), _scene("a-post", 2)]

    result = select_s1_pair(scenes, EVENT, _policy(), 12)

    assert (result.pre.scene_id, result.post.scene_id) == ("a-pre", "a-post")


def test_empty_scene_set_returns_no_scenes_error() -> None:
    with pytest.raises(ValleyeyeError) as caught:
        select_s1_pair([], EVENT, _policy(), 12)

    assert caught.value.code == ErrorCode.NO_S1_SCENES


def test_pair_selection_accepts_a_second_aoi_and_event_date() -> None:
    event = date(2020, 4, 15)
    aoi_geometry: dict[str, Any] = mapping(box(72, 19, 72.02, 19.02))
    pre = SceneMetadata(
        scene_id="historic-pre",
        platform="sentinel-1b",
        acquired_at=datetime(2020, 4, 13, tzinfo=UTC),
        product_type="GRD",
        instrument_mode="IW",
        polarizations=("VV", "VH"),
        orbit_direction="DESCENDING",
        relative_orbit=19,
        geometry=aoi_geometry,
        aoi_coverage_fraction=1.0,
    )
    post = pre.model_copy(
        update={
            "scene_id": "historic-post",
            "acquired_at": datetime(2020, 4, 17, tzinfo=UTC),
        }
    )

    result = select_s1_pair([pre, post], event, _policy(), 12)

    assert result.pre.scene_id == "historic-pre"
    assert result.post.scene_id == "historic-post"


def test_adaptive_search_expands_until_pair_is_found() -> None:
    searched: list[tuple[int, int]] = []
    pre, post = _scene("pre", -20), _scene("post", 20)

    async def search(start: datetime, end: datetime) -> list[SceneMetadata]:
        searched.append(((EVENT - start.date()).days, (end.date() - EVENT).days))
        return [pre, post] if (EVENT - start.date()).days >= 24 else []

    result = asyncio.run(select_s1_pair_adaptively(search, EVENT, _policy()))

    assert result.search_window_days == 24
    assert searched == [(12, 12), (24, 24)]
