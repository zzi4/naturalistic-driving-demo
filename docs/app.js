/* ============================================================
   驭研科技 · 宣传展示平台 · 主页交互
   ============================================================ */

const $ = id => document.getElementById(id);

const videoPlayer = $("videoPlayer");
const videoLoading = $("videoLoading");
const videoMeta = $("videoMeta");

const mapCanvas = $("mapCanvas");
const mapCtx = mapCanvas.getContext("2d");
const mapLoading = $("mapLoading");
const mapMeta = $("mapMeta");

const playBtn = $("playBtn");
const seekBar = $("seekBar");
const seekTime = $("seekTime");
const toggleBoxes = $("toggleBoxes");
const toggleTrails = $("toggleTrails");
const toggleLanes = $("toggleLanes");
const toggleMarkings = $("toggleMarkings");

const TRACK_PALETTE = [
    "#5fe6e0", "#7dc6ff", "#ffb05c", "#c89bff",
    "#7ddc94", "#ff7ab2", "#9aa9ff", "#ffd95c",
    "#5fc8a3", "#ff9a6b",
];

const state = {
    manifest: null,
    std: null,
    meta: null,
    raw: null,
    map: null,
    fps: 30,
    totalFrames: 0,
    currentFrame: 0,
    durationSec: 0,
    rafId: null,
    stdFrameIndex: new Map(),     // frame -> [{ trackId, idx }]
    mapView: { scale: 1, offsetX: 0, offsetY: 0 },
    videoSize: { w: 1920, h: 1080 },
};

/* ----------------------- 工具 ----------------------- */

function formatTime(sec) {
    if (!isFinite(sec)) return "00:00";
    const m = Math.floor(sec / 60);
    const s = Math.floor(sec % 60);
    return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

function trackColor(id) {
    const n = parseInt(id, 10) || 0;
    return TRACK_PALETTE[Math.abs(n) % TRACK_PALETTE.length];
}

async function fetchJSON(url) {
    const res = await fetch(url);
    if (!res.ok) throw new Error(`${url} ${res.status}`);
    return res.json();
}

function setHidden(el, hidden) {
    if (hidden) el.classList.add("hide");
    else el.classList.remove("hide");
}

/* ----------------------- 初始化 ----------------------- */

async function init() {
    try {
        state.manifest = await fetchJSON("data/manifest.json");
        if (state.manifest.video_info) {
            state.fps = state.manifest.video_info.fps || 30;
            state.totalFrames = state.manifest.video_info.total_frames || 0;
            state.videoSize.w = state.manifest.video_info.width;
            state.videoSize.h = state.manifest.video_info.height;
        }
        $("footCase").textContent = state.manifest.case || "--";

        // 视频源
        videoPlayer.src = state.manifest.video_url;

        // 并行加载 std / meta / map
        const [std, meta, map] = await Promise.all([
            fetchJSON("data/std.json"),
            fetchJSON("data/meta.json"),
            fetchJSON("data/map.json"),
        ]);
        state.std = std;
        state.meta = meta;
        state.map = map;

        buildStdFrameIndex();
        updateKPI();
        renderTrends();

        // 视频元数据就绪后初始化
        if (videoPlayer.readyState >= 1) {
            onVideoReady();
        } else {
            videoPlayer.addEventListener("loadedmetadata", onVideoReady, { once: true });
        }

        setHidden(mapLoading, true);
        alignMapToVideo();
        drawScene();

        videoMeta.textContent = `${state.videoSize.w}×${state.videoSize.h} · ${state.fps.toFixed(2)}fps`;
        mapMeta.textContent = `${state.map.lane_count} 车道 · ${state.std.track_count} 轨迹`;
    } catch (err) {
        console.error("init failed", err);
        videoLoading.textContent = "数据加载失败：" + err.message;
    }
}

function onVideoReady() {
    setHidden(videoLoading, true);
    state.durationSec = videoPlayer.duration || (state.totalFrames / state.fps);
    seekBar.max = Math.max(1, state.totalFrames - 1);
    syncCanvasToVideo();
    drawScene();
}

/* ----------------------- 数据索引 ----------------------- */

function buildStdFrameIndex() {
    const idx = new Map();
    const tracks = state.std.tracks || {};
    for (const [tid, track] of Object.entries(tracks)) {
        const frames = track.frames || [];
        for (let i = 0; i < frames.length; i++) {
            const f = frames[i];
            if (!idx.has(f)) idx.set(f, []);
            idx.get(f).push({ trackId: tid, idx: i });
        }
    }
    state.stdFrameIndex = idx;
}

/* ----------------------- KPI ----------------------- */

function updateKPI() {
    const trackCount = state.std.track_count || 0;
    const frameRange = state.std.frame_range || [0, 0];
    const frames = (frameRange[1] - frameRange[0] + 1) || state.totalFrames;
    const dur = frames / state.fps;
    const bounds = state.std.bounds || { min_x: 0, max_x: 0, min_y: 0, max_y: 0 };
    const w = Math.round(bounds.max_x - bounds.min_x);
    const h = Math.round(bounds.max_y - bounds.min_y);

    $("kpiTracks").textContent = trackCount;
    $("kpiFrames").textContent = frames.toLocaleString();
    $("kpiDuration").textContent = formatTime(dur);
    $("kpiArea").textContent = `${w}×${h} m`;
}

/* ----------------------- 直方图 ----------------------- */

function renderTrends() {
    const tracks = Object.values(state.meta.tracks || {});
    document.querySelectorAll("#trendGrid canvas[data-feature]").forEach(c => {
        const feature = c.dataset.feature;
        const values = tracks
            .map(t => t[feature])
            .filter(v => v !== null && v !== undefined && isFinite(v));
        drawHistogram(c, values);
    });
}

function drawHistogram(canvas, values) {
    const ctx = canvas.getContext("2d");
    const dpr = window.devicePixelRatio || 1;
    const cssW = canvas.clientWidth;
    const cssH = canvas.clientHeight;
    canvas.width = cssW * dpr;
    canvas.height = cssH * dpr;
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, cssW, cssH);

    if (!values.length) {
        ctx.fillStyle = "#5a6c8a";
        ctx.font = "12px sans-serif";
        ctx.fillText("暂无数据", 8, cssH / 2);
        return;
    }
    const min = Math.min(...values);
    const max = Math.max(...values);
    const range = max - min || 1;
    const bins = 24;
    const counts = new Array(bins).fill(0);
    for (const v of values) {
        let idx = Math.floor(((v - min) / range) * bins);
        if (idx >= bins) idx = bins - 1;
        if (idx < 0) idx = 0;
        counts[idx]++;
    }
    const maxCount = Math.max(...counts);
    const padX = 8;
    const padTop = 6;
    const padBottom = 18;
    const barAreaW = cssW - padX * 2;
    const barAreaH = cssH - padTop - padBottom;
    const barW = barAreaW / bins;

    counts.forEach((c, i) => {
        const h = (c / maxCount) * barAreaH;
        const x = padX + i * barW;
        const y = padTop + (barAreaH - h);
        const grad = ctx.createLinearGradient(0, y, 0, y + h);
        grad.addColorStop(0, "rgba(125, 198, 255, 0.95)");
        grad.addColorStop(1, "rgba(78, 160, 255, 0.4)");
        ctx.fillStyle = grad;
        ctx.fillRect(x + 1, y, Math.max(1, barW - 2), h);
    });

    ctx.fillStyle = "#6f86a8";
    ctx.font = "11px monospace";
    ctx.fillText(min.toFixed(1), padX, cssH - 4);
    const mTxt = max.toFixed(1);
    ctx.fillText(mTxt, cssW - padX - ctx.measureText(mTxt).width, cssH - 4);
}

/* ----------------------- Canvas 尺寸 ----------------------- */

function syncCanvasToVideo() {
    const dpr = window.devicePixelRatio || 1;
    const mr = mapCanvas.getBoundingClientRect();
    mapCanvas.width = Math.max(1, Math.round(mr.width * dpr));
    mapCanvas.height = Math.max(1, Math.round(mr.height * dpr));
    mapCtx.setTransform(dpr, 0, 0, dpr, 0, 0);
}

window.addEventListener("resize", () => {
    syncCanvasToVideo();
    alignMapToVideo();
    drawScene();
});

/* ----------------------- 坐标变换 ----------------------- */

function mapCanvasDims() {
    const rect = mapCanvas.getBoundingClientRect();
    return { w: rect.width, h: rect.height };
}

// 源 4K 视频帧每像素对应的真实距离 (米/像素)。
// 由数据采集流程标定：视频帧水平实际覆盖 sourceWidth * METERS_PER_SOURCE_PX 米。
const METERS_PER_SOURCE_PX = 0.075;

function alignMapToVideo() {
    // 让地图与视频共用坐标系：
    //  - 世界原点 (0, 0) 对齐到画布中心（即视频中心）
    //  - 使用与视频一致的 px/m 比例：源帧 0.075 m/px → 换算到画布显示像素
    // 这样对比滑块从左拖到右时，视频与轨迹视图在同一位置无缝衔接。
    const { w, h } = mapCanvasDims();
    if (!state.std?.bounds && !state.map?.bounds) return;

    // 视频在 16:9 容器内以 object-fit: contain 渲染，先得到视频画面在画布上的显示尺寸。
    const videoAR = state.videoSize.w / Math.max(1, state.videoSize.h);
    const containerAR = w / Math.max(1, h);
    let videoW;
    if (videoAR > containerAR) {
        videoW = w;
    } else {
        videoW = h * videoAR;
    }

    // 取源视频宽（manifest 里的 video_corners.image_width，通常 4K = 3840 px）。
    // 视频帧水平覆盖 sourceW * METERS_PER_SOURCE_PX 米；
    // 该范围被映射到画布上视频显示的 videoW 像素，得到 px/m。
    const sourceW = state.manifest?.video_corners?.image_width || state.videoSize.w;
    const worldWidthInFrame = Math.max(1e-3, sourceW * METERS_PER_SOURCE_PX);
    const scale = videoW / worldWidthInFrame;

    state.mapView.scale = scale;
    state.mapView.offsetX = w / 2;
    state.mapView.offsetY = h / 2;
}

function worldToMap(x, y) {
    const { scale, offsetX, offsetY } = state.mapView;
    return {
        x: x * scale + offsetX,
        y: offsetY - y * scale,
    };
}

/* ----------------------- 帧同步 ----------------------- */

function videoFrame() {
    if (!videoPlayer.duration) return 0;
    const frames = state.totalFrames || Math.round(videoPlayer.duration * state.fps);
    const ratio = videoPlayer.currentTime / videoPlayer.duration;
    return Math.min(frames - 1, Math.max(0, Math.round(ratio * (frames - 1))));
}

function drawScene() {
    const f = state.currentFrame = videoFrame();
    drawMap(f);
    if (videoPlayer.duration) {
        seekBar.value = f;
        seekTime.textContent = `${formatTime(videoPlayer.currentTime)} / ${formatTime(videoPlayer.duration)}`;
    }
}

function loop() {
    drawScene();
    state.rafId = requestAnimationFrame(loop);
}

videoPlayer.addEventListener("play", () => {
    if (state.rafId) cancelAnimationFrame(state.rafId);
    state.rafId = requestAnimationFrame(loop);
    playBtn.textContent = "❚❚";
});
videoPlayer.addEventListener("pause", () => {
    if (state.rafId) cancelAnimationFrame(state.rafId);
    state.rafId = null;
    drawScene();
    playBtn.textContent = "▶";
});
videoPlayer.addEventListener("seeked", drawScene);
videoPlayer.addEventListener("timeupdate", () => {
    if (videoPlayer.paused) drawScene();
});

playBtn.addEventListener("click", () => {
    if (videoPlayer.paused) videoPlayer.play();
    else videoPlayer.pause();
});

seekBar.addEventListener("input", () => {
    const f = parseInt(seekBar.value, 10);
    if (!isFinite(f) || !videoPlayer.duration) return;
    const total = state.totalFrames || Math.round(videoPlayer.duration * state.fps);
    videoPlayer.currentTime = (f / Math.max(1, total - 1)) * videoPlayer.duration;
});

document.querySelectorAll(".speed-btn").forEach(btn => {
    btn.addEventListener("click", () => {
        document.querySelectorAll(".speed-btn").forEach(b => b.classList.remove("active"));
        btn.classList.add("active");
        videoPlayer.playbackRate = parseFloat(btn.dataset.rate);
    });
});

[toggleBoxes, toggleTrails, toggleLanes, toggleMarkings].forEach(t => {
    t.addEventListener("change", drawScene);
});

/* ----------------------- 地图绘制 ----------------------- */

function drawMap(frame) {
    const { w, h } = mapCanvasDims();
    mapCtx.clearRect(0, 0, w, h);

    // 背景渐变
    const bg = mapCtx.createRadialGradient(w / 2, h / 2, 0, w / 2, h / 2, Math.max(w, h) / 1.4);
    bg.addColorStop(0, "#0a1830");
    bg.addColorStop(1, "#04070d");
    mapCtx.fillStyle = bg;
    mapCtx.fillRect(0, 0, w, h);
    drawMapGrid(w, h);

    if (toggleLanes.checked) drawLanes();
    if (toggleMarkings.checked) drawMarkings();

    // 轨迹尾迹（每帧出现的轨迹画过去 ~80 帧）
    if (toggleTrails.checked) {
        const stdEntries = state.stdFrameIndex.get(frame) || [];
        for (const { trackId, idx } of stdEntries) {
            drawStdTrail(trackId, idx);
        }
    }

    // 当前帧 bbox
    if (toggleBoxes.checked) {
        const stdEntries = state.stdFrameIndex.get(frame) || [];
        for (const { trackId, idx } of stdEntries) {
            drawStdBox(trackId, idx);
        }
    }

    drawFrameOverlay(frame, w, h);
}

function drawMapGrid(w, h) {
    mapCtx.strokeStyle = "rgba(140, 200, 255, 0.05)";
    mapCtx.lineWidth = 1;
    const step = 60;
    for (let x = 0; x < w; x += step) {
        mapCtx.beginPath();
        mapCtx.moveTo(x, 0);
        mapCtx.lineTo(x, h);
        mapCtx.stroke();
    }
    for (let y = 0; y < h; y += step) {
        mapCtx.beginPath();
        mapCtx.moveTo(0, y);
        mapCtx.lineTo(w, y);
        mapCtx.stroke();
    }
}

function drawLanes() {
    const lanes = state.map.lanes || [];
    for (const lane of lanes) {
        const polygon = lane.polygon || [];
        if (polygon.length < 3) continue;
        mapCtx.beginPath();
        polygon.forEach(([x, y], i) => {
            const p = worldToMap(x, y);
            if (i === 0) mapCtx.moveTo(p.x, p.y);
            else mapCtx.lineTo(p.x, p.y);
        });
        mapCtx.closePath();
        mapCtx.fillStyle = lane.type === "driving"
            ? "rgba(80, 130, 170, 0.18)"
            : "rgba(255, 200, 120, 0.10)";
        mapCtx.fill();
    }
}

function drawMarkings() {
    const markings = state.map.markings || [];
    for (const m of markings) {
        const pts = m.points || [];
        if (pts.length < 2) continue;
        mapCtx.beginPath();
        pts.forEach(([x, y], i) => {
            const p = worldToMap(x, y);
            if (i === 0) mapCtx.moveTo(p.x, p.y);
            else mapCtx.lineTo(p.x, p.y);
        });
        if (m.style === "center_yellow") {
            mapCtx.strokeStyle = "rgba(255, 200, 100, 0.5)";
            mapCtx.lineWidth = 1.6;
            mapCtx.setLineDash([]);
        } else if (m.style === "solid_white") {
            mapCtx.strokeStyle = "rgba(220, 230, 245, 0.55)";
            mapCtx.lineWidth = 1.4;
            mapCtx.setLineDash([]);
        } else {
            mapCtx.strokeStyle = "rgba(180, 210, 240, 0.32)";
            mapCtx.lineWidth = 1;
            mapCtx.setLineDash([6, 6]);
        }
        mapCtx.stroke();
        mapCtx.setLineDash([]);
    }
}

function drawStdTrail(trackId, idxAt) {
    const track = state.std.tracks[trackId];
    if (!track) return;
    const xs = track.x || [];
    const ys = track.y || [];
    if (!xs.length) return;
    const start = Math.max(0, idxAt - 60);
    const color = trackColor(trackId);
    mapCtx.strokeStyle = color;
    mapCtx.globalAlpha = 0.55;
    mapCtx.lineWidth = 1.4;
    mapCtx.beginPath();
    for (let i = start; i <= idxAt; i++) {
        const x = xs[i], y = ys[i];
        if (x === null || y === null) continue;
        const p = worldToMap(x, y);
        if (i === start) mapCtx.moveTo(p.x, p.y);
        else mapCtx.lineTo(p.x, p.y);
    }
    mapCtx.stroke();
    mapCtx.globalAlpha = 1;
}

function drawStdBox(trackId, idx) {
    const track = state.std.tracks[trackId];
    if (!track) return;
    const x = (track.x || [])[idx];
    const y = (track.y || [])[idx];
    if (x === null || y === null || x === undefined || y === undefined) return;
    const w = (track.width || [])[idx] || 4.5;
    const h = (track.height || [])[idx] || 1.9;
    const heading = (track.heading || [])[idx] || 0;
    // heading: 0°=+y(北), 顺时针. canvas y 向下, 因此旋转角 = heading - 90°
    const angle = ((heading - 90) * Math.PI) / 180;
    const scale = state.mapView.scale;
    const center = worldToMap(x, y);
    const color = trackColor(trackId);

    mapCtx.save();
    mapCtx.translate(center.x, center.y);
    mapCtx.rotate(angle);
    mapCtx.fillStyle = color;
    mapCtx.globalAlpha = 0.72;
    mapCtx.shadowColor = color;
    mapCtx.shadowBlur = 8;
    const ww = w * scale;
    const hh = h * scale;
    mapCtx.fillRect(-ww / 2, -hh / 2, ww, hh);
    mapCtx.shadowBlur = 0;
    mapCtx.globalAlpha = 1;

    // heading 三角
    mapCtx.fillStyle = "rgba(255,255,255,0.85)";
    mapCtx.beginPath();
    mapCtx.moveTo(ww / 2, 0);
    mapCtx.lineTo(ww / 2 - 4, -hh / 4);
    mapCtx.lineTo(ww / 2 - 4, hh / 4);
    mapCtx.closePath();
    mapCtx.fill();
    mapCtx.restore();
}

function drawFrameOverlay(frame, w, h) {
    const text = `Frame ${frame.toString().padStart(5, "0")} / ${state.totalFrames}`;
    mapCtx.font = "12px JetBrains Mono, ui-monospace, monospace";
    mapCtx.textBaseline = "top";
    const tw = mapCtx.measureText(text).width + 16;
    mapCtx.fillStyle = "rgba(0, 0, 0, 0.55)";
    mapCtx.fillRect(12, 12, tw, 22);
    mapCtx.fillStyle = "#cfe3ff";
    mapCtx.fillText(text, 20, 17);
}

/* ----------------------- 地图视图（锁定与视频对齐） -----------------------
 * 为了让对比滑块两侧空间一致，地图视图不再支持自由拖拽与缩放。
 * 任何画布的几何变化（resize 等）都会回到 alignMapToVideo() 给出的对齐参数。
 */

/* ----------------------- 对比滑块 ----------------------- */

const compareEl = $("compare");
const compareDivider = $("compareDivider");
const compareHandle = compareDivider ? compareDivider.querySelector(".compare-handle") : null;

function setSplit(percent) {
    const clamped = Math.min(95, Math.max(5, percent));
    compareEl.style.setProperty("--split-pos", clamped.toFixed(2) + "%");
}

if (compareHandle) {
    let dragPointerId = null;

    const onPointerMove = (e) => {
        if (e.pointerId !== dragPointerId) return;
        const rect = compareEl.getBoundingClientRect();
        const ratio = ((e.clientX - rect.left) / rect.width) * 100;
        setSplit(ratio);
    };
    const onPointerUp = (e) => {
        if (e.pointerId !== dragPointerId) return;
        dragPointerId = null;
        compareEl.classList.remove("dragging");
        compareHandle.releasePointerCapture?.(e.pointerId);
        window.removeEventListener("pointermove", onPointerMove);
        window.removeEventListener("pointerup", onPointerUp);
        window.removeEventListener("pointercancel", onPointerUp);
    };

    compareHandle.addEventListener("pointerdown", (e) => {
        // 让 handle 抢占事件，避免触发地图 canvas 的拖拽
        e.preventDefault();
        e.stopPropagation();
        dragPointerId = e.pointerId;
        compareEl.classList.add("dragging");
        compareHandle.setPointerCapture?.(e.pointerId);
        window.addEventListener("pointermove", onPointerMove);
        window.addEventListener("pointerup", onPointerUp);
        window.addEventListener("pointercancel", onPointerUp);
    });

    // 双击 handle 复位到中心
    compareHandle.addEventListener("dblclick", () => setSplit(50));

    // 键盘可达：左右方向键以 5% 步进
    compareHandle.addEventListener("keydown", (e) => {
        const cur = parseFloat(getComputedStyle(compareEl).getPropertyValue("--split-pos")) || 50;
        if (e.key === "ArrowLeft") { setSplit(cur - 5); e.preventDefault(); }
        else if (e.key === "ArrowRight") { setSplit(cur + 5); e.preventDefault(); }
    });
}

/* ----------------------- 滚动至舞台时自动播放 ----------------------- */

const showcaseEl = $("showcase");
let autoPlayTried = false;
const showcaseObserver = new IntersectionObserver((entries) => {
    for (const e of entries) {
        if (e.isIntersecting && !autoPlayTried && videoPlayer.readyState >= 2) {
            autoPlayTried = true;
            videoPlayer.play().catch(() => { /* autoplay blocked, ignore */ });
        }
    }
}, { threshold: 0.4 });
if (showcaseEl) showcaseObserver.observe(showcaseEl);

/* ----------------------- 启动 ----------------------- */

init();
