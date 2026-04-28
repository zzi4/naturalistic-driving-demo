"""驭研科技 · 自然驾驶数据集对外宣传展示平台。

后端只读接口：固化 demo 数据，复用 nds_code 的 OpenDRIVE 解析。
"""

import json
import logging
import math
import os
import sys
from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles


PROJECT_ROOT = Path(__file__).resolve().parent
DEMO_ROOT = PROJECT_ROOT / "demo"
DEMO_CASE = os.environ.get("DEMO_CASE", "DJI_20250802075110_0006_V")
DEMO_DIR = DEMO_ROOT / DEMO_CASE

# nds_code 目录用于 OpenDRIVE 解析
ADSAFETY_ROOT = Path(os.environ.get("ADSAFETY_ROOT", "/home/stu1/Projects/ADSafety"))
NDS_STEP4 = ADSAFETY_ROOT / "nds_code" / "utils" / "step4_mapMatching"


app = FastAPI(title="驭研科技 · 自然驾驶数据集展示平台")
log = logging.getLogger("datavisu_dr")


# ---------------------------------------------------------------------------
# 通用工具
# ---------------------------------------------------------------------------

def sanitize_for_json(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [sanitize_for_json(v) for v in obj]
    if isinstance(obj, tuple):
        return [sanitize_for_json(v) for v in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating, float)):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return float(obj)
    if pd.isna(obj):
        return None
    return obj


def _normalize_track_id(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except TypeError:
        pass
    try:
        return int(float(value))
    except (ValueError, TypeError):
        return None


def _file_cache_key(path: Path) -> Tuple[str, int, int]:
    stat = path.stat()
    return str(path), stat.st_mtime_ns, stat.st_size


# ---------------------------------------------------------------------------
# 标准化轨迹解析
# ---------------------------------------------------------------------------

def _load_grouped_tracks(
    path: Path,
    *,
    id_col: str,
    frame_col: str = "frame",
    class_col: Optional[str] = None,
    type_name: str,
) -> Dict[str, Any]:
    df = pd.read_csv(path)
    if id_col not in df.columns or frame_col not in df.columns:
        raise ValueError(f"{path} 缺少必要列: {id_col}, {frame_col}")

    df = df.copy()
    df[id_col] = pd.to_numeric(df[id_col], errors="coerce")
    df[frame_col] = pd.to_numeric(df[frame_col], errors="coerce")
    df = df.dropna(subset=[id_col, frame_col])
    df[id_col] = df[id_col].astype(int)
    df[frame_col] = df[frame_col].astype(int)

    ignore = {id_col, frame_col}
    if class_col:
        ignore.add(class_col)
    numeric_cols = [
        col
        for col in df.columns
        if col not in ignore and pd.api.types.is_numeric_dtype(df[col])
    ]

    tracks: Dict[str, Any] = {}
    for track_id, group in df.groupby(id_col):
        group = group.sort_values(frame_col).reset_index(drop=True)
        payload: Dict[str, Any] = {"frames": group[frame_col].astype(int).tolist()}
        if class_col and class_col in group.columns:
            cls_mode = group[class_col].mode(dropna=True)
            payload["class"] = int(cls_mode.iloc[0]) if len(cls_mode) > 0 else 0
        for col in numeric_cols:
            vals: List[Optional[float]] = []
            for value in group[col].tolist():
                if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
                    vals.append(None)
                else:
                    vals.append(round(float(value), 4))
            payload[col] = vals
        tracks[str(int(track_id))] = payload

    all_frames = df[frame_col].tolist()
    bounds = None
    if "x" in df.columns and "y" in df.columns:
        xs = pd.to_numeric(df["x"], errors="coerce")
        ys = pd.to_numeric(df["y"], errors="coerce")
        valid = xs.notna() & ys.notna()
        if valid.any():
            bounds = {
                "min_x": round(float(xs[valid].min()), 4),
                "max_x": round(float(xs[valid].max()), 4),
                "min_y": round(float(ys[valid].min()), 4),
                "max_y": round(float(ys[valid].max()), 4),
            }

    return sanitize_for_json(
        {
            "type": type_name,
            "tracks": tracks,
            "columns": numeric_cols,
            "frame_range": [int(min(all_frames)), int(max(all_frames))] if all_frames else [0, 0],
            "track_count": len(tracks),
            "row_count": int(len(df)),
            "bounds": bounds,
        }
    )


@lru_cache(maxsize=2)
def _load_std_trk_cached(key: Tuple[str, int, int]) -> Dict[str, Any]:
    return _load_grouped_tracks(Path(key[0]), id_col="id", type_name="std")


def load_std_trk(path: Path) -> Dict[str, Any]:
    data = _load_std_trk_cached(_file_cache_key(path))
    feature_groups = {
        "运动": ["heading", "lonVelocity", "latVelocity", "lonAcceleration", "latAcceleration"],
        "交互": ["precedingId", "followingId", "leftPrecedingId", "leftAlongsideId", "leftFollowingId",
                "rightPrecedingId", "rightAlongsideId", "rightFollowingId"],
        "安全": ["dhw", "thw", "ttc"],
        "定位": ["x", "y", "laneId", "width", "height"],
    }
    data = dict(data)
    data["feature_groups"] = {
        group: [feat for feat in feats if feat in data["columns"]]
        for group, feats in feature_groups.items()
    }
    return data


@lru_cache(maxsize=2)
def _load_std_meta_cached(key: Tuple[str, int, int]) -> Dict[str, Any]:
    df = pd.read_csv(key[0])
    if "id" not in df.columns:
        raise ValueError(f"{key[0]} 缺少 id 列")
    records: Dict[str, Any] = {}
    for _, row in df.iterrows():
        track_id = _normalize_track_id(row.get("id"))
        if track_id is None:
            continue
        item = {}
        for col in df.columns:
            value = row[col]
            if col == "id":
                item[col] = int(track_id)
            else:
                item[col] = sanitize_for_json(value)
        records[str(track_id)] = item
    return sanitize_for_json(
        {
            "tracks": records,
            "columns": df.columns.tolist(),
            "track_count": len(records),
        }
    )


def load_std_meta(path: Path) -> Dict[str, Any]:
    return _load_std_meta_cached(_file_cache_key(path))


# ---------------------------------------------------------------------------
# 原始轨迹解析（带 kept 标记）
# ---------------------------------------------------------------------------

@lru_cache(maxsize=2)
def _load_raw_trk_cached(raw_key: Tuple[str, int, int], std_key: Optional[Tuple[str, int, int]]) -> Dict[str, Any]:
    raw_path = raw_key[0]
    std_path = std_key[0] if std_key else None
    with open(raw_path, "r", encoding="utf-8") as fp:
        raw_frames = json.load(fp)

    grouped: Dict[int, List[Dict[str, Any]]] = {}
    class_votes: Dict[int, List[int]] = {}
    name_votes: Dict[int, List[str]] = {}
    observed_frames: List[int] = []
    max_x = 0.0
    max_y = 0.0

    for frame_item in raw_frames or []:
        frame_raw = frame_item.get("frame")
        try:
            frame = int(frame_raw)
        except (TypeError, ValueError):
            continue

        observed_frames.append(frame)
        for box_item in frame_item.get("box_info") or []:
            track_id = _normalize_track_id(box_item.get("track_id"))
            if track_id is None:
                continue

            box = box_item.get("box") or {}
            coords: List[float] = []
            valid = True
            for key in ("x1", "y1", "x2", "y2", "x3", "y3", "x4", "y4"):
                try:
                    value = float(box[key])
                except (KeyError, TypeError, ValueError):
                    valid = False
                    break
                if math.isnan(value) or math.isinf(value):
                    valid = False
                    break
                coords.append(round(value, 2))

            if not valid:
                continue

            confidence = box_item.get("confidence")
            try:
                conf_value: Optional[float] = round(float(confidence), 4)
                if math.isnan(conf_value) or math.isinf(conf_value):
                    conf_value = None
            except (TypeError, ValueError):
                conf_value = None

            grouped.setdefault(track_id, []).append({"frame": frame, "box": coords, "confidence": conf_value})

            cls_id = _normalize_track_id(box_item.get("class"))
            if cls_id is not None:
                class_votes.setdefault(track_id, []).append(cls_id)
            name = str(box_item.get("name") or "").strip()
            if name:
                name_votes.setdefault(track_id, []).append(name)

            xs = coords[0::2]
            ys = coords[1::2]
            if xs:
                max_x = max(max_x, *xs)
            if ys:
                max_y = max(max_y, *ys)

    kept_ids: set = set()
    if std_path:
        std_ids = pd.read_csv(std_path, usecols=["id"])
        for raw_id in std_ids["id"].tolist():
            tid = _normalize_track_id(raw_id)
            if tid is not None:
                kept_ids.add(tid)

    tracks: Dict[str, Any] = {}
    kept_track_ids: List[int] = []
    filtered_track_ids: List[int] = []

    for track_id in sorted(grouped):
        entries = sorted(grouped[track_id], key=lambda x: x["frame"])
        kept = track_id in kept_ids if std_path else False
        (kept_track_ids if kept else filtered_track_ids).append(track_id)
        cls_mode = Counter(class_votes.get(track_id) or [0]).most_common(1)[0][0]
        name_mode = Counter(name_votes.get(track_id) or [""]).most_common(1)[0][0]
        tracks[str(track_id)] = {
            "frames": [int(item["frame"]) for item in entries],
            "boxes": [item["box"] for item in entries],
            "confidence": [item["confidence"] for item in entries],
            "class": int(cls_mode),
            "name": str(name_mode),
            "kept": kept,
        }

    frame_range = [int(min(observed_frames)), int(max(observed_frames))] if observed_frames else [0, 0]
    return sanitize_for_json(
        {
            "type": "raw",
            "tracks": tracks,
            "frame_range": frame_range,
            "track_count": len(tracks),
            "kept_ids": kept_track_ids,
            "filtered_ids": filtered_track_ids,
            "kept_count": len(kept_track_ids),
            "filtered_count": len(filtered_track_ids),
            "image_size": {"width": int(math.ceil(max(0.0, max_x))), "height": int(math.ceil(max(0.0, max_y)))},
        }
    )


def load_raw_trk(raw_path: Path, std_path: Optional[Path] = None) -> Dict[str, Any]:
    raw_key = _file_cache_key(raw_path)
    std_key = _file_cache_key(std_path) if std_path else None
    return _load_raw_trk_cached(raw_key, std_key)


# ---------------------------------------------------------------------------
# OpenDRIVE 解析（依赖 nds_code）
# ---------------------------------------------------------------------------

def _ensure_opendrive_imports():
    if str(ADSAFETY_ROOT) not in sys.path:
        sys.path.insert(0, str(ADSAFETY_ROOT))
    if str(NDS_STEP4) not in sys.path:
        sys.path.insert(0, str(NDS_STEP4))
    from mapMatching_tools import get_all_lanes, load_xodr_and_parse  # type: ignore
    return load_xodr_and_parse, get_all_lanes


def _sample_points(points: List[Tuple[float, float]], stride: int = 5) -> List[List[float]]:
    normalized = [[round(float(x), 4), round(float(y), 4)] for x, y in points]
    if len(normalized) <= stride + 2:
        return normalized
    sampled = normalized[::stride]
    if sampled[-1] != normalized[-1]:
        sampled.append(normalized[-1])
    return sampled


@lru_cache(maxsize=2)
def _load_xodr_cached(path: str) -> Dict[str, Any]:
    load_xodr_and_parse, get_all_lanes = _ensure_opendrive_imports()
    road_network = load_xodr_and_parse(file=path)
    all_lanes = get_all_lanes(road_network, step=0.25)

    lanes: List[Dict[str, Any]] = []
    markings: List[Dict[str, Any]] = []
    all_xs: List[float] = []
    all_ys: List[float] = []

    def _extend(pts: List[Tuple[float, float]]) -> None:
        for x, y in pts:
            all_xs.append(float(x))
            all_ys.append(float(y))

    for (road_id, section_id), section_data in all_lanes.items():
        types = section_data.get("types", {})
        ref = section_data.get("reference_points", {})
        center = ref.get("position_center_lane") or []
        if center:
            markings.append({
                "roadId": int(road_id),
                "sectionId": int(section_id),
                "kind": "centerLine",
                "style": "center_yellow",
                "points": _sample_points(center, stride=2),
            })
            _extend(center)

        for line_key, side in (("lane_line_left", "left"), ("lane_line_right", "right")):
            for lane_pair, line_points in section_data.get(line_key, {}).items():
                if not line_points:
                    continue
                inner_id, outer_id = lane_pair
                is_edge = str(outer_id).upper() == "NAN"
                markings.append({
                    "roadId": int(road_id),
                    "sectionId": int(section_id),
                    "kind": "roadEdge" if is_edge else "laneDivider",
                    "side": side,
                    "innerLaneId": int(inner_id),
                    "outerLaneId": None if is_edge else int(outer_id),
                    "style": "solid_white" if is_edge else "dashed_white",
                    "points": _sample_points(line_points, stride=2),
                })
                _extend(line_points)

        for side, area_key in (("left", "left_lanes_area"), ("right", "right_lanes_area")):
            for lane_id, area in section_data.get(area_key, {}).items():
                inner = area.get("inner") or []
                outer = area.get("outer") or []
                if not inner or not outer:
                    continue
                polygon = inner + outer[::-1]
                centerline = [
                    ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)
                    for a, b in zip(inner, outer)
                ]
                lanes.append({
                    "roadId": int(road_id),
                    "sectionId": int(section_id),
                    "laneId": int(lane_id),
                    "side": side,
                    "type": types.get(lane_id, "unknown"),
                    "polygon": _sample_points(polygon, stride=4),
                    "centerline": _sample_points(centerline, stride=6),
                })
                _extend(polygon)

    bounds = None
    if all_xs and all_ys:
        bounds = {
            "min_x": round(float(min(all_xs)), 4),
            "max_x": round(float(max(all_xs)), 4),
            "min_y": round(float(min(all_ys)), 4),
            "max_y": round(float(max(all_ys)), 4),
        }

    return sanitize_for_json({
        "path": path,
        "lane_count": len(lanes),
        "lanes": lanes,
        "markings": markings,
        "bounds": bounds,
    })


def load_opendrive_map(path: Path) -> Dict[str, Any]:
    return _load_xodr_cached(str(path))


# ---------------------------------------------------------------------------
# 视频信息
# ---------------------------------------------------------------------------

@lru_cache(maxsize=2)
def _video_info(path: str) -> Dict[str, Any]:
    try:
        import cv2
        cap = cv2.VideoCapture(path)
        info = {
            "width": int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
            "height": int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
            "fps": round(cap.get(cv2.CAP_PROP_FPS), 3),
            "total_frames": int(cap.get(cv2.CAP_PROP_FRAME_COUNT)),
        }
        cap.release()
        return info
    except Exception:
        return {"width": 1920, "height": 1080, "fps": 30.0, "total_frames": 0}


# ---------------------------------------------------------------------------
# 资产路径
# ---------------------------------------------------------------------------

def _demo_paths() -> Dict[str, Path]:
    return {
        "video": DEMO_DIR / "video.mp4",
        "std": DEMO_DIR / "std_trk.csv",
        "meta": DEMO_DIR / "std_trk_meta.csv",
        "raw": DEMO_DIR / "raw_trk.json",
        "map": DEMO_DIR / "map.xodr",
        "corners": DEMO_DIR / "video_corners.json",
    }


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

@app.get("/api/manifest")
async def api_manifest():
    paths = _demo_paths()
    corners = {}
    if paths["corners"].exists():
        with open(paths["corners"], "r", encoding="utf-8") as fp:
            corners = json.load(fp)

    video_info = _video_info(str(paths["video"])) if paths["video"].exists() else None

    return JSONResponse(content=sanitize_for_json({
        "case": DEMO_CASE,
        "video_url": "/api/video",
        "video_info": video_info,
        "video_corners": corners,
        "endpoints": {
            "map": "/api/map",
            "std": "/api/std",
            "meta": "/api/meta",
            "raw": "/api/raw",
            "video": "/api/video",
        },
        "available": {key: path.exists() for key, path in paths.items()},
    }))


@app.get("/api/std")
async def api_std():
    path = _demo_paths()["std"]
    if not path.exists():
        raise HTTPException(status_code=404, detail="std_trk.csv 缺失")
    return JSONResponse(content=sanitize_for_json(load_std_trk(path)))


@app.get("/api/meta")
async def api_meta():
    path = _demo_paths()["meta"]
    if not path.exists():
        raise HTTPException(status_code=404, detail="std_trk_meta.csv 缺失")
    return JSONResponse(content=sanitize_for_json(load_std_meta(path)))


@app.get("/api/raw")
async def api_raw(filter: str = Query("kept", description="kept | filtered | all")):
    paths = _demo_paths()
    if not paths["raw"].exists():
        raise HTTPException(status_code=404, detail="raw_trk.json 缺失")
    std_path = paths["std"] if paths["std"].exists() else None
    data = load_raw_trk(paths["raw"], std_path)

    if filter in ("kept", "filtered"):
        keep_ids = set(data["kept_ids"]) if filter == "kept" else set(data["filtered_ids"])
        slim_tracks = {tid: t for tid, t in data["tracks"].items() if int(tid) in keep_ids}
        data = dict(data)
        data["tracks"] = slim_tracks
        data["filter"] = filter
    else:
        data = dict(data)
        data["filter"] = "all"

    return JSONResponse(content=sanitize_for_json(data))


@app.get("/api/map")
async def api_map():
    path = _demo_paths()["map"]
    if not path.exists():
        raise HTTPException(status_code=404, detail="map.xodr 缺失")
    try:
        data = load_opendrive_map(path)
        return JSONResponse(content=sanitize_for_json(data))
    except Exception as exc:
        log.exception("load xodr failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.head("/api/video")
@app.get("/api/video")
async def api_video(request: Request):
    path = _demo_paths()["video"]
    if not path.exists():
        raise HTTPException(status_code=404, detail="video.mp4 缺失（可能仍在编码）")

    stat_result = path.stat()
    file_size = stat_result.st_size
    etag = f'"{stat_result.st_mtime_ns}-{file_size}"'
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Type": "video/mp4",
        "Cache-Control": "public, max-age=600",
        "ETag": etag,
    }

    if_none_match = request.headers.get("if-none-match")
    if if_none_match:
        candidates = [c.strip() for c in if_none_match.split(",")]
        if "*" in candidates or etag in candidates:
            return Response(status_code=304, headers=headers)

    return FileResponse(path, headers=headers, media_type="video/mp4", stat_result=stat_result)


# ---------------------------------------------------------------------------
# 静态资源 & 页面
# ---------------------------------------------------------------------------

STATIC_DIR = PROJECT_ROOT / "docs"
LOGO_DIR = PROJECT_ROOT / "logo"

if LOGO_DIR.exists():
    app.mount("/logo", StaticFiles(directory=str(LOGO_DIR)), name="logo")

if STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
