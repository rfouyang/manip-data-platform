# 机器人数据平台

当前完成第 1–6 步，并完成第 7 步自检（转换映射与预检，尚未执行实际转换）：服务入口、Preline UI 页面、H5 只读访问、数据集注册/列表/详情/删除登记，以及 H5 可视化（逐帧 RGB/深度、自动播放、state/action 曲线与当前帧数值）已可运行。转换、拆分合并尚未实现。

## 环境

在仓库根目录使用 PowerShell：

```powershell
uv sync --locked
uv run python --version
```

项目使用 Python 3.12，虚拟环境位于 `.venv`，依赖版本记录在 `uv.lock`。无需手动激活环境。

IDE 解释器选择项目内 `.venv\Scripts\python.exe`。

## 配置

首次配置可复制 `.env.example` 到 `.env`，按需修改；不创建 `.env` 也可使用默认值。

- 默认监听 `127.0.0.1:8000`。
- FPS 不在这里配置：H5 转换使用映射文件 `config/x_humanoid.json` 中的 fps，LeRobot 数据集使用自身记录的 fps。
- 服务日志由 loguru 同时输出到终端和 `MANIP_LOG_DIR/manip_YYYY-MM-DD.log`（每日轮转、保留 30 天），级别由 `MANIP_LOG_LEVEL` 控制，默认 INFO。
- 数据集登记保存在 `MANIP_DB_PATH`（默认 `output/manip.sqlite3`）；页面中填写的相对路径以 `MANIP_DATA_DIR` 为基准。
- `MANIP_DATA_DIR`、`MANIP_OUTPUT_DIR`、`MANIP_LOG_DIR` 支持绝对路径；相对路径以仓库根目录为基准。
- 系统环境变量优先于 `.env`；构造函数显式参数优先于环境变量。

## 启动与自检

```powershell
uv run python -m app.main
```

打开 <http://127.0.0.1:8000/> 查看 UI，<http://127.0.0.1:8000/docs> 查看 API，<http://127.0.0.1:8000/api/health> 查看健康状态。按 Ctrl+C 停止服务。

当前 UI 参考 Preline 官方 Admin Dashboard 模板布局（左侧侧边栏 + 顶部 header），通过 Tailwind CSS 4 官方浏览器 CDN 生成样式，并加载 preline.js 5.0.0 用于小屏侧边栏 offcanvas。此环境有 Node 但没有 npm，因此本步骤没有引入本地前端构建；离线部署需求明确后再切换。

```powershell
uv run python -m config.settings
uv run python -m app.context
uv run python util/h5_helper.py
uv run pytest
```

所有文件可在 IDE 中右键直接运行（PyCharm 默认把项目根目录加入 PYTHONPATH）；命令行则从仓库根目录用上述命令运行。服务 demo 使用当前 Starlette 推荐的 dev 依赖 httpx2。

## 结构与进度

- `app/main.py`：服务启动和关闭。
- `app/context.py`：顶层对象；本步持有配置，后续在此实例化实际业务组件。
- `app/ui_manip/`、`app/api_manip/`：按业务预留目录。
- `component/common/`：共享逻辑；其他业务各一个子目录。
- `component/common/dataset_access.py`、`component/dataset/dataset_manager.py`、`app/api_manip/dataset/api.py`：数据集格式识别、登记、列表和详情。
- `component/visualization/data_visualizer.py`、`app/api_manip/visualization/api.py`：逐帧 RGB/深度查看、曲线与当前帧数值。
- `component/conversion/data_converter.py`、`app/api_manip/conversion/api.py`、`config/x_humanoid.json`：转换映射与预检。
- `util/storage_helper.py`：SQLite 登记存储。
- `util/h5_helper.py`：H5 episode 发现与只读访问（数值切片、RGB/深度按帧解码）。其余 helper 按后续步骤实现。
- `config/settings.py`：运行配置。
- `tools/robomind_batch_convert.py`：RoboMIND 2.0 天轶任务分批转换（每批约 10 GB H5 → v3 → 删 H5 → 合并），本地放不下整个任务的原始数据时使用。
- 代码约定见 [AGENTS.md](AGENTS.md)，计划见 [doc/plan.md](doc/plan.md)，进度见 [doc/current_state.md](doc/current_state.md)。

每一步先自检，再由用户检查；用户明确回复 OK 后才进入下一步。
