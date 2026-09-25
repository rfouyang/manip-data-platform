import json
import shutil
from datetime import datetime
from pathlib import Path

import h5py
import numpy as np
import pandas as pd
from loguru import logger

from component.common.dataset_access import LEROBOT_V3, LEROBOT_V21, DatasetAccess
from component.common.output_publish import output_fields, publish, remove_temporary, resolve_output, staged_output, temporary_path
from component.common.validation import Checks, require_passed
from component.conversion.version_converter import (
    LEROBOT_VERSION,
    V3_TO_V21_JOB,
    V21_TO_V3_JOB,
    dropped_features,
    validate_v21_output,
    version_notes,
    write_v21,
)
from util.lerobot_helper import (
    DEPTH_MAX_MM,
    create_dataset,
    image_feature,
    load_metadata,
    local_repo_id,
    official_read_failures,
    open_dataset,
    remove_empty_image_dir,
    save_episode,
    to_depth_mm,
    to_rgb_uint8,
    vector_feature,
)
from util.lerobot_v21_helper import (
    v21_features,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MAPPING_PATH = REPO_ROOT / "config" / "x_humanoid.json"
# 预检只对少量 episode 统计取帧比例；全量统计放在转换任务中。
DEFAULT_SAMPLE_EPISODES = 3
FRAME_COUNT_JOB = "conversion_frame_count"
CONVERT_JOB = "conversion_h5_to_lerobot_v3"
H5_TO_V21_JOB = "conversion_h5_to_lerobot_v21"
# 已支持的转换方向：(源格式, 目标格式) → (任务类型, 默认输出目录后缀)；每个源格式的第一项为默认目标。
# 不做 v3 → H5、v2.1 → H5；H5 → v2.1 由平台自动经 v3.0 完成，临时的 v3.0 不保留（用户确认，2026-09-23 / 2026-09-24）。
CONVERSIONS = {
    ("h5", LEROBOT_V3): (CONVERT_JOB, "_lerobot_v3"),
    ("h5", LEROBOT_V21): (H5_TO_V21_JOB, "_lerobot_v21"),
    (LEROBOT_V3, LEROBOT_V21): (V3_TO_V21_JOB, "_lerobot_v21"),
    (LEROBOT_V21, LEROBOT_V3): (V21_TO_V3_JOB, "_lerobot_v3"),
}
# 一个 episode 约 300 帧、写入约 15 s；帧内每隔若干帧检查一次取消。
CANCEL_CHECK_FRAMES = 30
# 抽查帧 RGB 与源的平均绝对误差上限（0–255）。视频有损编码实测每帧 2.0–2.35（2026-09-24 的 3 次转换），
# 超过上限说明取错图像或通道顺序错误；相邻帧差别小，错一帧由 RGB/深度切换行检查负责。
RGB_ERROR_LIMIT = 8.0


def load_mapping(path):
    mapping = json.loads(Path(path).read_text(encoding="utf-8"))
    return mapping


def output_frame_count(image_count, mapping):
    """action 取后续帧时，每个 episode 末尾没有后续帧的帧按 last_frame 策略明确丢弃。"""
    dropped = mapping["action"]["offset_frames"] if mapping["last_frame"]["policy"] == "drop" else 0
    count = max(image_count - dropped, 0)
    return count


class DataConverter:
    """转换映射、预检与后台任务提交（全量取帧统计、H5 → LeRobot v3.0 转换）。"""

    def __init__(self, dataset_manager, dataset_access, task_manager, output_dir, **kwargs):
        self.dataset_manager = dataset_manager
        self.dataset_access = dataset_access
        self.task_manager = task_manager
        self.output_dir = Path(output_dir)
        self.mapping_path = Path(kwargs.get("mapping_path", DEFAULT_MAPPING_PATH))

    def load_mapping(self):
        mapping = load_mapping(self.mapping_path)
        return mapping

    def vector_layout(self, mapping):
        """把 vector 配置展开为维度清单，明确 action/state 的顺序与单位。"""
        entries = []
        offset = 0
        for item in mapping["vector"]:
            entries.append({
                "field": item["field"],
                "dims": item["dims"],
                "unit": item["unit"],
                "names": item["names"],
                "meaning": item.get("meaning", ""),
                "slice": [offset, offset + item["dims"]],
            })
            offset += item["dims"]
        layout = {"size": offset, "entries": entries}
        return layout

    def field_issues(self, dataset, episodes, mapping):
        """逐 episode 核对映射所需字段与长度，返回有问题的 episode。"""
        issues_by_episode = []
        for episode in episodes:
            path = self.dataset_access.episode_path(dataset["path"], episode["id"])
            if not h5py.is_hdf5(path):
                continue
            with self.dataset_access.open_episode(dataset["path"], episode["id"]) as opened:
                issues = check_episode(opened, mapping)
            if issues:
                issues_by_episode.append({"episode_id": episode["id"], "issues": issues})
        return issues_by_episode

    def sample_frames(self, dataset, episode_ids, mapping):
        """对少量 episode 统计取帧结果，用于估算总帧数。"""
        samples = []
        for episode_id in episode_ids:
            with self.dataset_access.open_episode(dataset["path"], episode_id) as episode:
                camera = next(iter(mapping["cameras"]))
                starts = episode.image_change_indices("color", camera)
                samples.append({
                    "episode_id": episode_id,
                    "rows": episode.length,
                    "frames": len(starts),
                    "output_frames": output_frame_count(len(starts), mapping),
                    "rows_per_frame": round(episode.length / len(starts), 2),
                })
        return samples

    def check_direction(self, dataset, target):
        """返回该数据集的目标格式（未指定时取默认）；方向不支持时拒绝。"""
        supported = [to for source, to in CONVERSIONS if source == dataset["format"]]
        if not supported:
            raise ValueError(f"暂不支持从 {dataset['format']} 转换")
        target = target or supported[0]
        if target not in supported:
            raise ValueError(f"暂不支持的目标格式: {target}（{dataset['format']} 当前支持 {'、'.join(supported)}）")
        return target

    def output_path(self, dataset, **kwargs):
        target = self.check_direction(dataset, kwargs.get("target"))
        suffix = CONVERSIONS[(dataset["format"], target)][1]
        name = kwargs.get("output_name") or f"{dataset['name']}{suffix}"
        path = resolve_output(self.output_dir, name)
        return path

    def preview_version(self, dataset, target, **kwargs):
        """LeRobot 版本转换预检（v3.0 ↔ v2.1）：概况、保留与去掉的 feature、说明、输出目录与空间。"""
        meta = load_metadata(dataset["path"])
        dropped = dropped_features(meta, target)
        output = self.output_path(dataset, target=target, **kwargs)
        blocking = []
        if meta.total_episodes == 0:
            blocking.append("没有可转换的 episode")
        features = [
            {"name": key, "dtype": feature["dtype"], "shape": list(feature["shape"]), "kept": key not in dropped}
            for key, feature in meta.features.items()
        ]
        preview = {
            "kind": "lerobot_version",
            "dataset": {"id": dataset["id"], "name": dataset["name"], "path": dataset["path"], "format": dataset["format"]},
            "target": target,
            "fps": meta.fps,
            "episode_count": meta.total_episodes,
            "total_frames": meta.total_frames,
            "features": features,
            "dropped_features": dropped,
            "notes": version_notes(target, dropped, meta),
            "partial_supported": target == LEROBOT_V21,
            **output_fields(self.output_dir, output, blocking),
        }
        return preview

    def preview(self, dataset_id, **kwargs):
        """转换预检：确认映射、取帧方案、字段完整性、输出目录和磁盘空间，不写任何文件。"""
        dataset = self.dataset_manager.get(dataset_id)
        target = self.check_direction(dataset, kwargs.get("target"))
        if dataset["format"] != "h5":
            preview = self.preview_version(dataset, target, output_name=kwargs.get("output_name"))
            return preview
        mapping = self.load_mapping()
        detail = self.dataset_manager.inspect(dataset_id)
        episodes = detail["episodes"]
        blocked = [episode for episode in episodes if episode["status"] == "invalid"]
        convertible = [episode for episode in episodes if episode["status"] != "invalid"]

        field_issues = self.field_issues(dataset, convertible, mapping)
        sample_count = min(int(kwargs.get("sample_episodes", DEFAULT_SAMPLE_EPISODES)), len(convertible))
        samples = self.sample_frames(dataset, [episode["id"] for episode in convertible[:sample_count]], mapping)
        estimate = estimate_frames(samples, convertible, mapping)
        output = self.output_path(dataset, target=target, output_name=kwargs.get("output_name"))
        blocking = []
        if field_issues:
            blocking.append(f"{len(field_issues)} 个 episode 的映射字段不完整")
        if not convertible:
            blocking.append("没有可转换的 episode")

        preview = {
            "kind": "h5_to_lerobot",
            "dataset": {"id": dataset["id"], "name": dataset["name"], "path": dataset["path"], "format": dataset["format"]},
            "target": target,
            # H5 → v2.1：先生成临时 v3.0 再转 v2.1；v2.1 不支持深度，深度 feature 不写入。
            "via": LEROBOT_V3 if target == LEROBOT_V21 else None,
            "dropped_features": [item["depth_feature"] for item in mapping["cameras"].values()] if target == LEROBOT_V21 else [],
            "mapping_path": str(self.mapping_path),
            "mapping": mapping,
            "layout": self.vector_layout(mapping),
            "fps": mapping["fps"],
            "task": detail["tasks"],
            "episode_count": len(episodes),
            "convertible_count": len(convertible),
            "blocked_episodes": blocked,
            "field_issues": field_issues,
            "samples": samples,
            **estimate,
            "estimated_frames_note": "按抽样 episode 的行/帧比例估算相机图像数，再减去每个 episode 因 action 取下一帧而丢弃的末帧；转换时按每个 episode 实际图像边界取帧。",
            **output_fields(self.output_dir, output, blocking),
        }
        return preview

    def submit_frame_count(self, dataset_id):
        """提交全量取帧统计后台任务：逐 episode 统计相机图像与输出帧数，不写任何数据文件。"""
        dataset = self.dataset_manager.get(dataset_id)
        if dataset["format"] != "h5":
            raise ValueError(f"当前只支持 h5 数据集，该数据集格式为 {dataset['format']}")
        episodes = self.dataset_access.list_episodes(dataset["path"])
        params = {
            "dataset_id": dataset["id"],
            "dataset_path": dataset["path"],
            "episode_ids": [episode["id"] for episode in episodes if episode["status"] != "invalid"],
            "mapping_path": str(self.mapping_path),
        }
        task = self.task_manager.submit(FRAME_COUNT_JOB, params)
        return task

    def submit_conversion(self, dataset_id, **kwargs):
        """提交转换任务（H5 → v3.0、H5 → v2.1（经 v3.0）、v3.0 → v2.1、v2.1 → v3.0）。max_episodes 用于小样本；输出目录已存在时拒绝，不覆盖。"""
        dataset = self.dataset_manager.get(dataset_id)
        target = self.check_direction(dataset, kwargs.get("target"))
        kwargs = {**kwargs, "target": target}
        output = self.output_path(dataset, **kwargs)
        if output.exists():
            raise FileExistsError(f"输出目录已存在，不覆盖已有结果: {output}")
        if dataset["format"] != "h5":
            task = self.submit_version(dataset, target, output, max_episodes=kwargs.get("max_episodes"))
            return task
        episodes = [episode for episode in self.dataset_access.list_episodes(dataset["path"]) if episode["status"] != "invalid"]
        if kwargs.get("max_episodes"):
            episodes = episodes[: int(kwargs["max_episodes"])]
        if not episodes:
            raise ValueError("没有可转换的 episode")
        params = {
            "dataset_id": dataset["id"],
            "dataset_path": dataset["path"],
            "episode_ids": [episode["id"] for episode in episodes],
            "mapping_path": str(self.mapping_path),
            "output_path": str(output),
            "task": kwargs.get("task"),
        }
        task = self.task_manager.submit(CONVERSIONS[(dataset["format"], target)][0], params)
        return task

    def submit_version(self, dataset, target, output, **kwargs):
        meta = load_metadata(dataset["path"])
        count = meta.total_episodes
        if kwargs.get("max_episodes"):
            # v2.1 → v3.0 由官方函数整体转换；只转部分会与源 episode 编号不一致，因此不支持。
            if target != LEROBOT_V21:
                raise ValueError("v2.1 → v3.0 使用官方转换函数整体转换，不支持只转部分 episode")
            count = min(int(kwargs["max_episodes"]), count)
        if count == 0:
            raise ValueError("没有可转换的 episode")
        params = {
            "dataset_id": dataset["id"],
            "dataset_path": dataset["path"],
            "output_path": str(output),
            "episode_count": count,
            "dropped_features": dropped_features(meta, target),
        }
        task = self.task_manager.submit(CONVERSIONS[(dataset["format"], target)][0], params)
        return task


def estimate_frames(samples, episodes, mapping):
    """按抽样 episode 的行/帧比例估算相机图像数，再减去每个 episode 因 action 取下一帧而丢弃的末帧。"""
    total_rows = sum(episode["length"] for episode in episodes)
    rows_per_frame = sum(item["rows_per_frame"] for item in samples) / len(samples) if samples else None
    estimated_images = round(total_rows / rows_per_frame) if rows_per_frame else None
    estimated_frames = None
    if estimated_images is not None:
        dropped = estimated_images - output_frame_count(estimated_images, mapping)
        estimated_frames = estimated_images - len(episodes) * dropped
    estimate = {
        "total_rows": total_rows,
        "rows_per_frame": round(rows_per_frame, 2) if rows_per_frame else None,
        "estimated_frames": estimated_frames,
        "estimated_images": estimated_images,
    }
    return estimate


def check_episode(episode, mapping):
    """核对该 episode 是否具备映射所需的字段与长度。取帧只遍历 trajectory_length 行，
    用到的字段或相机图像更长时多出的行会被静默丢弃，因此预检与转换任务都据此拦截。"""
    issues = []
    # action 与 state 可能同源（均为 puppet），同一字段只检查一次。
    sources = sorted({mapping["action"]["source"], mapping["state"]["source"]})
    for source in sources:
        for item in mapping["vector"]:
            name = f"{source}/{item['field']}/data"
            if name not in episode.file:
                issues.append({"type": "missing_field", "name": name})
                continue
            data = episode.file[name]
            if data.size == 0:
                issues.append({"type": "empty_field", "name": name})
                continue
            dims = 1 if data.ndim == 1 else data.shape[1]
            if dims != item["dims"]:
                issues.append({"type": "dims", "name": name, "expected": item["dims"], "actual": dims})
            if data.shape[0] != episode.length:
                issues.append({"type": "length", "name": name, "length": data.shape[0]})
    for camera in mapping["cameras"]:
        if camera not in episode.cameras:
            issues.append({"type": "missing_camera", "name": camera})
            continue
        for kind in ("color", "depth"):
            name = f"camera_observations/{kind}_images/{camera}"
            if name not in episode.file:
                issues.append({"type": "missing_field", "name": name})
            elif episode.file[name].shape[0] != episode.length:
                issues.append({"type": "length", "name": name, "length": episode.file[name].shape[0]})
    return issues


def read_vectors(episode, mapping, source):
    """按映射顺序拼接该来源的所有行，返回 float64 (行数, 16)。"""
    parts = []
    for item in mapping["vector"]:
        data = episode.file[f"{source}/{item['field']}/data"][:]
        parts.append(data.reshape(len(data), item["dims"]))
    vectors = np.concatenate(parts, axis=1)
    return vectors


def episode_plan(episode, mapping):
    """取帧与 state/action：按相机图像起始行取帧；state、action 分别取后 offset_frames 帧的对应行，末帧按策略丢弃。"""
    camera = next(iter(mapping["cameras"]))
    starts = episode.image_change_indices("color", camera)
    count = output_frame_count(len(starts), mapping)
    state_offset = mapping["state"]["offset_frames"]
    action_offset = mapping["action"]["offset_frames"]
    state_rows = read_vectors(episode, mapping, mapping["state"]["source"])[starts]
    action_rows = read_vectors(episode, mapping, mapping["action"]["source"])[starts]
    plan = {
        "images": len(starts),
        "starts": starts,
        "rows": starts[:count],
        "state": state_rows[state_offset: state_offset + count],
        "action": action_rows[action_offset: action_offset + count],
    }
    return plan


def alignment_issues(episode, mapping, starts):
    """每路映射相机的 RGB 与深度须在取帧所用的行更新图像，否则取到的深度（或其他相机图像）错帧。
    starts 为第一路相机 RGB 的切换行（episode_plan 已算出，这里不再重复读取）。"""
    first = next(iter(mapping["cameras"]))
    issues = []
    for camera in mapping["cameras"]:
        for kind in ("color", "depth"):
            if (camera, kind) == (first, "color"):
                continue
            rows = episode.image_change_indices(kind, camera)
            if not np.array_equal(rows, starts):
                issues.append(f"{camera} {kind} 切换 {len(rows)} 次，取帧依据 {len(starts)} 次")
    return issues


def lerobot_features(mapping, episode):
    names = [name for item in mapping["vector"] for name in item["names"]]
    features = {"observation.state": vector_feature(names), "action": vector_feature(names)}
    for camera, target in mapping["cameras"].items():
        color_height, color_width = episode.read_value(f"camera_color_resolution/{camera}")
        depth_height, depth_width = episode.read_value(f"camera_depth_resolution/{camera}")
        features[target["rgb_feature"]] = image_feature((color_height, color_width, 3))
        features[target["depth_feature"]] = image_feature((depth_height, depth_width, 1), is_depth=True)
    return features


def write_lerobot(root, params, mapping, reporter):
    """逐 episode 写入 LeRobot，返回每个 episode 的写入记录。输出只含 LeRobot 标准文件。"""
    access = DatasetAccess()
    episode_ids = params["episode_ids"]
    with access.open_episode(params["dataset_path"], episode_ids[0]) as first:
        features = lerobot_features(mapping, first)
    dataset = create_dataset(root, mapping["fps"], features, robot_type=mapping["name"], repo_id=local_repo_id(params))
    episodes = []
    skipped = []
    try:
        for number, episode_id in enumerate(episode_ids):
            reporter.check_cancel()
            with access.open_episode(params["dataset_path"], episode_id) as episode:
                # 直接调用提交接口可绕过预检，任务中再核对一次。
                issues = check_episode(episode, mapping)
                if issues:
                    raise ValueError(f"{episode_id} 的映射字段不完整或长度不一致: {issues}")
                plan = episode_plan(episode, mapping)
                if len(plan["rows"]) == 0:
                    skipped.append({"episode_id": episode_id, "reason": f"只有 {plan['images']} 张相机图像，丢弃末帧后没有可输出的帧"})
                    continue
                # 错帧的深度不写入；跳过并记录，用户可先用“统计帧数”检查全部 episode。
                misaligned = alignment_issues(episode, mapping, plan["starts"])
                if misaligned:
                    skipped.append({"episode_id": episode_id, "reason": f"RGB 与深度图像切换行不一致：{'；'.join(misaligned)}"})
                    continue
                task = params.get("task") or episode.metadata()["language_instruction"]
                over_range = 0
                for index, row in enumerate(plan["rows"]):
                    if index % CANCEL_CHECK_FRAMES == 0:
                        reporter.check_cancel()
                    frame = {
                        "observation.state": plan["state"][index].astype(np.float32),
                        "action": plan["action"][index].astype(np.float32),
                        "task": task,
                    }
                    for camera, target in mapping["cameras"].items():
                        depth = episode.read_depth(camera, int(row))
                        over_range += int((depth > DEPTH_MAX_MM).sum())
                        frame[target["rgb_feature"]] = episode.read_color(camera, int(row))
                        frame[target["depth_feature"]] = depth[..., None]
                    dataset.add_frame(frame)
                save_episode(dataset)
                episode_index = len(episodes)
            episodes.append({
                "episode_index": episode_index,
                "episode_id": episode_id,
                "task": task,
                "images": plan["images"],
                "frames": len(plan["rows"]),
                "depth_over_range_pixels": over_range,
            })
            reporter.progress(number + 1, len(episode_ids), f"{episode_id}: {len(plan['rows'])} 帧")
    finally:
        # finalize 幂等，取消或失败时同样调用以释放文件句柄，否则 Windows 上临时目录无法删除。
        dataset.finalize()
    written = {"episodes": episodes, "skipped": skipped}
    return written


def validate_output(root, params, mapping, written):
    """读回输出并与源数据核对。数值全量比较；图像每个 episode 抽查首、中、末帧。"""
    access = DatasetAccess()
    reader = open_dataset(root, repo_id=local_repo_id(params))
    data = pd.read_parquet(root / "data")
    episodes = written["episodes"]
    checks = Checks()
    check = checks.add
    expected_frames = sum(item["frames"] for item in episodes)
    check("episode 数", reader.meta.total_episodes == len(episodes), f"{reader.meta.total_episodes} / {len(episodes)}")
    check("总帧数", reader.meta.total_frames == expected_frames == len(data), f"meta {reader.meta.total_frames}，parquet {len(data)}，期望 {expected_frames}")
    check("fps", reader.meta.fps == mapping["fps"], str(reader.meta.fps))
    failures = official_read_failures(root, reader.meta)
    check("官方读取每个 episode 各路视频首帧", not failures, str(failures[:5]))
    checks.standard_dirs(root)

    precision_error = 0.0
    rgb_errors = []
    depth_mismatch = 0
    for item in episodes:
        rows = data[data["episode_index"] == item["episode_index"]].sort_values("frame_index")
        with access.open_episode(params["dataset_path"], item["episode_id"]) as episode:
            plan = episode_plan(episode, mapping)
            state = np.stack(rows["observation.state"].to_numpy())
            action = np.stack(rows["action"].to_numpy())
            frame_index = rows["frame_index"].to_numpy()
            label = item["episode_id"]
            check(f"{label} 帧序号连续", np.array_equal(frame_index, np.arange(item["frames"])))
            check(f"{label} 时间戳 = frame_index / fps", np.allclose(rows["timestamp"].to_numpy(), frame_index / mapping["fps"], atol=1e-6))
            check(f"{label} state 与源 puppet 一致", np.array_equal(state, plan["state"].astype(np.float32)))
            check(f"{label} action 与下一帧 puppet 一致", np.array_equal(action, plan["action"].astype(np.float32)))
            check(f"{label} action[t] = state[t+1]", np.array_equal(action[:-1], state[1:]))
            precision_error = max(precision_error, float(np.abs(state.astype(np.float64) - plan["state"]).max()))

            first_index = int(rows["index"].iloc[0])
            for pick in sorted({0, item["frames"] // 2, item["frames"] - 1}):
                row = int(plan["rows"][pick])
                sample = reader[first_index + pick]
                for camera, target in mapping["cameras"].items():
                    source_depth = episode.read_depth(camera, row)
                    depth = to_depth_mm(sample[target["depth_feature"]])
                    depth_mismatch += int((depth != np.minimum(source_depth, DEPTH_MAX_MM)).sum())
                    color = to_rgb_uint8(sample[target["rgb_feature"]])
                    rgb_errors.append(float(np.abs(color.astype(np.int16) - episode.read_color(camera, row)).mean()))
    check("抽查帧深度与源一致（超过 4095 mm 的像素按截断值比较）", depth_mismatch == 0, f"不一致像素 {depth_mismatch}")
    worst = max(rgb_errors, default=0.0)
    check(f"抽查帧 RGB 平均误差不超过 {RGB_ERROR_LIMIT:g}（0–255）", worst <= RGB_ERROR_LIMIT, f"最大 {worst:.2f}")
    summary = {
        **checks.summary(),
        "state_float32_max_abs_error": precision_error,
        "rgb_mean_abs_error": round(float(np.mean(rgb_errors)), 3) if rgb_errors else None,
        "rgb_max_mean_abs_error": round(float(np.max(rgb_errors)), 3) if rgb_errors else None,
        "sampled_frames": len(rgb_errors),
    }
    return summary


def conversion_report(params, mapping, written, validation):
    """来源与语义记录，保存在任务结果中（不写入输出目录，保持 LeRobot 数据集只含标准文件）。"""
    report = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "source_dataset": params["dataset_path"],
        "target": "lerobot_v3.0",
        "lerobot_version": LEROBOT_VERSION,
        "repo_id": local_repo_id(params),
        "load": "LeRobotDataset(repo_id, root=<数据集目录>)；repo_id 不写入数据集文件，本地读取以 root 为准",
        "mapping": mapping,
        "semantics": {
            "observation.state": "当前输出帧的 puppet 关节值，float64 → float32",
            "action": "下一输出帧（下一张相机图像起始行）的 puppet 关节值，float64 → float32",
            "frames": "按相机图像起始行取帧，数值不重采样；每个 episode 最后一帧无下一帧而丢弃",
            "timestamp": "LeRobot 按 frame_index / fps 生成；原始整数秒时间戳与干预标记不写入",
            "rgb": "LeRobot 视频有损编码（参数见 meta/info.json 中的 video.* 字段）",
            "depth": f"LeRobot 深度线性量化 0–{DEPTH_MAX_MM} mm、每级 1 mm，HEVC 无损，≤{DEPTH_MAX_MM} mm 时与源一致；超出部分截断为 {DEPTH_MAX_MM}（不可恢复），像素数见 episodes",
        },
        "episodes": written["episodes"],
        "skipped_episodes": written["skipped"],
        "validation": validation,
    }
    return report


def build_v3(root, params, mapping, reporter):
    """H5 → v3.0 写入 root 并验证；验证未通过时抛出异常。"""
    written = write_lerobot(root, params, mapping, reporter)
    remove_empty_image_dir(root)
    reporter.set_status("validating", "验证输出")
    validation = validate_output(root, params, mapping, written)
    require_passed(validation)
    result = (written, validation)
    return result


def convert_job(params, reporter):
    """工作进程中运行：写入临时目录 → 验证 → 重命名发布 → 登记。失败或取消时删除临时目录。"""
    mapping = load_mapping(params["mapping_path"])
    with staged_output(params["output_path"], reporter) as (temp, output):
        written, validation = build_v3(temp, params, mapping, reporter)
        publish(temp, output, reporter)
    dataset = reporter.register_dataset(output.name, output, LEROBOT_V3)
    result = {
        "output_path": str(output),
        "registered_dataset_id": dataset["id"],
        "episode_count": len(written["episodes"]),
        "frames": sum(item["frames"] for item in written["episodes"]),
        "skipped_episodes": written["skipped"],
        "depth_over_range_pixels": sum(item["depth_over_range_pixels"] for item in written["episodes"]),
        "validation": validation,
        "report": conversion_report(params, mapping, written, validation),
    }
    return result


def h5_to_v21_job(params, reporter):
    """工作进程中运行：H5 → 临时 v3.0（写入并验证）→ v2.1（写入并验证）→ 发布 → 登记 v2.1。临时 v3.0 始终删除。"""
    mapping = load_mapping(params["mapping_path"])
    with staged_output(params["output_path"], reporter) as (temp, output):
        # 中间 v3.0 的临时目录，无论成功失败都删除。
        work = temporary_path(output.parent / f"{output.name}-via-v3", reporter.task_id)
        try:
            written, v3_validation = build_v3(work, {**params, "output_path": str(work)}, mapping, reporter)
            reporter.check_cancel()
            reporter.set_status("running", "由 v3.0 写出 v2.1")
            meta = load_metadata(work)
            v21_params = {"dataset_path": str(work), "output_path": str(output), "episode_count": meta.total_episodes,
                          "dropped_features": dropped_features(meta, LEROBOT_V21)}
            features = v21_features(meta.features, set(v21_params["dropped_features"]))
            written_v21 = write_v21(temp, v21_params, meta, features, reporter)
            reporter.set_status("validating", "验证 v2.1 输出")
            validation = validate_v21_output(temp, v21_params, meta, features, written_v21)
            require_passed(validation, "v2.1 输出")
            publish(temp, output, reporter)
        finally:
            remove_temporary(work)
    dataset = reporter.register_dataset(output.name, output, LEROBOT_V21)
    result = {
        "output_path": str(output),
        "registered_dataset_id": dataset["id"],
        "episode_count": len(written["episodes"]),
        "frames": sum(item["frames"] for item in written["episodes"]),
        "skipped_episodes": written["skipped"],
        "depth_over_range_pixels": sum(item["depth_over_range_pixels"] for item in written["episodes"]),
        "dropped_features": v21_params["dropped_features"],
        "v3_validation": v3_validation,
        "validation": validation,
        "report": {
            **conversion_report({**params, "output_path": str(output)}, mapping, written, v3_validation),
            "target": LEROBOT_V21,
            "via": "临时 v3.0（写入并验证后删除）",
            "v21_notes": version_notes(LEROBOT_V21, v21_params["dropped_features"], meta),
        },
    }
    return result


def frame_count_job(params, reporter):
    """工作进程中运行：按相机图像取帧，统计每个 episode 的图像数、输出帧数与 RGB/深度边界是否一致。"""
    access = DatasetAccess()
    mapping = load_mapping(params["mapping_path"])
    camera = next(iter(mapping["cameras"]))
    episode_ids = params["episode_ids"]
    episodes = []
    for index, episode_id in enumerate(episode_ids):
        reporter.check_cancel()
        with access.open_episode(params["dataset_path"], episode_id) as episode:
            color_starts = episode.image_change_indices("color", camera)
            depth_starts = episode.image_change_indices("depth", camera)
            episodes.append({
                "episode_id": episode_id,
                "rows": episode.length,
                "images": len(color_starts),
                "output_frames": output_frame_count(len(color_starts), mapping),
                "depth_aligned": bool(np.array_equal(color_starts, depth_starts)),
            })
        reporter.progress(index + 1, len(episode_ids), episode_id)
    result = {
        "dataset_id": params["dataset_id"],
        "episode_count": len(episodes),
        "rows": sum(item["rows"] for item in episodes),
        "images": sum(item["images"] for item in episodes),
        "output_frames": sum(item["output_frames"] for item in episodes),
        "zero_frame_episodes": [item["episode_id"] for item in episodes if item["output_frames"] == 0],
        "depth_misaligned_episodes": [item["episode_id"] for item in episodes if not item["depth_aligned"]],
        "episodes": episodes,
    }
    return result


DEMO_OUTPUT = REPO_ROOT / "output" / "demo" / "data_converter"


def demo_data_converter():
    from component.common.dataset_access import DatasetAccess
    from component.dataset.dataset_manager import DatasetManager
    from util.storage_helper import Storage

    db_path = DEMO_OUTPUT / "demo.sqlite3"
    db_path.unlink(missing_ok=True)
    access = DatasetAccess()
    manager = DatasetManager(Storage(db_path), access, REPO_ROOT / "data")
    converter = DataConverter(manager, access, None, DEMO_OUTPUT / "output")
    dataset = manager.register("place_red_pepper_on_red_tray")

    preview = converter.preview(dataset["id"], sample_episodes=2)
    layout = preview["layout"]
    logger.info("Target={}, fps={}, action={}+{} frame, state={}", preview["target"], preview["fps"],
                preview["mapping"]["action"]["source"], preview["mapping"]["action"]["offset_frames"], preview["mapping"]["state"]["source"])
    logger.info("Vector size={}", layout["size"])
    for entry in layout["entries"]:
        logger.info("  {} dims={} unit={} slice={}", entry["field"], entry["dims"], entry["unit"], entry["slice"])
    logger.info("Episodes: total={}, convertible={}, blocked={}", preview["episode_count"], preview["convertible_count"], len(preview["blocked_episodes"]))
    logger.info("Rows total={}, rows/frame={}, estimated images={}, output frames={}", preview["total_rows"], preview["rows_per_frame"], preview["estimated_images"], preview["estimated_frames"])
    logger.info("Samples: {}", preview["samples"])
    logger.info("Output={} exists={} free={:.1f} GB", preview["output_path"], preview["output_exists"], preview["free_bytes"] / 1e9)
    logger.info("Blocking: {}; ready={}", preview["blocking"], preview["ready"])

    try:
        converter.preview(dataset["id"], target="h5")
        raise AssertionError("unsupported target should be rejected")
    except ValueError as error:
        logger.info("Rejected: {}", error)


def demo_convert_sample():
    """小样本：转换真实数据集的前 2 个 episode 到独立输出目录，不覆盖已有结果。"""
    import time

    from component.common.task_manager import DemoReporter
    from component.dataset.dataset_manager import DatasetManager
    from util.storage_helper import Storage

    db_path = DEMO_OUTPUT / "convert.sqlite3"
    db_path.unlink(missing_ok=True)
    output_dir = DEMO_OUTPUT / "converted"
    shutil.rmtree(output_dir, ignore_errors=True)
    access = DatasetAccess()
    manager = DatasetManager(Storage(db_path), access, REPO_ROOT / "data")
    converter = DataConverter(manager, access, None, output_dir)
    dataset = manager.register("place_red_pepper_on_red_tray")
    episodes = [episode for episode in access.list_episodes(dataset["path"]) if episode["status"] != "invalid"][:2]
    params = {
        "dataset_id": dataset["id"],
        "dataset_path": dataset["path"],
        "episode_ids": [episode["id"] for episode in episodes],
        "mapping_path": str(converter.mapping_path),
        "output_path": str(converter.output_path(dataset, output_name="sample_2_episodes")),
        "task": None,
    }
    started = time.monotonic()
    result = convert_job(params, DemoReporter())
    elapsed = time.monotonic() - started
    validation = result["validation"]
    logger.info("Converted {} episodes, {} frames in {:.1f}s -> {}", result["episode_count"], result["frames"], elapsed, result["output_path"])
    logger.info("Validation passed={} checks={} failed={}", validation["passed"], validation["check_count"], validation["failed"])
    logger.info("float32 max error={:.3g}, RGB mean abs error={} (max {}), sampled frames={}, depth over-range pixels={}",
                validation["state_float32_max_abs_error"], validation["rgb_mean_abs_error"], validation["rgb_max_mean_abs_error"],
                validation["sampled_frames"], result["depth_over_range_pixels"])
    for path in sorted(Path(result["output_path"]).rglob("*")):
        if path.is_file():
            logger.info("  {} {:.1f} KB", path.relative_to(result["output_path"]), path.stat().st_size / 1024)


def main():
    demo_data_converter()
    demo_convert_sample()


if __name__ == "__main__":
    main()
