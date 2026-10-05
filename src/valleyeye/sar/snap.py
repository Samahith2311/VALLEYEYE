from __future__ import annotations

import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from pyproj import CRS
from shapely.geometry.base import BaseGeometry

from valleyeye.core.errors import ErrorCode, ValleyeyeError
from valleyeye.core.geometry import buffer_aoi_m

PROCESSING_STEPS = (
    "Apply-Orbit-File",
    "ThermalNoiseRemoval",
    "Remove-GRD-Border-Noise",
    "Calibration sigma0 (linear power)",
    "Lee Sigma speckle filter",
    "Terrain-Correction with SRTM 1Sec HGT",
)


def _node(
    parent: ET.Element,
    node_id: str,
    operator: str,
    source_id: str | None,
    parameters: dict[str, str],
) -> ET.Element:
    node = ET.SubElement(parent, "node", id=node_id)
    ET.SubElement(node, "operator").text = operator
    sources = ET.SubElement(node, "sources")
    if source_id is not None:
        ET.SubElement(sources, "sourceProduct", refid=source_id)
    params = ET.SubElement(
        node, "parameters", attrib={"class": "com.bc.ceres.binding.dom.XppDomElement"}
    )
    for name, value in parameters.items():
        ET.SubElement(params, name).text = value
    return node


def build_snap_graph(
    source_product: Path,
    output_path: Path,
    aoi_wgs84: BaseGeometry,
    target_crs: CRS,
    pixel_spacing_m: float = 10.0,
    aoi_buffer_m: float = 500.0,
) -> str:
    if not source_product.is_file():
        raise FileNotFoundError(source_product)
    if aoi_wgs84.geom_type not in {"Polygon", "MultiPolygon"} or aoi_wgs84.is_empty:
        raise ValueError("SNAP subset AOI must be a non-empty Polygon or MultiPolygon")
    if not target_crs.is_projected or pixel_spacing_m <= 0 or aoi_buffer_m < 0:
        raise ValueError("SNAP target CRS must be projected and pixel spacing positive")

    root = ET.Element("graph", id="ValleyeyeGRD")
    ET.SubElement(root, "version").text = "1.0"
    _node(root, "Read", "Read", None, {"file": str(source_product.resolve())})
    _node(
        root,
        "Apply-Orbit-File",
        "Apply-Orbit-File",
        "Read",
        {
            "orbitType": "Sentinel Precise (Auto Download)",
            "polyDegree": "3",
            "continueOnFail": "false",
        },
    )
    _node(
        root,
        "Subset",
        "Subset",
        "Apply-Orbit-File",
        {"geoRegion": buffer_aoi_m(aoi_wgs84, aoi_buffer_m).wkt, "copyMetadata": "true"},
    )
    _node(
        root,
        "ThermalNoiseRemoval",
        "ThermalNoiseRemoval",
        "Subset",
        {"removeThermalNoise": "true", "reIntroduceThermalNoise": "false"},
    )
    _node(
        root,
        "BorderNoiseRemoval",
        "Remove-GRD-Border-Noise",
        "ThermalNoiseRemoval",
        {"borderLimit": "500", "trimThreshold": "50.0"},
    )
    _node(
        root,
        "Calibration",
        "Calibration",
        "BorderNoiseRemoval",
        {
            "outputImageInComplex": "false",
            "outputImageScaleInDb": "false",
            "selectedPolarisations": "VV,VH",
            "outputSigmaBand": "true",
            "outputGammaBand": "false",
            "outputBetaBand": "false",
        },
    )
    _node(
        root,
        "SpeckleFilter",
        "Speckle-Filter",
        "Calibration",
        {
            "filter": "Lee Sigma",
            "filterSizeX": "3",
            "filterSizeY": "3",
            "dampingFactor": "2",
            "estimateENL": "true",
            "enl": "1.0",
            "windowSize": "7x7",
            "targetWindowSizeStr": "3x3",
            "sigmaStr": "0.9",
            "anSize": "50",
        },
    )
    _node(
        root,
        "TerrainCorrection",
        "Terrain-Correction",
        "SpeckleFilter",
        {
            "demName": "SRTM 1Sec HGT",
            "demResamplingMethod": "BILINEAR_INTERPOLATION",
            "imgResamplingMethod": "BILINEAR_INTERPOLATION",
            "pixelSpacingInMeter": str(pixel_spacing_m),
            "mapProjection": target_crs.to_wkt(version="WKT1_GDAL"),
            "alignToStandardGrid": "true",
            "standardGridOriginX": "0.0",
            "standardGridOriginY": "0.0",
            "nodataValueAtSea": "true",
            "saveDEM": "true",
            "saveLayoverShadowMask": "true",
            "saveSelectedSourceBand": "true",
            "outputComplex": "false",
        },
    )
    _node(
        root,
        "Write",
        "Write",
        "TerrainCorrection",
        {"file": str(output_path.resolve()), "formatName": "GeoTIFF"},
    )
    return ET.tostring(root, encoding="unicode")


def run_snap_preprocessing(
    source_product: Path,
    output_path: Path,
    aoi_wgs84: BaseGeometry,
    target_crs: CRS,
    gpt_path: Path,
    pixel_spacing_m: float = 10.0,
    aoi_buffer_m: float = 500.0,
) -> tuple[str, ...]:
    graph = build_snap_graph(
        source_product, output_path, aoi_wgs84, target_crs, pixel_spacing_m, aoi_buffer_m
    )
    if not gpt_path.is_file():
        raise ValleyeyeError(
            ErrorCode.PREPROCESSING_FAILED,
            "SNAP GPT executable is unavailable; install SNAP and configure its gpt path.",
            stage="PREPROCESSING",
            details={"gpt_path": str(gpt_path)},
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix="valleyeye-snap-") as temp_dir:
            graph_path = Path(temp_dir) / "graph.xml"
            graph_path.write_text(graph, encoding="utf-8")
            subprocess.run(
                [str(gpt_path), str(graph_path)],
                check=True,
                capture_output=True,
                text=True,
            )
    except (OSError, subprocess.CalledProcessError) as exc:
        message = "SNAP Sentinel-1 GRD preprocessing failed."
        details: dict[str, object] = {}
        if isinstance(exc, subprocess.CalledProcessError):
            details["returncode"] = exc.returncode
            details["stderr_tail"] = (exc.stderr or "")[-2000:]
        raise ValleyeyeError(
            ErrorCode.PREPROCESSING_FAILED,
            message,
            stage="PREPROCESSING",
            retryable=True,
            details=details,
        ) from exc
    if not output_path.is_file():
        raise ValleyeyeError(
            ErrorCode.PREPROCESSING_FAILED,
            "SNAP completed without producing its declared output raster.",
            stage="PREPROCESSING",
        )
    return PROCESSING_STEPS
