const datasetMessage = document.querySelector("#dataset-message");
const datasetList = document.querySelector("#dataset-list");
const datasetDetail = document.querySelector("#dataset-detail");
const datasetDialog = document.querySelector("#dataset-dialog");
const datasetForm = document.querySelector("#dataset-register-form");
const datasetDialogMessage = document.querySelector("#dataset-dialog-message");

const statusBadges = {
  ok: ["正常", "bg-teal-100 text-teal-800 dark:bg-teal-900/40 dark:text-teal-300"],
  issues: ["异常", "bg-yellow-100 text-yellow-800 dark:bg-yellow-900/40 dark:text-yellow-300"],
  invalid: ["不可读", "bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-300"],
  empty: ["空字段", "bg-gray-100 text-gray-700 dark:bg-neutral-700 dark:text-neutral-300"],
};
const formatBadges = {
  h5: "bg-purple-100 text-purple-800 dark:bg-purple-900/40 dark:text-purple-300",
  "lerobot_v3.0": "bg-blue-100 text-blue-800 dark:bg-blue-900/40 dark:text-blue-300",
  "lerobot_v2.1": "bg-gray-100 text-gray-700 dark:bg-neutral-700 dark:text-neutral-300",
};

function statusBadge(status) {
  const [label, colors] = statusBadges[status];
  const html = `<span class="ui-badge ${colors}">${label}</span>`;
  return html;
}

function formatBadge(format) {
  const html = `<span class="ui-badge ${formatBadges[format] ?? formatBadges["lerobot_v2.1"]}">${escapeHtml(formatLabel(format))}</span>`;
  return html;
}

// LeRobot 显示 episode 数与帧数；H5 需逐个打开文件才能统计，在详情中查看。
function datasetSize(dataset) {
  if (!dataset.summary) {
    const title = dataset.format === "h5" ? "H5 的 episode 数在详情中查看" : "未找到 meta/info.json";
    return `<span class="ui-hint" title="${title}">—</span>`;
  }
  const html = `${formatCount(dataset.summary.episodes)} 个 episode<div class="ui-hint">${formatCount(dataset.summary.frames)} 帧</div>`;
  return html;
}

function renderDatasetRows(datasets) {
  if (!datasets.length) {
    datasetList.innerHTML = `<tr><td colspan="5" class="p-4">${emptyHtml("还没有数据集。点击右上角“添加数据集”登记本地目录。")}</td></tr>`;
    return;
  }
  const selected = datasetDetail.dataset.datasetId;
  const rows = datasets.map((dataset) => `
    <tr class="${String(dataset.id) === selected ? "bg-blue-50 dark:bg-blue-900/20" : ""}">
      <td class="ui-td max-w-[9rem] sm:max-w-md">
        <button type="button" class="ui-link block max-w-full truncate text-start" data-dataset-id="${dataset.id}">${escapeHtml(dataset.name)}</button>
        <div class="ui-hint truncate" title="${escapeHtml(dataset.path)}">${escapeHtml(dataset.path)}</div>
      </td>
      <td class="ui-td">${formatBadge(dataset.format)}</td>
      <td class="ui-td hidden whitespace-nowrap sm:table-cell">${datasetSize(dataset)}</td>
      <td class="ui-td hidden whitespace-nowrap md:table-cell"><span class="ui-hint">${escapeHtml((dataset.created_at || "").replace("T", " "))}</span></td>
      <td class="ui-td whitespace-nowrap text-end">
        <button type="button" class="ui-btn ui-btn-sm" data-dataset-id="${dataset.id}">查看</button>
        <button type="button" class="ui-btn ui-btn-sm ui-btn-danger" data-remove-id="${dataset.id}" data-remove-name="${escapeHtml(dataset.name)}">删除登记</button>
      </td>
    </tr>`);
  datasetList.innerHTML = rows.join("");
}

async function loadDatasets() {
  if (!datasetList.children.length) datasetList.innerHTML = `<tr><td colspan="5" class="p-4">${loadingHtml()}</td></tr>`;
  const datasets = await fetchJson("/api/datasets");
  datasets.forEach((dataset) => datasetCache.set(dataset.id, dataset));
  renderDatasetRows(datasets);
}

function previewValue(value) {
  if (value === null || value === undefined) return "—";
  if (Array.isArray(value)) return `[${value.join(", ")}]`;
  return String(value);
}

function sectionCard(title, subtitle, bodyHtml) {
  const html = `
    <div class="ui-card space-y-4">
      <div>
        <h2 class="text-base font-semibold text-gray-800 dark:text-white">${escapeHtml(title)}</h2>
        ${subtitle ? `<p class="ui-hint mt-0.5">${escapeHtml(subtitle)}</p>` : ""}
      </div>
      ${bodyHtml}
    </div>`;
  return html;
}

function renderDatasetPreview(preview) {
  const summary = preview.format === "h5"
    ? Object.entries(preview.summary.metadata).slice(0, 4).map(([key, value]) => [key, previewValue(value)])
    : [["episode", formatCount(preview.summary.episodes)], ["帧数", formatCount(preview.summary.frames)], ["FPS", preview.summary.fps], ["机器人", preview.summary.robot_type ?? "—"]];
  const fieldRows = preview.fields.map((field) => {
    const names = Array.isArray(field.names) ? field.names.join(", ") : previewValue(field.names);
    const detail = Object.entries(field.detail).map(([key, value]) => `${key}=${value}`).join("，");
    const row = `<tr class="align-top"><td class="ui-td font-mono text-xs whitespace-nowrap">${escapeHtml(field.name)}</td><td class="ui-td">${escapeHtml(field.dtype)}</td><td class="ui-td whitespace-nowrap">${escapeHtml(JSON.stringify(field.shape))}</td><td class="ui-td min-w-64 text-xs">${escapeHtml(names)}</td><td class="ui-td text-xs">${escapeHtml(detail)}</td></tr>`;
    return row;
  });
  const taskRows = preview.tasks.map((task) => `<tr><td class="ui-td min-w-80 select-all">${escapeHtml(task.task)}</td><td class="ui-td">${task.episodes ?? "—"}</td><td class="ui-td">${task.frames === null || task.frames === undefined ? "—" : formatCount(task.frames)}</td></tr>`);
  const dataRows = preview.rows.map((row) => {
    const cells = preview.columns.map((column) => {
      const wide = Array.isArray(row[column]) || column === "task" ? "min-w-72" : "whitespace-nowrap";
      return `<td class="ui-td font-mono text-xs ${wide}">${escapeHtml(previewValue(row[column]))}</td>`;
    });
    return `<tr class="align-top">${cells.join("")}</tr>`;
  });
  const body = `
    ${statCards(summary)}
    ${detailsHtml(`字段（${preview.fields.length}）`, tableHtml(["字段", "类型", "形状", "各维名称", "说明"], fieldRows, "max-h-80"), true)}
    ${detailsHtml(`任务描述（${preview.tasks.length}）`, preview.tasks.length ? tableHtml(["任务描述", "episode", "帧"], taskRows, "max-h-80") : emptyHtml("无任务描述。"))}
    ${detailsHtml(`前 ${preview.rows.length} 行数据（${preview.row_source}）`, tableHtml(preview.columns, dataRows) + `<p class="ui-hint mt-2">${escapeHtml(preview.note)}</p>`)}`;
  const html = sectionCard(preview.name, `${formatLabel(preview.format)} · 字段与数据预览`, body);
  return html;
}

function renderSchema(schema) {
  if (!schema) return "";
  // LeRobot 数据集没有相机型号，也可能没有深度相机。
  const cameraRows = Object.entries(schema.cameras).map(([name, camera]) => `
    <tr><td class="ui-td">${escapeHtml(name)}</td><td class="ui-td">${escapeHtml(camera.model ?? "—")}</td><td class="ui-td">${escapeHtml(camera.color_channel)}</td>
      <td class="ui-td">${escapeHtml(camera.color_resolution.join(" × "))}</td><td class="ui-td">${escapeHtml(camera.depth_resolution ? camera.depth_resolution.join(" × ") : "无")}</td></tr>`);
  const seriesRows = Object.entries(schema.series).map(([name, series]) => `
    <tr><td class="ui-td font-mono text-xs">${escapeHtml(name)}</td><td class="ui-td">${escapeHtml(series.shape.join(" × "))}</td><td class="ui-td">${escapeHtml(series.dtype)}</td><td class="ui-td">${series.empty ? statusBadge("empty") : ""}</td></tr>`);
  const html = detailsHtml(`相机与数值字段（取自 ${schema.source_episode}）`, `
    <div class="grid grid-cols-1 gap-4 lg:grid-cols-2">
      <div><div class="ui-hint mb-1">相机（分辨率为 高 × 宽）</div>${tableHtml(["相机", "型号", "通道", "RGB", "深度"], cameraRows)}</div>
      <div><div class="ui-hint mb-1">数值字段</div>${tableHtml(["字段", "形状", "类型", ""], seriesRows, "max-h-72")}</div>
    </div>`);
  return html;
}

function renderEpisodeRows(episodes) {
  const rows = episodes.map((episode) => `
    <tr>
      <td class="ui-td font-mono text-xs">${escapeHtml(episode.id)}</td>
      <td class="ui-td">${episode.length ?? "—"}</td>
      <td class="ui-td whitespace-nowrap"><span class="ui-hint">${escapeHtml(episode.collection_time ?? "—")}</span></td>
      <td class="ui-td" title="${escapeHtml(episode.error ?? "")}">${statusBadge(episode.status)}</td>
      <td class="ui-td text-end">${episode.status === "invalid"
        ? `<span class="text-xs text-red-700 dark:text-red-400">${escapeHtml(episode.error)}</span>`
        : `<button type="button" class="ui-btn ui-btn-sm" data-episode-id="${escapeHtml(episode.id)}">详情</button>`}</td>
    </tr>`);
  return rows;
}

function renderDetail(detail) {
  const body = `
    ${statCards([["episode", detail.episode_count], ["可读取", detail.readable_count], ["存在问题", detail.problem_count], ["总行数", formatCount(detail.total_frames)]])}
    ${renderSchema(detail.schema)}
    <div class="grid grid-cols-1 gap-4 lg:grid-cols-5">
      <div class="lg:col-span-3">${tableHtml(["episode", "行数", "采集时间", "状态", ""], renderEpisodeRows(detail.episodes), "max-h-[32rem]")}</div>
      <div id="episode-detail" class="lg:col-span-2">${emptyHtml("选择一个 episode 查看详情。")}</div>
    </div>`;
  const html = sectionCard("episode 与数据质量", detail.description ?? "", body);
  return html;
}

function renderEpisode(episode) {
  const metadataRows = Object.entries(episode.metadata).map(
    ([key, value]) => `<div class="text-gray-500 dark:text-neutral-400">${escapeHtml(key)}</div><div class="break-all">${escapeHtml(value)}</div>`,
  );
  const issues = episode.issues.map((issue) => `${issue.type}：${issue.name}`);
  const html = `
    <div class="rounded-lg border border-gray-200 p-4 text-sm dark:border-neutral-700">
      <h3 class="font-mono font-semibold text-gray-800 dark:text-white">${escapeHtml(episode.id)}</h3>
      <div class="mt-3 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1">
        <div class="text-gray-500 dark:text-neutral-400">行数</div><div>${episode.length}</div>
        <div class="text-gray-500 dark:text-neutral-400">时间戳（整数秒）</div><div>${episode.timestamp_range.join(" – ")}</div>
        ${metadataRows.join("")}
      </div>
      <h4 class="mt-4 font-semibold text-gray-800 dark:text-white">检查结果</h4>
      <div class="mt-1 text-gray-600 dark:text-neutral-400">${issues.length ? listHtml(issues) : "无问题"}</div>
      <p class="ui-hint mt-2">empty 表示该字段为空数组，按原样保留，不补零。</p>
    </div>`;
  return html;
}

async function showDataset(datasetId) {
  datasetDetail.hidden = false;
  datasetDetail.dataset.datasetId = datasetId;
  datasetList.querySelectorAll("tr").forEach((row) => {
    const selected = row.querySelector(`[data-dataset-id="${datasetId}"]`) !== null;
    row.classList.toggle("bg-blue-50", selected);
    row.classList.toggle("dark:bg-blue-900/20", selected);
  });
  datasetDetail.innerHTML = `<div id="dataset-preview" class="ui-card">${loadingHtml("正在读取字段与前几行数据…")}</div><div id="dataset-episodes" class="ui-card">${loadingHtml("正在读取 episode…")}</div>`;
  datasetDetail.scrollIntoView({ behavior: "smooth", block: "start" });
  // 预览只读少量数据，先显示；episode 列表（H5 需逐个打开文件）随后加载。
  const [preview, detail] = [fetchJson(`/api/datasets/${datasetId}/preview`), fetchJson(`/api/datasets/${datasetId}`)];
  const replace = (selector, html) => {
    if (datasetDetail.dataset.datasetId === String(datasetId)) document.querySelector(selector).outerHTML = `<div id="${selector.slice(1)}">${html}</div>`;
  };
  try {
    replace("#dataset-preview", renderDatasetPreview(await preview));
  } catch (error) {
    replace("#dataset-preview", alertHtml(error.message, "error"));
  }
  try {
    replace("#dataset-episodes", renderDetail(await detail));
  } catch (error) {
    replace("#dataset-episodes", alertHtml(error.message, "error"));
  }
}

async function showEpisode(episodeId) {
  const target = document.querySelector("#episode-detail");
  target.innerHTML = loadingHtml();
  try {
    const episode = await fetchJson(`/api/datasets/${datasetDetail.dataset.datasetId}/episodes/${encodeURI(episodeId)}`);
    target.innerHTML = renderEpisode(episode);
  } catch (error) {
    target.innerHTML = alertHtml(error.message, "error");
  }
}

async function removeDataset(datasetId, name) {
  if (!confirm(`删除“${name}”的登记？\n只删除平台中的登记，不会删除磁盘上的数据。`)) return;
  try {
    await fetchJson(`/api/datasets/${datasetId}`, { method: "DELETE" });
    datasetMessage.innerHTML = alertHtml(`已删除登记：${name}（数据文件未改动）`, "success");
    if (datasetDetail.dataset.datasetId === String(datasetId)) {
      datasetDetail.hidden = true;
      datasetDetail.innerHTML = "";
      delete datasetDetail.dataset.datasetId;
    }
    await loadDatasets();
  } catch (error) {
    datasetMessage.innerHTML = alertHtml(error.message, "error");
  }
}

datasetForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = datasetForm.querySelector("button[type=submit]");
  const form = new FormData(datasetForm);
  const body = { path: form.get("path").trim(), name: form.get("name").trim() || null };
  button.disabled = true;
  datasetDialogMessage.innerHTML = "";
  try {
    const dataset = await postJson("/api/datasets", body);
    datasetDialog.close();
    datasetForm.reset();
    datasetMessage.innerHTML = alertHtml(`已添加：${dataset.name}`, "success");
    await loadDatasets();
    await showDataset(dataset.id);
  } catch (error) {
    datasetDialogMessage.innerHTML = alertHtml(error.message, "error");
  } finally {
    button.disabled = false;
  }
});

document.querySelector("#dataset-add").addEventListener("click", () => {
  datasetDialogMessage.innerHTML = "";
  datasetDialog.showModal();
});
document.querySelector("#dataset-dialog-cancel").addEventListener("click", () => datasetDialog.close());

datasetList.addEventListener("click", (event) => {
  const removeButton = event.target.closest("[data-remove-id]");
  if (removeButton) {
    removeDataset(removeButton.dataset.removeId, removeButton.dataset.removeName);
    return;
  }
  const viewButton = event.target.closest("[data-dataset-id]");
  if (viewButton) showDataset(viewButton.dataset.datasetId);
});

datasetDetail.addEventListener("click", (event) => {
  const button = event.target.closest("[data-episode-id]");
  if (button) showEpisode(button.dataset.episodeId);
});

function refreshDatasets() {
  loadDatasets().catch((error) => {
    datasetMessage.innerHTML = alertHtml(error.message, "error");
  });
}

// 其他页面生成的数据集会自动登记；显示本页时重新读取列表。
onPanelShow("dataset", refreshDatasets);
// 其他页面的“查看数据集”。
document.addEventListener("dataset:open", async (event) => {
  await loadDatasets().catch(() => {});
  showDataset(event.detail);
});
