from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from datetime import UTC, date, datetime

from shapely.geometry import shape

from valleyeye.cdse.models import PairAlternative, PairingPolicy, PairSelection, SceneMetadata
from valleyeye.cdse.stac import event_window
from valleyeye.core.errors import ErrorCode, ValleyeyeError
from valleyeye.core.geometry import geometry_area_km2


def select_s1_pair(
    scenes: Iterable[SceneMetadata], event_date: date, policy: PairingPolicy, window_days: int
) -> PairSelection:
    candidates = list({scene.scene_id: scene for scene in scenes}.values())
    event = event_date
    before = sorted(
        (scene for scene in candidates if scene.acquired_at.date() < event),
        key=lambda scene: (scene.acquired_at, scene.scene_id),
    )
    after = sorted(
        (scene for scene in candidates if scene.acquired_at.date() > event),
        key=lambda scene: (scene.acquired_at, scene.scene_id),
    )
    if not candidates:
        raise ValleyeyeError(
            ErrorCode.NO_S1_SCENES,
            "No Sentinel-1 scenes intersect the AOI and search window",
            "DISCOVERY",
        )

    evaluated: list[
        tuple[SceneMetadata, SceneMetadata, tuple[str, ...], float | None, dict[str, float]]
    ] = []
    for pre in before:
        for post in after:
            reasons = _rejection_reasons(pre, post, policy.minimum_aoi_coverage)
            criteria = _pair_criteria(pre, post, event, policy, window_days)
            score = _score(criteria, policy) if not reasons else None
            evaluated.append((pre, post, reasons, score, criteria))

    valid = [pair for pair in evaluated if not pair[2]]
    alternatives = _alternatives(evaluated, event)
    if not valid:
        coverage_only = bool(evaluated) and all(
            all(reason.startswith("insufficient_aoi_coverage") for reason in pair[2])
            for pair in evaluated
        )
        code = (
            ErrorCode.S1_INSUFFICIENT_AOI_COVERAGE if coverage_only else ErrorCode.S1_NO_VALID_PAIR
        )
        raise ValleyeyeError(
            code,
            "No Sentinel-1 pre/post pair satisfies all hard pairing requirements",
            "PAIRING",
            details={"alternatives": [item.model_dump(mode="json") for item in alternatives]},
        )

    ranked = sorted(
        valid,
        key=lambda pair: (-(pair[3] or 0.0), pair[0].scene_id, pair[1].scene_id),
    )
    pre, post, _, score, criteria = ranked[0]
    assert score is not None
    alternatives = _alternatives(
        [
            pair
            for pair in evaluated
            if (pair[0].scene_id, pair[1].scene_id) != (pre.scene_id, post.scene_id)
        ],
        event,
        mark_valid_as_lower_score=True,
    )
    warnings: list[str] = []
    if pre.platform != post.platform:
        warnings.append("pre/post scenes use different Sentinel-1 platforms")
    total_gap = (1 - criteria["pre_closeness"]) * window_days
    total_gap += (1 - criteria["post_closeness"]) * window_days
    if total_gap > policy.long_gap_warning_days:
        warnings.append("long temporal gap between the event and selected scenes")
    return PairSelection(
        pre=pre,
        post=post,
        score=score,
        criteria=criteria,
        alternatives=tuple(alternatives),
        warnings=tuple(warnings),
        search_window_days=window_days,
    )


async def select_s1_pair_adaptively(
    search: Callable[[datetime, datetime], Awaitable[list[SceneMetadata]]],
    event_date: date,
    policy: PairingPolicy,
) -> PairSelection:
    window_days = policy.initial_window_days
    collected: dict[str, SceneMetadata] = {}
    last_error: ValleyeyeError | None = None
    while True:
        start, end = event_window(event_date, window_days)
        for scene in await search(start, end):
            collected[scene.scene_id] = scene
        try:
            return select_s1_pair(collected.values(), event_date, policy, window_days)
        except ValleyeyeError as exc:
            if exc.code not in {
                ErrorCode.NO_S1_SCENES,
                ErrorCode.S1_NO_VALID_PAIR,
                ErrorCode.S1_INSUFFICIENT_AOI_COVERAGE,
            }:
                raise
            last_error = exc
        if window_days == policy.max_window_days:
            assert last_error is not None
            raise last_error
        window_days = min(policy.max_window_days, window_days * policy.window_multiplier)


def _rejection_reasons(
    pre: SceneMetadata, post: SceneMetadata, minimum_coverage: float
) -> tuple[str, ...]:
    reasons: list[str] = []
    required = (
        ("instrument_mode", pre.instrument_mode, post.instrument_mode),
        ("polarizations", pre.polarizations, post.polarizations),
        ("orbit_direction", pre.orbit_direction, post.orbit_direction),
        ("relative_orbit", pre.relative_orbit, post.relative_orbit),
        ("product_type", pre.product_type, post.product_type),
    )
    for name, left, right in required:
        if left is None or right is None:
            reasons.append(f"missing_{name}")
        elif left != right:
            reasons.append(f"mismatched_{name}")
    if pre.aoi_coverage_fraction < minimum_coverage:
        reasons.append("insufficient_aoi_coverage_pre")
    if post.aoi_coverage_fraction < minimum_coverage:
        reasons.append("insufficient_aoi_coverage_post")
    return tuple(reasons)


def _pair_criteria(
    pre: SceneMetadata,
    post: SceneMetadata,
    event_date: date,
    policy: PairingPolicy,
    window_days: int,
) -> dict[str, float]:
    event_start = datetime.combine(event_date, datetime.min.time(), tzinfo=UTC)
    pre_gap = max(0.0, (event_start - pre.acquired_at).total_seconds() / 86400)
    post_gap = max(0.0, (post.acquired_at - event_start).total_seconds() / 86400)
    pre_closeness = max(0.0, 1.0 - pre_gap / window_days)
    post_closeness = max(0.0, 1.0 - post_gap / window_days)
    pre_geom = shape(pre.geometry)
    post_geom = shape(post.geometry)
    intersection_area = geometry_area_km2(pre_geom.intersection(post_geom))
    union_area = geometry_area_km2(pre_geom) + geometry_area_km2(post_geom) - intersection_area
    overlap = intersection_area / union_area if union_area > 0 else 0.0
    return {
        "footprint_overlap": min(1.0, max(0.0, overlap)),
        "pre_closeness": pre_closeness,
        "post_closeness": post_closeness,
        "same_platform": float(pre.platform == post.platform),
        "coverage": min(pre.aoi_coverage_fraction, post.aoi_coverage_fraction),
    }


def _score(criteria: dict[str, float], policy: PairingPolicy) -> float:
    weighted = (
        criteria["footprint_overlap"] * policy.weight_overlap
        + criteria["pre_closeness"] * policy.weight_pre_closeness
        + criteria["post_closeness"] * policy.weight_post_closeness
        + criteria["same_platform"] * policy.weight_same_platform
        + criteria["coverage"] * policy.weight_coverage
    )
    return min(1.0, max(0.0, weighted))


def _alternatives(
    pairs: list[
        tuple[SceneMetadata, SceneMetadata, tuple[str, ...], float | None, dict[str, float]]
    ],
    event_date: date,
    mark_valid_as_lower_score: bool = False,
) -> list[PairAlternative]:
    ordered = sorted(
        pairs,
        key=lambda pair: (
            len(pair[2]),
            abs((event_date - pair[0].acquired_at.date()).days)
            + abs((pair[1].acquired_at.date() - event_date).days),
            pair[0].scene_id,
            pair[1].scene_id,
        ),
    )
    return [
        PairAlternative(
            pre_scene_id=pre.scene_id,
            post_scene_id=post.scene_id,
            score=score,
            rejection_reasons=(
                reasons or (("lower_score_than_selected",) if mark_valid_as_lower_score else ())
            ),
        )
        for pre, post, reasons, score, _ in ordered[:20]
    ]
