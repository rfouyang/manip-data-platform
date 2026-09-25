const convDataset = document.querySelector("#conv-dataset");
const convTarget = document.querySelector("#conv-target");
const convTargetHint = document.querySelector("#conv-target-hint");
const convScope = document.querySelector("#conv-scope");
const convMaxEpisodes = document.querySelector("#conv-max-episodes");
const convOutput = document.querySelector("#conv-output");
const convResult = document.querySelector("#conv-result");
const convTask = document.querySelector("#conv-task");
const convFrameCountButton = document.querySelector("#conv-frame-count");

// 源格式 → 可选目标：[值, 名称, 目标说明, 是否支持只转前 N 个 episode]。不提供转回 H5。
const convTargets = {
  h5: [
    ["lerobot_v3.0", "LeRobot v3.0", "", true],
    ["lerobot_v2.1", "LeRobot v2.1", "先生成临时 v3.0 并验证，完成后只保留 v2.1；v2.1 不含深度。", true],
  ],
  "lerobot_v3.0": [["lerobot_v2.1", "LeRobot v2.1", "v2.1 不含深度与语言标注列。", true]],
  "lerobot_v2.1": [["lerobot_v3.0", "LeRobot v3.0", "使用官方转换函数，整体转换全部 episode。", false]],
};
const convSuffixes = { "lerobot_v3.0": "_lerobot_v3", "lerobot_v2.1": "_lerobot_v21" };

function convTargetOption() {
  const dataset = datasetById(convDataset.value);
  const option = (convTargets[dataset?.format] ?? []).find(([value]) => value === convTarget.value);
  return option;
}

function updateTargets() {
  const dataset = datasetById(convDataset.value);
  const options = convTargets[dataset?.format] ?? [];
  convTarget.innerHTML = options.map(([value, label]) => `<option value="${value}">${escapeHtml(label)}</option>`).join("");
  convFrameCountButton.hidden = dataset?.format !== "h5";
  updateTargetHint();
}

function updateTargetHint() {
  const dataset = datasetById(convDataset.value);
  const option = convTargetOption();
  convTargetHint.textContent = option?.[2] ?? "";
  convTargetHint.hidden = !option?.[2];
  convScope.hidden = !option?.[3];
  convOutput.placeholder = dataset && option ? `默认 ${dataset.name}${convSuffixes[option[0]]}` : "";
  convFlow.reset();
  convTask.innerHTML = "";
}

function convRequest() {
  if (!convDataset.value || !convTarget.value) return null;
  const body = { dataset_id: Number(convDataset.value), target: convTarget.value };
  if (convOutput.value.trim()) body.output_name = convOutput.value.trim();
  if (!convScope.hidden && convMaxEpisodes.value) body.max_episodes = Number(convMaxEpisodes.value);
  return body;
}

function convPreviewRequest(body) {
  const params = new URLSearchParams({ dataset_id: body.dataset_id, target: body.target });
  if (body.output_name) params.set("output_name", body.output_name);
  const preview = fetchJson(`/api/conversion/preview?${params}`);
  return preview;
}

function commentHtml(...comments) {
  const html = `<p class="ui-hint mt-2">${escapeHtml(comments.filter(Boolean).join(" "))}</p>`;
  return html;
}

function renderVersionPreview(preview) {
  const rows = preview.features.map((feature) => `<tr><td class="ui-td font-mono text-xs">${escapeHtml(feature.name)}</td><td class="ui-td">${escapeHtml(feature.dtype)}</td><td class="ui-td">${escapeHtml(JSON.stringify(feature.shape))}</td><td class="ui-td">${feature.kept ? "写入" : "不写入"}</td></tr>`);
  const html = `
    ${previewStatusHtml(preview, `${preview.episode_count} 个 episode、${formatCount(preview.total_frames)} 帧。`)}
    ${statCards([["转换", `${formatLabel(preview.dataset.format)} → ${formatLabel(preview.target)}`], ["episode", preview.episode_count], ["帧数", formatCount(preview.total_frames)], ["不写入", preview.dropped_features.length ? preview.dropped_features.length + " 个 feature" : "无"]])}
    ${detailsHtml(`feature（${preview.features.length}）`, tableHtml(["feature", "类型", "形状", "目标"], rows, "max-h-80"))}
    ${detailsHtml("转换说明", `<div class="text-sm">${listHtml(preview.notes)}</div>`)}
    ${outputHtml(preview)}`;
  return html;
}

function renderH5Preview(preview) {
  const mapping = preview.mapping;
  const blocked = preview.blocked_episodes.length
    ? alertHtml(`${preview.blocked_episodes.length} 个 episode 不可读，转换时跳过：${preview.blocked_episodes.map((episode) => `${episode.id}（${episode.error}）`).join("；")}`, "warning")
    : "";
  const issues = preview.field_issues.length
    ? detailsHtml(`字段不完整的 episode（${preview.field_issues.length}）`, listHtml(preview.field_issues.map((item) => `${item.episode_id}：${item.issues.map((issue) => `${issue.name}（${issue.type}）`).join("、")}`)), true)
    : "";
  const layoutRows = preview.layout.entries.map((entry) => `<tr><td class="ui-td font-mono text-xs">${escapeHtml(entry.field)}</td><td class="ui-td whitespace-nowrap">${entry.dims > 1 ? `${entry.slice[0]}–${entry.slice[1] - 1}` : entry.slice[0]}</td><td class="ui-td">${escapeHtml(entry.unit)}</td><td class="ui-td text-xs">${escapeHtml(entry.names.join(", "))}</td></tr>`);
  const unmappedRows = mapping.unmapped.map((item) => `<tr><td class="ui-td font-mono text-xs">${escapeHtml(item.name)}</td><td class="ui-td">${escapeHtml(item.reason)}</td></tr>`);
  const semantics = listHtml([
    `state：当前帧的 ${mapping.state.source}；action：后第 ${mapping.action.offset_frames} 帧的 ${mapping.action.source}`,
    `取帧：按相机图像更新（约 ${preview.rows_per_frame} 行/帧），${mapping.fps} FPS`,
    `深度：${mapping.depth.unit}，超过 4095 mm 截断`,
    `任务描述：${preview.task.join("；") || "—"}`,
  ]);
  const html = `
    ${previewStatusHtml(preview, `${preview.convertible_count} 个 episode，估算输出 ${formatCount(preview.estimated_frames)} 帧。`)}
    ${blocked}
    ${issues}
    ${statCards([["转换", `H5 → ${formatLabel(preview.target)}`], ["可转换 episode", `${preview.convertible_count} / ${preview.episode_count}`], ["估算输出帧", formatCount(preview.estimated_frames)], ["FPS", preview.fps]])}
    ${detailsHtml("字段对应", `<div class="text-sm">${semantics}</div>${commentHtml(mapping.action.comment, mapping.last_frame.comment)}`)}
    ${detailsHtml(`action / state 向量（${preview.layout.size} 维）`, tableHtml(["源字段", "维度", "单位", "名称"], layoutRows) + commentHtml(mapping.vector_comment))}
    ${detailsHtml(`不写入的字段（${mapping.unmapped.length}）`, tableHtml(["字段", "原因"], unmappedRows))}
    ${outputHtml(preview)}`;
  return html;
}

function renderConvPreview(preview) {
  const html = preview.kind === "lerobot_version" ? renderVersionPreview(preview) : renderH5Preview(preview);
  return html;
}

// 一句话结果：适用于 H5 → v3.0 / v2.1 与版本互转。
function convResultText(result) {
  const parts = [`生成 ${baseName(result.output_path)}（${result.episode_count} 个 episode、${formatCount(result.frames)} 帧），验证 ${result.validation.check_count} 项通过`];
  if (result.v3_validation) parts.push(`经临时 v3.0（验证 ${result.v3_validation.check_count} 项）`);
  if (result.dropped_features?.length) parts.push(`未写入 ${result.dropped_features.join("、")}`);
  if (result.depth_over_range_pixels) parts.push(`${formatCount(result.depth_over_range_pixels)} 个深度像素超过 4095 mm 已截断`);
  if (result.skipped_episodes?.length) parts.push(`跳过 ${result.skipped_episodes.length} 个 episode`);
  const text = `${parts.join("；")}。`;
  return text;
}

async function submitFrameCount() {
  convFrameCountButton.disabled = true;
  convTask.innerHTML = loadingHtml("提交中…");
  try {
    const task = await postJson("/api/conversion/frame-count", { dataset_id: Number(convDataset.value) });
    pollTaskInto(task.id, convTask, frameCountResultText);
  } catch (error) {
    convTask.innerHTML = alertHtml(error.message, "error");
  } finally {
    convFrameCountButton.disabled = false;
  }
}

async function loadConversionDatasets() {
  try {
    const changed = await fillDatasetSelect(convDataset);
    if (changed || !convTarget.options.length) updateTargets();
  } catch (error) {
    convResult.innerHTML = alertHtml(error.message, "error");
  }
}

const convFlow = setupFlow({
  previewButton: document.querySelector("#conv-preview"),
  submitButton: document.querySelector("#conv-submit"),
  result: convResult,
  status: convTask,
  request: convRequest,
  preview: convPreviewRequest,
  submit: (body) => postJson("/api/conversion/convert", body),
  renderPreview: renderConvPreview,
  resultText: convResultText,
  onSucceeded: loadConversionDatasets,
});

convDataset.addEventListener("change", updateTargets);
convTarget.addEventListener("change", updateTargetHint);
convOutput.addEventListener("input", () => convFlow.reset());
convFrameCountButton.addEventListener("click", submitFrameCount);
onPanelShow("conversion", loadConversionDatasets);
