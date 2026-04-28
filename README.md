# 驭研科技 · 自然驾驶数据集展示平台

对外宣传站点：以无人机视角同步展示原始视频与标准化轨迹（OpenDRIVE 高精地图坐标系），覆盖 KPI 概览、技术链路说明与特征分布。

构建产物全部静态化到 `docs/`，可直接由 GitHub Pages 托管，也支持任意静态服务器在线预览。

## 目录结构

```
DataVisu_DR/
├── app.py                # 数据解析（CSV / OpenDRIVE / 视频元信息）+ 可选 FastAPI 接口
├── build.py              # 一次性预生成 docs/data/*.json
├── requirements.txt
├── logo/                 # 驭研科技 LOGO（构建时拷贝到 docs/logo）
├── demo/                 # 原始大文件（gitignore，不入仓库）
│   └── DJI_20250802075110_0006_V/
│       ├── video.mp4              # 原始 4K 稳定后视频（本地源）
│       ├── std_trk.csv            # 标准化轨迹（highD 风格）
│       ├── std_trk_meta.csv
│       ├── raw_trk.json           # 仅本地参考，前端不再使用
│       ├── map.xodr
│       └── video_corners.json
└── docs/                 # GitHub Pages 站点根目录（直接进仓库）
    ├── index.html        # 首页：Hero + KPI + 视频/地图同步舞台 + 特征分布
    ├── tech.html         # 技术说明：6 步 pipeline + 关键模块
    ├── style.css
    ├── app.js
    ├── logo/             # 站点引用的 LOGO
    ├── video.mp4         # 重压缩后的 1080p 演示视频（~65MB）
    └── data/
        ├── manifest.json # 视频元信息 + 角点
        ├── std.json      # 精简后的标准化轨迹（仅 frames/x/y/width/height/heading）
        ├── meta.json     # 每条轨迹的 META
        └── map.json      # OpenDRIVE 解析后的 lanes + markings
```

## 构建流程

```bash
# 1. 重压缩视频（一次性，源文件在 demo/.../video.mp4，输出到 docs/video.mp4）
ffmpeg -i demo/DJI_20250802075110_0006_V/video.mp4 \
    -c:v libx264 -preset medium -crf 27 -vf scale=1920:1080 \
    -c:a aac -b:a 96k -movflags +faststart docs/video.mp4

# 2. 生成静态 JSON
conda run -n nds python build.py
```

## 本地预览

```bash
python -m http.server -d docs 8014
# 访问 http://127.0.0.1:8014/
```

## GitHub Pages 部署

1. 在 GitHub 创建仓库（如 `DataVisu_DR`）。
2. 本地推送：
   ```bash
   git init
   git add .
   git commit -m "init"
   git branch -M main
   git remote add origin git@github.com:<user>/DataVisu_DR.git
   git push -u origin main
   ```
3. 仓库 Settings → Pages → Source = `Deploy from a branch`，Branch = `main`，Folder = `/docs`。
4. 等待几秒后 Pages 给出公网地址。

## 设计要点

- **风格**：宣传展示导向（深色主题 + 蓝色品牌色 + 渐变发光），不暴露功能型操作面板
- **首页 Hero**：logo + 标语 + KPI（轨迹数 / 帧数 / 时长 / 覆盖范围）
- **主舞台**：左视频，右地图（标准化轨迹尾迹 + 当前帧 bbox + heading 三角，按帧同步）
- **技术页**：6 步 pipeline（稳像 → 检测跟踪 → 后处理 → 地图匹配 → 标准化输出 → 场景挖掘）+ 关键模块卡片 + 精度验证占位
- **特征趋势**：基于 META 的 meanVelocity / numFrames / traveledDistance / minTTC 直方图

## 数据替换

切换 demo 案例：

1. 在 `demo/<case>/` 下放置同样的 6 个文件
2. 设置 `DEMO_CASE=<case>` 后重新执行视频压缩 + `python build.py`

## 依赖

- OpenDRIVE 解析依赖 `nds_code/utils/step4_mapMatching/mapMatching_tools`，路径由 `ADSAFETY_ROOT` 环境变量配置（默认 `/home/stu1/Projects/ADSafety`）
- Python 环境固定 `conda nds`

## 待补充

- 精度验证报告（人工标注 / RTK ground truth 对比）
- 多 demo case 切换入口
