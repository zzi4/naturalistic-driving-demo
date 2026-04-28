"""把 demo 数据预生成为静态 JSON，供 GitHub Pages 直接消费。

输出到 docs/data/{manifest,std,meta,map}.json。
std.json 仅保留前端可视化需要的列（frames/x/y/width/height/heading），
meta.json 保留全部 META（体积本来就小），
map.json 是 OpenDRIVE 解析后的 lanes + markings。

用法：
    conda run -n nds python build.py
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from app import (
    DEMO_CASE,
    DEMO_DIR,
    _demo_paths,
    _video_info,
    load_opendrive_map,
    load_std_meta,
    load_std_trk,
    sanitize_for_json,
)


PROJECT_ROOT = Path(__file__).resolve().parent
DOCS_DATA = PROJECT_ROOT / "docs" / "data"
DOCS_VIDEO = PROJECT_ROOT / "docs" / "video.mp4"


VIZ_COLUMNS = ("x", "y", "width", "height", "heading")


def slim_std(std: dict) -> dict:
    tracks_in = std.get("tracks", {})
    tracks_out: dict = {}
    for tid, track in tracks_in.items():
        slim = {"frames": track.get("frames", [])}
        for col in VIZ_COLUMNS:
            if col in track:
                slim[col] = track[col]
        tracks_out[tid] = slim
    return {
        "type": std.get("type", "std"),
        "tracks": tracks_out,
        "frame_range": std.get("frame_range", [0, 0]),
        "track_count": std.get("track_count", len(tracks_out)),
        "bounds": std.get("bounds"),
    }


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fp:
        json.dump(data, fp, ensure_ascii=False, separators=(",", ":"))
    size_mb = path.stat().st_size / (1024 * 1024)
    print(f"  -> {path.relative_to(PROJECT_ROOT)}  {size_mb:.2f} MB")


def main() -> None:
    paths = _demo_paths()
    print(f"[build] case = {DEMO_CASE}")
    print(f"[build] demo dir = {DEMO_DIR}")

    # ---- manifest ----
    corners = {}
    if paths["corners"].exists():
        with open(paths["corners"], "r", encoding="utf-8") as fp:
            corners = json.load(fp)

    # 优先使用 docs/video.mp4（已重压缩、入仓库），否则回落到 demo 源视频
    if DOCS_VIDEO.exists():
        video_info = _video_info(str(DOCS_VIDEO))
        video_url = "video.mp4"
    elif paths["video"].exists():
        video_info = _video_info(str(paths["video"]))
        video_url = "video.mp4"
    else:
        video_info = None
        video_url = "video.mp4"

    manifest = sanitize_for_json({
        "case": DEMO_CASE,
        "video_url": video_url,
        "video_info": video_info,
        "video_corners": corners,
    })
    write_json(DOCS_DATA / "manifest.json", manifest)

    # ---- std (slimmed) ----
    print("[build] parsing std_trk.csv ...")
    std_full = load_std_trk(paths["std"])
    std_slim = slim_std(std_full)
    write_json(DOCS_DATA / "std.json", sanitize_for_json(std_slim))

    # ---- meta ----
    print("[build] parsing std_trk_meta.csv ...")
    meta = load_std_meta(paths["meta"])
    write_json(DOCS_DATA / "meta.json", sanitize_for_json(meta))

    # ---- map ----
    print("[build] parsing OpenDRIVE map ...")
    map_data = load_opendrive_map(paths["map"])
    write_json(DOCS_DATA / "map.json", sanitize_for_json(map_data))

    print("[build] done.")


if __name__ == "__main__":
    main()
