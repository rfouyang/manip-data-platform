const vizDataset = document.querySelector("#viz-dataset");
const vizEpisode = document.querySelector("#viz-episode");
const vizCamera = document.querySelector("#viz-camera");
const vizMessage = document.querySelector("#viz-message");
const vizViewer = document.querySelector("#viz-viewer");
const vizSlider = document.querySelector("#viz-slider");
const vizIndex = document.querySelector("#viz-index");
const vizLength = document.querySelector("#viz-length");
const vizFrameInfo = document.querySelector("#viz-frame-info");
const vizColor = document.querySelector("#viz-color");
const vizDepth = document.querySelector("#viz-depth");
const vizDepthMin = document.querySelector("#viz-depth-min");
const vizDepthMax = document.querySelector("#viz-depth-max");
const vizDepthValue = document.querySelector("#viz-depth-value");
const vizColorRes = document.querySelector("#viz-color-res");
const vizDepthRes = document.querySelector("#viz-depth-res");
const vizEpisodeInfo = document.querySelector("#viz-episode-info");
const vizPlay = document.querySelector("#viz-play");
const vizPlayIcon = document.querySelector("#viz-play-icon");
const vizPlayLabel = document.querySelector("#viz-play-label");
const vizPlayMode = document.querySelector("#viz-play-mode");
const vizPlayFps = document.querySelector("#viz-play-fps");
const vizPlayLoop = document.querySelector("#viz-play-loop");
const vizPlayDepth = document.querySelector("#viz-play-depth");
const vizPlayStatus = document.querySelector("#viz-play-status");
const vizField = document.querySelector("#viz-field");
const vizFieldPair = document.querySelector("#viz-field-pair");
const vizCurveStatus = document.querySelector("#viz-curve-status");
const vizChart = document.querySelector("#viz-chart");
const vizFrameValues = document.querySelector("#viz-frame-values");
const vizSubtasks = document.querySelector("#viz-subtasks");
const vizSubtaskColors = ["bg-blue-200 dark:bg-blue-900", "bg-teal-200 dark:bg-teal-900", "bg-amber-200 dark:bg-amber-900", "bg-purple-200 dark:bg-purple-900", "bg-rose-200 dark:bg-rose-900"];

// 当前 episode 的帧索引与最近一次加载的深度；depthRequest 用于丢弃过期的响应。
const vizState = { frameIndex: null, row: 0, depth: null, depthRequest: 0 };
// 播放状态：playing 控制循环，measured 用于显示实际帧率。
const playState = { playing: false, rendered: 0, measuredAt: 0 };
// 曲线：chart 为 ECharts 实例，valuesRequest 用于丢弃过期的当前帧数值响应。
const curveState = { chart: null, valuesRequest: 0 };

const CURVE_COLORS = ["#2563eb", "#059669", "#d97706", "#dc2626", "#7c3aed", "#0891b2", "#db2777", "#65a30d"];

function vizAlert(text) {
  vizMessage.innerHTML = text ? alertHtml(text, "error") : "";
}

// Turbo 伪彩色的多项式近似，预先计算 256 级查找表。
const turboTable = Array.from({ length: 256 }, (_, i) => {
  const t = i / 255;
  const r = 0.13572138 + t * (4.6153926 + t * (-42.66032258 + t * (132.13108234 + t * (-152.94239396 + t * 59.28637943))));
  const g = 0.09140261 + t * (2.19418839 + t * (4.84296658 + t * (-14.18503333 + t * (4.27729857 + t * 2.82956604))));
  const b = 0.1066733 + t * (12.64194608 + t * (-60.58204836 + t * (110.36276771 + t * (-89.90310912 + t * 27.34824973))));
  const color = [r, g, b].map((value) => Math.round(Math.min(Math.max(value, 0), 1) * 255));
  return color;
});

function vizQuery(extra = {}) {
  const params = new URLSearchParams({ dataset_id: vizDataset.value, episode_id: vizEpisode.value, ...extra });
  const query = params.toString();
  return query;
}

function cameraInfo() {
  const info = vizState.frameIndex.cameras[vizCamera.value];
  return info;
}

// 返回包含该行的相机图像序号（0 起）；image_starts 为图像变化的起始行，升序。
function imageNumberAt(row) {
  const starts = cameraInfo().image_starts;
  let low = 0;
  let high = starts.length - 1;
  while (low < high) {
    const middle = Math.ceil((low + high) / 2);
    if (starts[middle] <= row) low = middle;
    else high = middle - 1;
  }
  return low;
}

// 当前行所在的子任务段（起始帧不大于当前行的最后一段），没有时为 -1。
function vizSubtaskAt(row) {
  let position = -1;
  vizState.frameIndex.subtasks.forEach((segment, index) => { if (segment.start <= row) position = index; });
  return position;
}

function renderVizSubtasks() {
  const { subtasks, length } = vizState.frameIndex;
  vizSubtasks.hidden = !subtasks.length;
  vizSubtasks.innerHTML = subtasks.map((segment, index) => {
    const end = index + 1 < subtasks.length ? subtasks[index + 1].start : length;
    const html = `<button type="button" data-start="${segment.start}" title="${escapeHtml(segment.text)}" aria-label="跳到子任务：${escapeHtml(segment.text)}" style="width:${((end - segment.start) / length) * 100}%" class="${vizSubtaskColors[index % vizSubtaskColors.length]} truncate px-1 text-start text-gray-800 ring-blue-600 ring-inset focus-visible:ring-2 focus-visible:outline-hidden dark:text-neutral-100">${escapeHtml(segment.text)}</button>`;
    return html;
  }).join("");
}

function renderFrameInfo(row) {
  const frameIndex = vizState.frameIndex;
  const starts = cameraInfo().image_starts;
  const imageNumber = imageNumberAt(row);
  const imageStart = starts[imageNumber];
  const imageEnd = (starts[imageNumber + 1] ?? frameIndex.length) - 1;
  // H5 的相机图像约每 6 行更新一次，显示当前图像序号；LeRobot 每帧一张图像（相邻帧解码后可能相同），不显示。
  const items = [`行 <b>${row}</b> / ${frameIndex.length - 1}`];
  if (frameIndex.format === "h5") {
    items.push(`图像 <b>${imageNumber + 1}</b> / ${starts.length}（行 ${imageStart}–${imageEnd}${row === imageStart ? "，新图像" : ""}）`);
  }
  items.push(`时间戳 ${Number(frameIndex.timestamps[row].toFixed(6))} ${escapeHtml(frameIndex.timestamp_unit)}`);
  if (frameIndex.is_intervene) items.push(`干预 ${frameIndex.is_intervene[row] ? "是" : "否"}`);
  const position = vizSubtaskAt(row);
  if (frameIndex.subtasks.length) items.push(`子任务 <b>${escapeHtml(position >= 0 ? frameIndex.subtasks[position].text : "（无）")}</b>`);
  vizSubtasks.querySelectorAll("[data-start]").forEach((element, index) => element.classList.toggle("ring-2", index === position));
  vizFrameInfo.innerHTML = items.map((item) => `<span>${item}</span>`).join("");
}

function drawDepth() {
  if (!vizState.depth) return;
  const [height, width] = cameraInfo().depth_resolution;
  const low = Number(vizDepthMin.value) || 0;
  const high = Math.max(Number(vizDepthMax.value) || 1, low + 1);
  const context = vizDepth.getContext("2d");
  const image = context.createImageData(width, height);
  for (let i = 0; i < vizState.depth.length; i += 1) {
    const value = vizState.depth[i];
    const offset = i * 4;
    image.data[offset + 3] = 255;
    if (value === 0) continue;
    const level = Math.round((Math.min(Math.max(value, low), high) - low) / (high - low) * 255);
    const [r, g, b] = turboTable[level];
    image.data[offset] = r;
    image.data[offset + 1] = g;
    image.data[offset + 2] = b;
  }
  context.putImageData(image, 0, 0);
}

// 取当前帧非零深度的 2%–98% 分位作为显示范围（抽样计算），切换帧时保持不变以免颜色跳动。
function autoDepthRange() {
  if (!vizState.depth) return;
  const samples = [];
  for (let i = 0; i < vizState.depth.length; i += 16) {
    if (vizState.depth[i] > 0) samples.push(vizState.depth[i]);
  }
  samples.sort((a, b) => a - b);
  if (samples.length === 0) return;
  vizDepthMin.value = samples[Math.floor(samples.length * 0.02)];
  vizDepthMax.value = samples[Math.floor(samples.length * 0.98)];
  drawDepth();
}

async function loadDepth(row) {
  const request = ++vizState.depthRequest;
  if (!cameraInfo().depth_resolution) {
    vizState.depth = null;
    vizDepth.getContext("2d").clearRect(0, 0, vizDepth.width, vizDepth.height);
    vizDepthValue.textContent = "该相机没有深度。";
    return;
  }
  const response = await fetch(`/api/visualization/depth?${vizQuery({ camera: vizCamera.value, index: row })}`);
  if (!response.ok) throw new Error(`深度读取失败：${response.status}`);
  const buffer = await response.arrayBuffer();
  if (request !== vizState.depthRequest) return;
  vizState.depth = new Uint16Array(buffer);
  if (!vizDepthMax.value) autoDepthRange();
  else drawDepth();
}

// withDepth=false 时只更新 RGB，用于高帧率播放。
async function showRow(row, withDepth = true) {
  const frameIndex = vizState.frameIndex;
  const clamped = Math.min(Math.max(row, 0), frameIndex.length - 1);
  vizState.row = clamped;
  vizSlider.value = clamped;
  vizIndex.value = clamped;
  renderFrameInfo(clamped);
  vizColor.src = `/api/visualization/color?${vizQuery({ camera: vizCamera.value, index: clamped })}`;
  updateCursor();
  const pending = [vizColor.decode().catch(() => undefined)];
  if (withDepth) pending.push(loadDepth(clamped).catch((error) => vizAlert(error.message)));
  // 播放时不逐帧请求数值表，停止后再补一次。
  if (!playState.playing) pending.push(loadFrameValues(clamped).catch(() => undefined));
  await Promise.all(pending);
}

function stepRow(kind, direction) {
  stopPlayback();
  if (kind === "row") {
    showRow(vizState.row + direction);
    return;
  }
  const starts = cameraInfo().image_starts;
  const imageNumber = imageNumberAt(vizState.row);
  const onImageStart = starts[imageNumber] === vizState.row;
  // 向前跳时，若不在图像起始行，先回到当前图像的起始行。
  const target = direction > 0 ? imageNumber + 1 : onImageStart ? imageNumber - 1 : imageNumber;
  const clampedTarget = Math.min(Math.max(target, 0), starts.length - 1);
  showRow(starts[clampedTarget]);
}

function nextRow(direction) {
  const starts = cameraInfo().image_starts;
  if (vizPlayMode.value === "row") {
    const next = vizState.row + direction;
    return next;
  }
  const next = starts[imageNumberAt(vizState.row) + direction];
  return next;
}

const vizPlayIcons = {
  play: '<svg class="size-4" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M7 5v14l11-7z"></path></svg>',
  pause: '<svg class="size-4" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M7 5h4v14H7zM13 5h4v14h-4z"></path></svg>',
};

function setPlayButton(playing) {
  vizPlayIcon.innerHTML = playing ? vizPlayIcons.pause : vizPlayIcons.play;
  vizPlayLabel.textContent = playing ? "暂停" : "播放";
}

function stopPlayback(message = "") {
  const wasPlaying = playState.playing;
  playState.playing = false;
  setPlayButton(false);
  vizPlayStatus.textContent = message;
  if (wasPlaying && vizState.frameIndex) loadFrameValues(vizState.row).catch(() => undefined);
}

async function runPlayback() {
  playState.rendered = 0;
  playState.measuredAt = performance.now();
  while (playState.playing) {
    const started = performance.now();
    const next = nextRow(1);
    if (next === undefined || next > vizState.frameIndex.length - 1) {
      if (!vizPlayLoop.checked) {
        stopPlayback("已播放到末尾");
        return;
      }
      await showRow(0, vizPlayDepth.checked);
    } else {
      await showRow(next, vizPlayDepth.checked);
    }
    playState.rendered += 1;
    const now = performance.now();
    if (now - playState.measuredAt >= 1000) {
      const fps = playState.rendered / ((now - playState.measuredAt) / 1000);
      vizPlayStatus.textContent = `实际约 ${fps.toFixed(1)} FPS`;
      playState.rendered = 0;
      playState.measuredAt = now;
    }
    // 目标帧率；读取和绘制慢于目标时不再额外等待，实际帧率显示在右侧。
    const remaining = 1000 / Number(vizPlayFps.value) - (performance.now() - started);
    if (remaining > 0) await new Promise((resolve) => setTimeout(resolve, remaining));
  }
}

function togglePlayback() {
  if (playState.playing) {
    stopPlayback();
    return;
  }
  playState.playing = true;
  setPlayButton(true);
  vizPlayStatus.textContent = "播放中…";
  runPlayback().catch((error) => stopPlayback(error.message));
}

// 叠加对应字段：H5 为同名 master / puppet（控制端与执行端），LeRobot 为 observation.state / action。
const pairedNames = { "observation.state": "action", action: "observation.state" };

function pairedFields(name) {
  const names = [name];
  if (vizFieldPair.checked) {
    let other = name.startsWith("master/") ? name.replace("master/", "puppet/") : name.replace("puppet/", "master/");
    if (pairedNames[name]) other = pairedNames[name];
    if (other !== name && vizState.frameIndex.series_names.includes(other)) names.push(other);
  }
  return names;
}

function cursorLine() {
  const line = {
    silent: true,
    symbol: "none",
    label: { show: false },
    lineStyle: { color: "#ef4444", width: 1 },
    data: [{ xAxis: vizState.row }],
  };
  return line;
}

function buildChartOption(curves, names) {
  const series = [];
  names.forEach((name, nameIndex) => {
    const dims = curves.series[name];
    const short = name.split("/")[1] ?? name;
    const owner = name.split("/")[0];
    const labels = vizState.frameIndex.series_labels?.[name];
    dims.forEach((values, dim) => {
      const points = curves.rows.map((row, i) => [row, values[i]]);
      const dimLabel = labels ? `·${labels[dim]}` : dims.length > 1 ? `[${dim}]` : "";
      series.push({
        name: `${owner}${owner === short ? "" : `·${short}`}${dimLabel}`,
        type: "line",
        showSymbol: false,
        lineStyle: { width: 1.2, type: nameIndex === 0 ? "solid" : "dashed" },
        color: CURVE_COLORS[dim % CURVE_COLORS.length],
        data: points,
        markLine: dim === 0 && nameIndex === 0 ? cursorLine() : undefined,
      });
    });
  });
  const option = {
    animation: false,
    grid: { left: 56, right: 16, top: 40, bottom: 72 },
    tooltip: { trigger: "axis", axisPointer: { type: "line" } },
    legend: { type: "scroll", top: 0, textStyle: { fontSize: 10 } },
    xAxis: { type: "value", min: curves.rows[0], max: curves.rows[curves.rows.length - 1], name: "行号", nameLocation: "middle", nameGap: 26 },
    yAxis: { type: "value", scale: true },
    dataZoom: [{ type: "inside" }, { type: "slider", height: 18, bottom: 8 }],
    series,
  };
  return option;
}

async function loadCurves() {
  if (!window.echarts || !vizField.value) return;
  const names = pairedFields(vizField.value);
  vizCurveStatus.textContent = "读取中…";
  try {
    const curves = await fetchJson(`/api/visualization/series?${vizQuery({ fields: names.join(",") })}`);
    if (!curveState.chart) {
      curveState.chart = window.echarts.init(vizChart);
      new ResizeObserver(() => curveState.chart.resize()).observe(vizChart);
      // 只注册一次：点击曲线区域跳转到对应行。
      curveState.chart.getZr().on("click", (event) => {
        const point = curveState.chart.convertFromPixel({ seriesIndex: 0 }, [event.offsetX, event.offsetY]);
        if (point) {
          stopPlayback();
          showRow(Math.round(point[0]));
        }
      });
    }
    curveState.chart.setOption(buildChartOption(curves, names), true);
    // 容器宽度在面板显示、窗口变化后才确定，这里按当前宽度重新布局。
    curveState.chart.resize();
    const sampled = curves.stride > 1 ? `，按每 ${curves.stride} 行抽样` : "，未抽样";
    vizCurveStatus.textContent = `${curves.rows.length} 个点${sampled}`;
  } catch (error) {
    vizCurveStatus.textContent = error.message;
  }
}

function updateCursor() {
  if (!curveState.chart) return;
  curveState.chart.setOption({ series: [{ markLine: cursorLine() }] });
}

async function loadFrameValues(row) {
  const request = ++curveState.valuesRequest;
  const frame = await fetchJson(`/api/visualization/frame?${vizQuery({ index: row })}`);
  if (request !== curveState.valuesRequest) return;
  const rows = Object.entries(frame.values).map(([name, values]) => {
    const labels = vizState.frameIndex.series_labels?.[name];
    const text = values.map((value, i) => (labels ? `${labels[i]}=${value.toFixed(6)}` : value.toFixed(6))).join(", ");
    const html = `<tr class="align-top"><td class="ui-td font-mono text-xs whitespace-nowrap">${escapeHtml(name)}</td><td class="ui-td font-mono text-xs text-gray-600 dark:text-neutral-400">${escapeHtml(text)}</td></tr>`;
    return html;
  });
  vizFrameValues.innerHTML = rows.join("");
}

async function loadEpisode() {
  stopPlayback();
  vizAlert("");
  vizViewer.hidden = true;
  vizCamera.disabled = true;
  if (!vizEpisode.value) return;
  try {
    vizState.frameIndex = await fetchJson(`/api/visualization/episode?${vizQuery()}`);
  } catch (error) {
    vizAlert(error.message);
    return;
  }
  const cameras = Object.keys(vizState.frameIndex.cameras);
  vizCamera.innerHTML = cameras.map((camera) => `<option value="${escapeHtml(camera)}">${escapeHtml(camera)}</option>`).join("");
  vizCamera.disabled = false;
  const camera = cameraInfo();
  const [colorHeight, colorWidth] = camera.color_resolution;
  const [height, width] = camera.depth_resolution ?? [0, 0];
  // 分辨率字段存储顺序为 [height, width]，这里按“宽 × 高”显示。
  vizColorRes.textContent = `${colorWidth} × ${colorHeight}`;
  vizDepthRes.textContent = camera.depth_resolution ? `${width} × ${height}` : "无深度";
  vizDepth.width = width || 1;
  vizDepth.height = height || 1;
  vizState.depth = null;
  vizDepthMin.value = "";
  vizDepthMax.value = "";
  vizSlider.max = vizState.frameIndex.length - 1;
  vizIndex.max = vizState.frameIndex.length - 1;
  vizLength.textContent = `/ ${vizState.frameIndex.length - 1}`;
  vizViewer.hidden = false;
  renderVizSubtasks();

  const timestamps = vizState.frameIndex.timestamps;
  const seconds = timestamps[timestamps.length - 1] - timestamps[0] + 1;
  const images = camera.image_starts.length;
  const cameraRate = (images / seconds).toFixed(1);
  const rowRate = (vizState.frameIndex.length / seconds).toFixed(0);
  const previousField = vizField.value;
  vizField.innerHTML = vizState.frameIndex.series_names
    .map((name) => `<option value="${escapeHtml(name)}">${escapeHtml(name)}</option>`)
    .join("");
  if (vizState.frameIndex.series_names.includes(previousField)) vizField.value = previousField;
  await loadCurves();

  vizEpisodeInfo.textContent = vizState.frameIndex.format === "h5"
    ? `共 ${vizState.frameIndex.length} 行、${images} 张图像，约 ${rowRate} 行/秒、${cameraRate} 张图像/秒（时间戳只有整数秒，为粗估）。`
    : `共 ${vizState.frameIndex.length} 帧，每帧一张图像；时间戳 = frame_index / fps，RGB 为视频解码结果。`;
  showRow(0);
}

async function loadEpisodeOptions() {
  vizEpisode.disabled = true;
  vizEpisode.innerHTML = "";
  vizViewer.hidden = true;
  if (!vizDataset.value) return;
  vizEpisode.innerHTML = "<option>加载中…</option>";
  try {
    const detail = await fetchJson(`/api/datasets/${vizDataset.value}`);
    const options = detail.episodes.map((episode) => {
      const disabled = episode.status === "invalid" ? " disabled" : "";
      const unit = detail.format === "h5" ? "行" : "帧";
      const label = episode.status === "invalid" ? `${episode.id}（不可读）` : `${episode.id}（${episode.length} ${unit}）`;
      const option = `<option value="${escapeHtml(episode.id)}"${disabled}>${escapeHtml(label)}</option>`;
      return option;
    });
    vizEpisode.innerHTML = options.join("");
    vizEpisode.disabled = false;
    const firstReadable = detail.episodes.find((episode) => episode.status !== "invalid");
    if (firstReadable) vizEpisode.value = firstReadable.id;
    await loadEpisode();
  } catch (error) {
    vizEpisode.innerHTML = "";
    vizAlert(error.message);
  }
}

// 进入本页面时刷新数据集列表，以反映新登记或删除的数据集；选中项变化时重新读取 episode。
async function loadDatasetOptions() {
  try {
    if (await fillDatasetSelect(vizDataset)) await loadEpisodeOptions();
  } catch (error) {
    vizAlert(error.message);
  }
}

vizDataset.addEventListener("change", loadEpisodeOptions);
vizEpisode.addEventListener("change", loadEpisode);
vizField.addEventListener("change", loadCurves);
vizFieldPair.addEventListener("change", loadCurves);
window.addEventListener("resize", () => curveState.chart?.resize());
vizPlay.addEventListener("click", togglePlayback);
setPlayButton(false);
vizDataset.addEventListener("change", () => stopPlayback());
vizCamera.addEventListener("change", () => {
  stopPlayback();
  vizState.depth = null;
  vizDepthMax.value = "";
  showRow(vizState.row);
});
vizSlider.addEventListener("input", () => {
  stopPlayback();
  showRow(Number(vizSlider.value));
});
vizIndex.addEventListener("change", () => {
  stopPlayback();
  showRow(Number(vizIndex.value));
});
vizDepthMin.addEventListener("input", drawDepth);
vizDepthMax.addEventListener("input", drawDepth);
document.querySelector("#viz-depth-auto").addEventListener("click", autoDepthRange);
document.querySelectorAll(".viz-step").forEach((button) => {
  button.addEventListener("click", () => stepRow(button.dataset.step, Number(button.dataset.direction)));
});

document.addEventListener("keydown", (event) => {
  const panelVisible = !document.querySelector('[data-panel="visualization"]').hidden;
  const typing = ["INPUT", "SELECT", "TEXTAREA"].includes(event.target.tagName) && event.target.type !== "range";
  if (!panelVisible || vizViewer.hidden || typing) return;
  if (event.key === " ") {
    event.preventDefault();
    togglePlayback();
    return;
  }
  if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
  event.preventDefault();
  stopPlayback();
  const direction = event.key === "ArrowRight" ? 1 : -1;
  stepRow(event.shiftKey ? "image" : "row", direction);
});

vizDepth.addEventListener("mousemove", (event) => {
  if (!vizState.depth) return;
  const [height, width] = cameraInfo().depth_resolution;
  const box = vizDepth.getBoundingClientRect();
  const x = Math.min(Math.floor((event.clientX - box.left) / box.width * width), width - 1);
  const y = Math.min(Math.floor((event.clientY - box.top) / box.height * height), height - 1);
  const value = vizState.depth[y * width + x];
  vizDepthValue.textContent = `x=${x}, y=${y}：${value}（uint16，毫米）`;
});

vizSubtasks.addEventListener("click", (event) => {
  const element = event.target.closest("[data-start]");
  if (element) showRow(Number(element.dataset.start));
});

onPanelShow("visualization", () => {
  loadDatasetOptions();
  // 面板此前是隐藏的（宽度为 0），显示后需要让图表重新按实际宽度布局。
  curveState.chart?.resize();
});
