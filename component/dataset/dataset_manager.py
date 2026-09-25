from pathlib import Path

import numpy as np
from loguru import logger

from component.common.dataset_access import LEROBOT_V3


class DatasetManager:
    """数据集登记、列表与详情。登记信息存 SQLite，episode 内容由 DatasetAccess 从源文件读取。"""

    def __init__(self, storage, dataset_access, data_dir):
        self.storage = storage
        self.dataset_access = dataset_access
        # 相对路径以数据目录为基准，方便直接填写 data 下的子目录名。
        self.data_dir = Path(data_dir)

    def resolve_path(self, path):
        path = (self.data_dir / Path(path).expanduser()).resolve()
        if not path.is_dir():
            raise ValueError(f"目录不存在: {path}")
        return path

    def register(self, path, **kwargs):
        root = self.resolve_path(path)
        existing = self.storage.find_dataset_by_path(root)
        if existing is not None:
            raise FileExistsError(f"该目录已登记为数据集 {existing['id']}（{existing['name']}）")
        dataset_format = self.dataset_access.detect_format(root)
        name = kwargs.get("name") or root.name
        dataset = self.storage.add_dataset(name, root, dataset_format)
        logger.info("Registered dataset {}: {} ({})", dataset["id"], root, dataset_format)
        return dataset

    def list(self):
        """登记列表；LeRobot 数据集附带 episode 数与帧数（读 info.json）。H5 需逐个打开 episode 文件才能统计，列表中不显示。"""
        from util.lerobot_helper import info_summary

        datasets = self.storage.list_datasets()
        for dataset in datasets:
            dataset["summary"] = None if dataset["format"] == "h5" else info_summary(dataset["path"])
        return datasets

    def get(self, dataset_id):
        dataset = self.storage.get_dataset(dataset_id)
        if dataset is None:
            raise LookupError(f"数据集不存在: {dataset_id}")
        return dataset

    def get_v3(self, dataset_id):
        """tag、拆分、子任务只处理 LeRobot v3.0 数据集。"""
        dataset = self.get(dataset_id)
        if dataset["format"] != LEROBOT_V3:
            raise ValueError(f"只支持 LeRobot v3.0 数据集，该数据集格式为 {dataset['format']}")
        return dataset

    def remove(self, dataset_id):
        """只删除登记记录，不删除或修改源数据目录。"""
        dataset = self.get(dataset_id)
        self.storage.delete_dataset(dataset_id)
        logger.info("Removed dataset registration {}: {} (source files untouched)", dataset_id, dataset["path"])
        return dataset

    def inspect(self, dataset_id):
        dataset = self.get(dataset_id)
        root = dataset["path"]
        episodes = self.dataset_access.list_episodes(root)
        readable = [episode for episode in episodes if episode["status"] != "invalid"]
        problems = [episode for episode in episodes if episode["status"] != "ok"]
        tasks = sorted({episode["task"] for episode in readable if episode["task"]})
        total_frames = sum(episode["length"] for episode in readable)
        description = self.dataset_access.read_description(root)

        # 字段与相机信息取自第一个可读 episode；全量结构一致性在转换预检中核对。
        schema = None
        if readable:
            first = self.dataset_access.read_episode(root, readable[0]["id"])
            schema = {"source_episode": first["id"], "cameras": first["cameras"], "series": first["series"]}

        detail = {
            **dataset,
            "description": description,
            "episode_count": len(episodes),
            "readable_count": len(readable),
            "problem_count": len(problems),
            "total_frames": total_frames,
            "tasks": tasks,
            "schema": schema,
            "episodes": episodes,
        }
        return detail

    def episode(self, dataset_id, episode_id):
        dataset = self.get(dataset_id)
        info = self.dataset_access.read_episode(dataset["path"], episode_id)
        return info

    def preview(self, dataset_id, **kwargs):
        """字段定义、任务列表与第一个 episode 的前几行原始数据（默认 5 行），只读取少量数据，供编写正则、选择字段前查看。"""
        dataset = self.get(dataset_id)
        count = int(kwargs.get("rows", DEFAULT_PREVIEW_ROWS))
        if dataset["format"] == "h5":
            preview = h5_preview(self.dataset_access, dataset["path"], count)
        else:
            preview = lerobot_preview(dataset["path"], count)
        preview = {"id": dataset["id"], "name": dataset["name"], "format": dataset["format"], **preview}
        return preview


DEFAULT_PREVIEW_ROWS = 5


def cell_value(value):
    """表格单元：数组转为列表，浮点保留 6 位有效数字，字节解码为文本。"""
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    elif isinstance(value, np.ndarray) or isinstance(value, list):
        value = [cell_value(item) for item in np.asarray(value).tolist()]
    elif isinstance(value, (float, np.floating)):
        value = float(f"{float(value):.6g}")
    elif isinstance(value, np.generic):
        value = value.item()
    return value


def language_cell(value):
    """语言标注列（每帧若干行）紧凑显示为“style@秒: 内容”，用 | 分隔。"""
    items = [] if value is None else list(value)
    text = " | ".join(f"{item.get('style') or item.get('role')}@{float(item['timestamp']):.2f}s: {item.get('content') or ''}" for item in items)
    return text


def lerobot_preview(root, count):
    from util.lerobot_helper import head_rows, load_metadata, task_names

    meta = load_metadata(root)
    names = task_names(meta)
    lengths = [int(meta.episodes[index]["length"]) for index in range(meta.total_episodes)]
    tasks = []
    for task_index, task in sorted(names.items()):
        members = [index for index in range(meta.total_episodes) if task in meta.episodes[index]["tasks"]]
        tasks.append({"task_index": task_index, "task": task, "episodes": len(members), "frames": sum(lengths[index] for index in members)})
    fields = []
    for key, feature in meta.features.items():
        info = feature.get("info") or {}
        fields.append({
            "name": key,
            "dtype": feature["dtype"],
            "shape": list(feature["shape"]),
            "names": feature.get("names"),
            "detail": {name: info[name] for name in ("video.codec", "video.pix_fmt", "video.fps", "is_depth_map") if name in info},
        })
    table = head_rows(root, meta, count)
    columns = [key for key in meta.features if key in table.columns]
    rows = []
    for _, row in table.iterrows():
        values = {key: language_cell(row[key]) if meta.features[key]["dtype"] == "language" else cell_value(row[key]) for key in columns}
        values["task"] = names.get(int(row["task_index"]))
        rows.append(values)
    preview = {
        "summary": {"fps": meta.fps, "robot_type": meta.robot_type, "episodes": meta.total_episodes, "frames": meta.total_frames, "tasks": len(tasks)},
        "fields": fields,
        "tasks": tasks,
        "row_source": "episode 0",
        "columns": [*columns, "task"],
        "rows": rows,
        "note": "视频 feature 不在数据表中；task 列为 task_index 对应的任务描述。",
    }
    return preview


def h5_preview(access, root, count):
    episodes = [episode for episode in access.list_episodes(root)[:20] if episode["status"] != "invalid"]
    if not episodes:
        raise ValueError("没有可读取的 episode")
    with access.open_episode(root, episodes[0]["id"]) as episode:
        info = episode.info()
        fields = [{"name": name, "dtype": series["dtype"], "shape": series["shape"][1:], "names": series.get("names"), "detail": {}}
                  for name, series in info["series"].items()]
        for camera, item in info["cameras"].items():
            fields.append({"name": f"camera_observations/color_images/{camera}", "dtype": "jpeg", "shape": item["color_resolution"], "names": None, "detail": {}})
            if item.get("depth_resolution"):
                fields.append({"name": f"camera_observations/depth_images/{camera}", "dtype": "png", "shape": item["depth_resolution"], "names": None, "detail": {}})
        stop = min(count, episode.length)
        timestamps = episode.read_timestamps(0, stop)
        series = {name: episode.read_series(name, 0, stop) for name in info["series"] if not info["series"][name]["empty"]}
        rows = []
        for index in range(stop):
            values = {"row": index, "timestamp": cell_value(timestamps[index])}
            values.update({name: cell_value(data[index]) for name, data in series.items()})
            rows.append(values)
        metadata = {key: cell_value(value) for key, value in info["metadata"].items()}
    task = metadata.get("language_instruction")
    preview = {
        "summary": {"episodes_checked": len(episodes), "metadata": metadata},
        "fields": fields,
        "tasks": [{"task_index": None, "task": task, "episodes": None, "frames": None}] if task else [],
        "row_source": episodes[0]["id"],
        "columns": ["row", "timestamp", *series],
        "rows": rows,
        "note": "H5 的一行是一次数值采样（约 190 Hz），图像只在部分行更新；任务描述来自 metadata.language_instruction。",
    }
    return preview


REPO_ROOT = Path(__file__).resolve().parents[2]
DEMO_OUTPUT = REPO_ROOT / "output" / "demo" / "dataset_manager"


def demo_dataset_manager():
    from component.common.dataset_access import DatasetAccess
    from util.storage_helper import Storage

    db_path = DEMO_OUTPUT / "demo.sqlite3"
    db_path.unlink(missing_ok=True)
    manager = DatasetManager(Storage(db_path), DatasetAccess(), REPO_ROOT / "data")

    dataset = manager.register("place_red_pepper_on_red_tray")
    detail = manager.inspect(dataset["id"])
    logger.info(
        "Dataset {}: episodes={}, readable={}, problems={}, frames={}",
        detail["name"], detail["episode_count"], detail["readable_count"], detail["problem_count"], detail["total_frames"],
    )
    logger.info("Tasks: {}", detail["tasks"])
    logger.info("Description: {}", detail["description"])
    first_id = detail["episodes"][0]["id"]
    episode = manager.episode(dataset["id"], first_id)
    logger.info("Episode {}: {} frames, issues={}", first_id, episode["length"], episode["issues"])

    removed = manager.remove(dataset["id"])
    assert manager.list() == [] and Path(removed["path"]).is_dir()
    dataset = manager.register("place_red_pepper_on_red_tray")

    for bad_path, expected in (("place_red_pepper_on_red_tray", FileExistsError), ("no_such_dir", ValueError)):
        try:
            manager.register(bad_path)
            raise AssertionError(f"{bad_path} should be rejected")
        except expected as error:
            logger.info("Rejected {}: {}", bad_path, error)


def main():
    demo_dataset_manager()


if __name__ == "__main__":
    main()
