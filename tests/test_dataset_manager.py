import pytest

from component.common.dataset_access import DatasetAccess
from component.dataset.dataset_manager import DatasetManager
from tests.test_h5_helper import write_episode
from util.storage_helper import Storage


def make_dataset(root):
    """两个正常 episode 和一个 Git LFS 指针文件，目录形态与采集样本一致。"""
    for name in ("0001", "0002"):
        episode_dir = root / "success_episodes" / name / "data"
        episode_dir.mkdir(parents=True)
        write_episode(episode_dir / "trajectory.hdf5")
    pointer_dir = root / "success_episodes" / "0003" / "data"
    pointer_dir.mkdir(parents=True)
    (pointer_dir / "trajectory.hdf5").write_text("version https://git-lfs.github.com/spec/v1\n", encoding="utf-8")


@pytest.fixture
def manager(tmp_path):
    data_dir = tmp_path / "data"
    make_dataset(data_dir / "demo_task")
    manager = DatasetManager(Storage(tmp_path / "test.sqlite3"), DatasetAccess(), data_dir)
    return manager


def test_register_and_inspect(manager):
    dataset = manager.register("demo_task")
    detail = manager.inspect(dataset["id"])
    statuses = {episode["id"]: episode["status"] for episode in detail["episodes"]}
    assert statuses == {
        "success_episodes/0001": "ok",
        "success_episodes/0002": "ok",
        "success_episodes/0003": "invalid",
    }
    assert detail["readable_count"] == 2
    assert detail["total_frames"] == 8
    invalid = next(episode for episode in detail["episodes"] if episode["status"] == "invalid")
    assert "Git LFS" in invalid["error"]


def test_register_rejects_duplicate_missing_and_unsupported(manager):
    manager.register("demo_task")
    with pytest.raises(FileExistsError):
        manager.register("demo_task")
    with pytest.raises(ValueError):
        manager.register("no_such_dir")

    lerobot_root = manager.data_dir / "lerobot_demo"
    (lerobot_root / "meta").mkdir(parents=True)
    # 支持 v3.0 与 v2.1；其他版本（如 v2.0）拒绝登记。
    (lerobot_root / "meta" / "info.json").write_text('{"codebase_version": "v2.0"}', encoding="utf-8")
    with pytest.raises(ValueError, match="LeRobot v2.0"):
        manager.register("lerobot_demo")
    assert len(manager.list()) == 1


def test_episode_lookup_stays_inside_dataset(manager):
    dataset = manager.register("demo_task")
    with pytest.raises(LookupError):
        manager.episode(dataset["id"], "../../outside")
    with pytest.raises(ValueError):
        manager.episode(dataset["id"], "success_episodes/0003")


def test_remove_only_deletes_registration(manager):
    dataset = manager.register("demo_task")
    manager.remove(dataset["id"])
    assert manager.list() == []
    assert (manager.data_dir / "demo_task" / "success_episodes" / "0001" / "data" / "trajectory.hdf5").is_file()
    with pytest.raises(LookupError):
        manager.remove(dataset["id"])
    # 删除后可重新登记同一目录。
    manager.register("demo_task")


def test_preview_h5_fields_and_first_rows(manager):
    dataset = manager.register("demo_task")
    preview = manager.preview(dataset["id"], rows=3)
    assert preview["format"] == "h5" and preview["row_source"] == "success_episodes/0001"
    assert len(preview["rows"]) == 3 and preview["rows"][0]["row"] == 0
    assert all(column in preview["rows"][0] for column in preview["columns"])
    assert any(field["name"].startswith("camera_observations/color_images/") for field in preview["fields"])


def test_preview_lerobot_fields_tasks_and_first_rows(tmp_path):
    from tests.test_data_tagger import TASKS, make_tasks_v3

    make_tasks_v3(tmp_path / "v3")
    manager = DatasetManager(Storage(tmp_path / "test.sqlite3"), DatasetAccess(), tmp_path)
    preview = manager.preview(manager.register(tmp_path / "v3")["id"])
    assert preview["summary"]["episodes"] == 4 and preview["summary"]["tasks"] == 3
    assert [task["task"] for task in preview["tasks"]] == [TASKS[0], TASKS[1], TASKS[3]]
    assert [task["episodes"] for task in preview["tasks"]] == [2, 1, 1]
    assert [field["name"] for field in preview["fields"]][:2] == ["observation.state", "observation.images.cam"]
    # 前 5 行来自 episode 0（3 帧），附带任务描述；数组转为列表。
    assert [row["frame_index"] for row in preview["rows"]] == [0, 1, 2]
    assert preview["rows"][1]["observation.state"] == [0.0, 1.0] and preview["rows"][0]["task"] == TASKS[0]


def test_list_shows_lerobot_size_only(manager):
    from tests.test_data_tagger import make_tasks_v3

    make_tasks_v3(manager.data_dir / "v3")
    manager.register("demo_task")
    manager.register("v3")
    # make_tasks_v3：4 个 episode，3 + 4 + 5 + 6 帧；H5 需逐个打开文件才能统计，不显示。
    assert [item["summary"] for item in manager.list()] == [None, {"episodes": 4, "frames": 18}]
    # 登记后目录被移走：该项没有规模信息，列表照常返回。
    (manager.data_dir / "v3").rename(manager.data_dir / "v3_moved")
    assert manager.list()[1]["summary"] is None


def main():
    import sys

    sys.exit(pytest.main([__file__, "-q"]))


if __name__ == "__main__":
    main()
