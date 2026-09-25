# 机器人数据平台实施计划

更新日期：2026-09-24。当前阶段：第 1–14 步已由用户确认（v3 → H5 按用户要求取消并已移除；第 14 步于 2026-09-25 确认）；第 15、16 步（代码重构、界面重设计）已完成，待用户检查，原第 15 步顺延为第 17 步（用户确认，2026-09-24）。转换输出只含 LeRobot 标准文件（用户要求，2026-09-23），深度只写 LeRobot 深度 feature。

## 逐步实施与验收约定

执行顺序为：实现一步 → 自检 → 用户检查 → 用户明确回复 OK → 下一步。存在问题只修改当前步骤并重新检查，不自动推进。

| 步骤 | 内容 | 状态 |
| --- | --- | --- |
| 1 | uv 虚拟环境、依赖、配置、main/context、业务目录 | 用户已确认 OK |
| 2 | Preline UI 页面框架、四个导航 panel | 用户已确认 OK |
| 3 | H5 读取 helper 与 demo | 用户已确认 OK |
| 4 | 数据集注册、列表、详情、删除登记 | 用户已确认 OK |
| 5 | H5 RGB/深度逐帧显示、分辨率信息、自动播放 | 用户已确认 OK |
| 6 | 时间轴与 state/action 曲线联动（播放已在第 5 步提前实现） | 用户已确认 OK |
| 7 | 转换映射和预检，确认 action/state、单位、深度策略 | 用户已确认 OK |
| 8 | 后台任务、进度、取消与重试（首个任务：全量取帧统计） | 用户已确认 OK |
| 9 | H5 → v3，小样本转换与验证 | 用户已确认 OK |
| 10 | v3 可视化、转换产物自动登记（v3 → H5 已按用户要求取消） | 用户已确认 OK |
| 11 | v2.1 支持：v3 ↔ v2.1 相互转换、v2.1 读取与可视化 | 用户已确认 OK |
| 12 | 由任务描述生成列（tag）；v3 拆分（按条件抽取子数据集） | 用户已确认 OK |
| 13 | v3 合并 | 用户已确认 OK |
| 14 | 子任务：按时间线编辑子任务（LeRobot 0.6 语言标注）、修改 task、子任务转逐帧 task、现有功能适配语言列 | 用户已确认 OK |
| 15 | 代码重构（行为不变）：15a 合并重复逻辑、删除无用代码；15b 拆分大文件与长函数、统一 API 异常处理 | 15a 用户已确认 OK；15b 已自检，待用户检查 |
| 16 | 界面重设计：16a 导航、页面模板、文案规范与“数据拆分”样板页；16b 其余页面 | 已自检，待用户检查（用户要求连续完成，次日统一检查） |
| 17 | 批量、异常恢复与整体验收 | 未开始 |

下面 P0–P5 是整体技术阶段，实际交付与停止点以上述逐步验收表为准。每一步更新 doc/current_state.md。

## 1. 目标与范围

第一版实现：

1. 格式转换（用户确认，2026-09-23）：H5 → v3、v3 → v2.1、v2.1 → v3。不做 v3 → H5、v2.1 → H5。H5 → v2.1 由平台自动经临时 v3.0 完成，只保留 v2.1（用户确认，2026-09-24）。
2. 三种格式统一可视化：RGB、深度、state/action 曲线、逐帧与播放。
3. LeRobot v3 数据集按 episode 拆分与合并。
4. 本地数据集注册、转换预检、后台进度、取消和重试。

在线机器人采集接入留到后续阶段。源数据只读，结果写入新目录，不默认上传外部服务。

技术选择：FastAPI + Jinja2 + 模块化 JavaScript + Preline UI/Tailwind；曲线使用 ECharts；Python 依赖用 uv 管理。

## 2. 已知样本与待确认语义

目录统计：293 个 trajectory.hdf5，约 81.8 GB。第 3 步全量结构扫描：292 个可读且 schema 完全一致（单相机 camera_top），共 398,769 行，每个 episode 868–2381 行，逐帧字段长度均与 trajectory_length 一致；`0106_104528` 是 137 字节的 Git LFS 指针文件，数据未下载。

- 目录形态：`<任务>/success_episodes/<episode>/data/trajectory.hdf5`。
- RGB：`camera_observations/color_images/camera_top`，JPEG，640×480（resolution 字段顺序为 [height, width]）。`camera_color_channel=rgb`：采集端把 RGB 数组直接交给 OpenCV 编码，cv2.imdecode 结果即为 RGB，不能再做 BGR→RGB（样本帧目视核对：红色托盘/辣椒）。
- 深度：`camera_observations/depth_images/camera_top`，16 位 PNG，640×480。
- master/puppet：左右臂各 7 维，左右末端位置各 1 维；部分 pose 字段为空数组。
- 元信息：任务指令、采集信息、原始整数秒时间戳和干预标记。
- 用户确认常见 FPS 为 30；采用可覆盖默认值。第 3 步抽查 3 个 episode：数值行约 188–190 行/秒，但 RGB/深度图像每约 6 行（2–9 行）才变化一次，唯一图像数折算约 30 张/秒，RGB 与深度切换边界一致。即 30 FPS 对应相机帧率，而非 H5 行率；若把每行当作 30 FPS 的一帧，时长会被拉长约 6 倍。第 7 步必须明确选择：按相机帧取行、保留高频数值另存，或其他方案，不静默重采样。
- 原始时间戳不能恢复精确亚秒间隔；LeRobot 时间按 `frame_index / fps` 生成并保留原始值。

2026-09-23 用户已确认（配置见 `config/x_humanoid.json`）：

- state = 当前输出帧的 puppet，action = 下一输出帧（下一张相机图像起始行）的 puppet，即 action[t] = state[t+1]。master 为遥操作臂数据，不用于 action/state，不写入 LeRobot。每个 episode 最后一帧没有下一帧，明确丢弃，不补值、不复制（用户确认，2026-09-23）。关节值含义参考 SDK PDF：关节位置 rad（/arm/status MotorStatus.pos），正方向按整机坐标系与右手定则。
- 拼接顺序：左臂 7 维、左夹爪 1 维、右臂 7 维、右夹爪 1 维，共 16 维，与 H5 存储顺序一致。关节名称取自 doc/天轶2.0 Pro ROS2 SDK-250926.pdf，按电机 ID（左臂 11–17、右臂 21–27）依次为 shoulder_pitch、shoulder_roll、shoulder_yaw、elbow_pitch、wrist_yaw、wrist_pitch、wrist_roll；H5 维度按 ID 升序对应属推断。
- 单位：关节为弧度（实测 -1.64~1.61），夹爪为归一化开合度 0~1（实测 0.495~0.998），深度为毫米（实测非零 400~3300）。
- 取帧：按相机图像起始行取帧，输出约 30 FPS，不重采样数值。

## 3. 代码结构

```text
app/
  main.py                          # 服务启动入口
  context.py                       # 顶层对象，导入并实例化 component
  ui_manip/
    content.py                     # 导航、页面布局与 panel 路由
    templates/base.html            # 布局、分组导航、ui-* 公共样式类（第 16 步）
    static/app.css
    static/app.js                  # 公共函数：提示、表格、统计卡片、数据集下拉框、setupFlow（预检 → 生成 → 轮询）
    dataset/                       # panel.html + panel.js（panel.js 经 /ui/<业务>/panel.js 白名单路由提供）
    conversion/                    # panel.html + panel.js
    visualization/                 # panel.html + panel.js
    tag/                           # panel.html + panel.js（添加标签）
    split/                         # panel.html + panel.js（数据拆分）
    merge/                         # panel.html + panel.js（数据合并）
    subtask/                       # panel.html + panel.js（任务标注）
    task/                          # panel.html + panel.js（后台任务）
  api_manip/
    common.py                      # 组件异常统一转换为 404 / 409 / 400（第 15b 步）
    dataset/api.py
    conversion/api.py
    visualization/api.py
    tag/api.py
    split/api.py
    merge/api.py
    subtask/api.py
    task_api.py                    # 公共任务接口：查询、取消、重试
component/
  common/
    dataset_access.py              # 格式识别与统一读取
    task_manager.py                # 后台执行、进度、取消与重试
    output_publish.py              # 输出名校验、临时目录（staged_output）、发布与预检输出字段
    validation.py                  # 输出验证清单 Checks 与 require_passed（第 15a 步）
  dataset/dataset_manager.py
  conversion/data_converter.py     # H5 → v3.0 / v2.1
  conversion/version_converter.py  # v3.0 ↔ v2.1（第 15b 步拆出）
  visualization/data_visualizer.py
  tag/data_tagger.py
  split/data_splitter.py
  merge/data_merger.py
  subtask/subtask_editor.py
util/
  h5_helper.py
  lerobot_helper.py
  lerobot_v21_helper.py            # v2.1 元数据读取、v3 → v2.1 写出、调用官方 v2.1 → v3 函数
  data_conversion_helper.py
  video_helper.py
  storage_helper.py
config/
  settings.py
  x_humanoid.json
tests/
tools/lerobot_v21/                 # 隔离环境（lerobot 0.3.3），只用于验证 v2.1 能被官方代码读取
doc/plan.md
doc/current_state.md
data/
output/
log/
```

只在 component 设置 common。UI/API 按相同四个业务拆分，导航分别为数据集、格式转换、数据可视化、拆分合并。API 请求模型先留在对应 api.py，不提前增加模型层。

`main.py` 加载配置、创建 AppContext、创建 FastAPI、挂载 UI/API 并管理关闭流程。`AppContext` 持有 settings、dataset_access、task_manager、dataset_manager、data_converter、data_visualizer、data_tagger、data_splitter、data_merger、subtask_editor。组件只接收所需依赖，不导入 context。

每个业务先一个主要类：

| 类 | 主要方法 |
| --- | --- |
| DatasetManager | register、list、inspect、episode、remove（只删登记） |
| DataConverter | preview、convert |
| DataVisualizer | get_episode（帧索引与相机图像边界）、get_color、get_depth、get_series（抽样曲线）、get_frame（当前行原值）；后续 prepare_preview |
| DataTagger | preview、submit（由任务描述生成列） |
| DataSplitter | options、preview、submit（按条件抽取子数据集） |
| DataMerger（第 13 步） | preview、submit |
| SubtaskEditor（第 14 步） | episodes、episode、save、discard、replace_text、export_rows、import_rows、preview_write/submit_write、preview_to_task/submit_to_task |
| TaskManager | submit、get、cancel、retry |

重要复用参数存成员变量；方法保留少量关键参数，其余选项通过有默认值的 kwargs 传入。每个 Python 文件提供 demo_xxx/main。完整风格要求见根目录 AGENTS.md。

## 4. 转换方法

### H5 → v3

流程：发现 episode → 预检字段与长度 → 应用映射 → 按帧块读取 → 官方 SDK 写入 → 验证 → 发布。

- h5py 分块读取数值，逐帧解码图像，不整批加载约 81.8 GB 数据。
- 默认保留 RGB 分辨率；颜色通道按 `camera_color_channel` 处理（样本为 rgb，解码后不交换通道）。
- 使用 LeRobotDataset.create/add_frame/save_episode/finalize；锁定经过验证的 SDK 版本。
- state/action 使用明确配置的字段顺序和类型，任务文本取 metadata.language_instruction，允许显式覆盖。
- 记录源路径、源 episode、映射、FPS、精度变化和未映射字段。
- 预检缺字段、帧数不一致、目标覆盖和磁盘空间；不在各层重复检查。

### 深度及原始扩展信息

核查的官方 v0.6.1 深度实现包含 12 位量化，不能将它称为原始 16 位无损保存。

- 用户要求（2026-09-23）：转换后的 LeRobot 数据要小巧、严格遵循格式，不含额外内容。深度只写官方深度 feature；不再写配套 HDF5 与 platform/ 目录（此前“两者都写”的方案取消）。来源与语义记录（映射、源 episode、截断像素数、验证结果）保存在任务结果中，不写入数据集目录。
- 深度量化参数：线性 0–4.095 m，12 位代码正好 1 mm/级，HEVC 无损；源为整数毫米，≤4095 mm 时逐像素无损。超出部分截断（不可恢复）并在任务结果中逐 episode 记录像素数（小样本中出现最大 4294 mm）；用户确认（2026-09-23）保持 1 mm/级截断方案，不改为 2 mm/级。深度视频保持无损编码（用户确认，2026-09-23），虽约占输出体积 93%。
- 原始整数秒时间戳、干预标记、源行号和原始深度 PNG 字节不写入 LeRobot 输出。
- RGB 使用有损编码时，往返不保证像素或原始 JPEG 字节相等。

### v3 → H5（已取消）

用户确认（2026-09-23）不需要 v3 → H5 与 v2.1 → H5。第 10 步曾实现并验证 v3 → H5，已按要求移除。

### v2.1 相关路径

- v2.1 → v3：调用 lerobot 0.6.1 官方脚本 convert_dataset_v21_to_v30 中的各步函数（convert_info/tasks/data/videos/episodes_metadata），源目录只读、写入临时目录；不调用其 convert_dataset（会原地改写源目录）。整体转换，不支持部分 episode。
- v3 → v2.1：平台实现（用户确认，2026-09-23：v2.1 → v3 用官方逻辑，v3 → v2.1 用平台逻辑，要求 v2.1 → v3 → v2.1 得到的数据与原始 v2.1 完全一致）。作为官方转换的逆操作：按 lerobot 0.3.3 的 v2.1 布局写每 episode 一个 parquet（datasets 3.x 的 Sequence 元数据与 fixed_size_list 类型）与 mp4（从合并视频按 episode 流拷贝拆出，不重新编码；H.264 去掉官方 concat 合并时由 h264_mp4toannexb 复制到每个关键帧的 SPS/PPS），meta 下 4 个文件换行固定为 LF；feature 定义原样写回，只去掉官方转换加的 fps；统计只保留 min/max/mean/std/count。
- v2.1 不含深度（用户确认，2026-09-23）：lerobot 0.3.3 要求图像统计为 (3,1,1)，带 1 通道深度 feature 的 v2.1 实测无法加载，因此 v3 → v2.1 明确去掉深度 feature 并记录在任务结果中。
- H5 → v2.1：平台自动经临时 v3.0 完成（H5 → v3.0 写入并验证 → v3.0 → v2.1 写入并验证），临时 v3.0 不保留（用户确认，2026-09-24）。
- v2.1 → H5：不做（用户确认，2026-09-23）。
- 版本处理（用户确认，2026-09-23）：主环境只固定 lerobot 0.6.1（只读写 v3.0）。v2.1 → v3.0 使用 0.6.1 自带的官方脚本 convert_dataset_v21_to_v30；v3.0 → v2.1 由平台用 pyarrow/PyAV 直接写 v2.1 文件布局（每 episode 一个 parquet 与 mp4，加 meta 下 info.json、episodes.jsonl、tasks.jsonl、episodes_stats.jsonl），不在服务中运行旧版 SDK。隔离环境 tools/lerobot_v21（lerobot 0.3.3，最后一个 v2.1 版本，CPU 版 torch）只用于验证：用官方代码创建 v2.1 样本、读取平台写出的 v2.1。
- 参考双向转换项目，验证其旧版依赖与当前 SDK 的兼容性；不能只修改版本标识。
- 视频边界以帧准确性为准；必要时重编码，不假设任意时间点 stream copy 都准确。

## 5. 可视化方法

统一访问约定：数据集 → episode → frame_index → 图像、数值、任务。

- H5：h5py 切片，JPEG/PNG 按需读取。
- v2.1：对应 episode 的 Parquet 与视频/图像。
- v3：元数据定位行范围、视频分片和 episode 时间偏移。
- 数值读取只加载必要列，曲线按视窗降采样，当前帧面板显示原值。
- 浏览器连续播放使用兼容视频；H5 或不兼容视频按 episode 生成缓存代理。
- 严格逐帧查看通过后端精确定位，不依赖浏览器 seek 达到帧精度。
- 同一帧游标联动画面、曲线和数值；多相机匹配偏差与缺帧显式呈现。
- 深度伪彩色仅用于显示，支持查看原始像素值；单位未知时不显示为米。
- 缓存键包含来源和预览配置，限制容量，源数据变化后失效。

页面：episode 列表、相机画面、播放时间轴、state/action 曲线和当前帧详情。

## 6. v3 拆分合并

### 拆分

用户定义（2026-09-23）：拆分是按一定方式从一个 LeRobot v3 数据集中选出部分 episode，保存为一个子数据集（一个输出），不是 train/val/test 划分。

1. 选择方式（用户确认，2026-09-23）：指定 episode（编号或范围）、按任务、按由任务描述生成的列（默认 tag）的取值。视频处理（用户确认，2026-09-23）：数据、元数据与统计用官方 split_dataset 的函数（_copy_and_reindex_data、_copy_and_reindex_episodes_metadata，LeRobotDatasetMetadata.create 参数相同）；视频不按官方做法重新编码，源文件中 episode 全选时整文件复制（同官方），部分选中时按 episode 流拷贝片段后用官方 concatenate_video_files 合并。
2. 新增列（数据加 tag）：取值方式为整列统一值（用户要求，2026-09-24，用于标记来源、便于合并后区分），或由任务描述生成（用户要求，2026-09-23）：列名可自定义，默认 tag；用户填写正则表达式，有捕获组取第 1 组，否则取整个匹配；未匹配的任务取用户填写的默认值，不填则为空字符串（用户确认，2026-09-24）。以逐帧字符串 feature 写入，调用官方 add_features（视频整文件复制）。
2. 检查重复、越界、空集合；结果中记录选中与未选的 episode 及其与原 episode 的对应关系。
3. 输出为新目录，源数据只读；验证索引、统计、视频边界及数据完整性。

### 合并

用户定义（2026-09-23）：合并是把多个字段一致的 LeRobot v3 数据集合并为一个数据集。

1. 写入用官方 aggregate_datasets（视频整文件复制或流拷贝拼接，不重新编码；任务合并去重；统计汇总）；平台负责预检、临时目录、验证、发布与登记。
2. 预检按官方规则逐项比对字段（视频 feature 忽略 crf、g 等编码参数键）、FPS、机器人类型，不一致时拒绝并列出差异；按内容指纹检测重复 episode 并警告；勾选顺序即合并顺序。
3. 验证：官方 LeRobotDataset 可打开、计数为各来源之和、任务为并集、全局 index 连续、逐 episode 数据列与任务文本一致、每路视频抽查首/中/末帧逐像素一致；结果记录每个输出 episode 的来源。

### 子任务（第 14 步，用户确认 2026-09-24）

用户训练 pi0.5 / pi0.6 / pi0.7：模型支持子任务时直接使用子任务数据；不支持时（lerobot 0.6.1 的 pi05 只读 task）把子任务转为逐帧 task。

1. 格式同官方 lerobot-annotate：子任务是 language_persistent 列中 style="subtask" 的行（role="assistant"，content 为描述，timestamp 为起始帧的原始 float32 时间戳），整个 episode 的行复制到每一帧；task 保持整体描述。写入调用官方 StagingValidator（timestamp 精确匹配）、LanguageColumnsWriter.write_all 与 Executor._ensure_annotation_metadata_in_info（登记 language feature 与 say 工具）。
2. 编辑页（“子任务”）：episode 列表（已修改 / 已标注 / 未标注）、当前帧图像与滑块、彩色时间线、分段表（起始帧、结束帧、时长、描述），在当前帧切分、合并到上一段、批量查找替换（可正则）、CSV/JSON 导入导出。常用子任务（用户要求，2026-09-24）：本数据集已用的描述按使用段数排序，显示为带编号 1–9 的标签（点击或按数字键填入当前帧所在段），描述输入框聚焦时弹出可过滤的下拉建议（↑↓ Enter Esc），也可输入新描述；S 键在当前帧切分。编辑存为 SQLite 草稿（subtask_drafts 表），“生成新数据集”时才写文件；分段规则：从第 0 帧开始、起始帧严格递增且在 episode 内、描述非空。
3. 写入新数据集：复制源数据集，有草稿的 episode 只替换 subtask 行；plan、memory、事件行与未修改 episode 的子任务原样保留（持续行放 plan 模块、事件行放 interjections 模块暂存，满足官方校验）。验证：官方可打开、language feature 已登记、逐 episode 子任务分段与草稿一致、其他语言标注保留、其他数据列逐值一致、视频逐字节相同。
4. 子任务转逐帧 task：模板含 {subtask}，可含 {task}（原整体描述）；每帧 task_index 指向当前子任务文本，重建 tasks 表、episode 任务列表、task_index 的逐 episode 统计与 stats.json；无子任务的 episode 保留原 task；可选择保留或去掉语言列。直接使用当前子任务（草稿优先，不需要先生成新数据集；用户反馈，2026-09-24），保留语言列时草稿同时写入。
5. 修改 task（用户要求，2026-09-24）：子任务页每个 episode 的整体描述可编辑（与原任务列表一一对应，不能为空），存为 task 草稿（task_drafts 表，{原文本: 新文本}）；批量查找替换可选范围（子任务 / task / 两者）。生成新数据集时改写任务表、逐帧 task_index、episode 任务列表与 task_index 统计；只改 task 时不写语言标注列，输出不增加语言 feature。与数据集已有内容相同的子任务不存草稿。转逐帧 task 时 {task} 与无子任务的 episode 使用修改后的 task。
6. 现有功能适配语言列：v3 → v2.1 去掉语言列（v2.1 无该类型，结果说明中注明，需要时先转为逐帧 task）；加 tag、拆分、合并与转换验证用 columns_equal 比较嵌套列；合并重复检测对语言列按 JSON 内容计算指纹；数据集预览紧凑显示语言列；可视化显示子任务时间线与当前子任务。

## 7. 任务与 API

- UI/API 只做页面和参数处理，耗时工作交给单独工作进程；初期一个任务并发，视频编码线程有界。
- 长任务返回 202 和任务记录；前端每秒轮询进度，全部结束后停止，暂不引入 WebSocket。
- 任务函数为模块级函数，由 context 登记到 TaskManager；工作进程在 episode 边界调用 check_cancel，发现取消或已被标为 interrupted 时退出。工作进程只在任务处于活动状态时写回，避免服务被强制结束后残留的进程覆盖状态。
- SQLite 保存注册信息和任务状态，不保存另一套权威数据集元信息。
- 状态：queued → running → validating → succeeded；支持 failed、cancelling、cancelled、interrupted。
- 取消优先在帧块/episode 边界协作完成；必要时终止任务进程及其编码子进程，并清理未验证输出。
- 重启后未完成任务标为 interrupted；重试创建新记录，首版不做半个视频的断点续写。
- 临时输出完成关闭与验证后再发布；最终注册成功才作为可用数据集。

API 按业务提供：数据集注册/详情/episodes，转换预检/提交，帧/图像/曲线/预览，拆分计划/提交，合并计划/提交，以及公共任务查询/取消/重试。

## 8. 重构与界面重设计（第 15–16 步，用户确认 2026-09-24）

原则：功能与数据行为不变，API 不变；每个子步骤跑全部 pytest，界面部分在浏览器实测，用户确认后再进入下一步。

### 第 15 步：代码重构

- 15a：9 个任务共用输出准备与验证判定（output_publish 的 staged_output、validation 的 require_passed，convert_job 改用 publish）；验证清单、首/中/末帧比对放入 component/common/validation.py（保留失败项最多列 20 条）；预检末尾（输出路径、是否已存在、剩余空间）、default_output、v3_dataset 各一处；DemoReporter 统一放 task_manager.py；删除无用代码（check_episodes、verify_frames、h5_helper 中 v3 → H5 残留分支）。MANIP_FPS 于 2026-09-25 按用户确认删除（未被使用）。
- 15b：data_converter.py 的 v2.1 部分拆到 version_converter.py；app/api_manip/common.py 统一“异常 → HTTP 状态码”（task_api 的 409 保留）；拆分各 preview、SubtaskEditor.current（改为返回 dict）、rewrite_tasks 等长函数；修正过时 docstring 与 demo；为重构涉及、目前没有测试的 submit 与 API 补小测试。

### 第 16 步：界面重设计

- 导航每项 3–5 字，按工作流程分组：数据（数据管理、数据查看）、加工（格式转换、添加标签、任务标注、数据拆分、数据合并）、系统（后台任务）。界面中“任务”统一区分为“后台任务”与“任务描述”；episode、state、action 保留英文。
- 加工页统一结构：标题 + 一句说明（≤ 30 字）→ ① 选择数据 ② 设置 ③ 预检（摘要卡片 + 可展开详情）④ 生成（进度条、结果与“查看数据集”）。“源数据不变、验证后发布”只在生成按钮旁提示一次；实现细节放入详情。转换页不再内嵌任务列表。
- 文案：字段标签 2–6 字、不带括号说明，占位符显示实际默认值；按钮统一为 预检 / 生成 / 取消 / 重试；帮助文字只在易错处出现，最多一行。
- 重排：数据查看（图像在上，RGB 与深度并排，一行播放控件，快捷键收进提示）；任务标注（episode 列表 | 图像与时间线 | 分段表，导入导出与批量替换收进“更多”，两种生成方式用标签页）；数据管理（“添加数据集”弹窗，表格加 episode 数）。全局补加载中与空状态，修复任务结果中的 undefined，在 375 / 768 / 1280 宽度与深色模式下检查。
- 前端代码：app.js 统一提示框、统计卡片、数据集下拉框与“预检 → 生成 → 轮询”流程；仍用 Tailwind CDN + Preline，不引入构建。
- 16a 先完成导航、模板、文案规范与“数据拆分”样板页，用户确认视觉与文字风格后 16b 改其余页面。

## 9. 阶段与验收

| 阶段 | 交付 | 验收 |
| --- | --- | --- |
| P0 | 数据契约、映射和依赖兼容验证 | 小型真实样本读写，确认语义和深度处理 |
| P1 | H5 数据集浏览、RGB/深度/曲线 | 可逐帧检查，按需读取 |
| P2 | H5 ↔ v3 完整闭环 | 官方读取成功，帧数/数值/深度/时间对应通过 |
| P3 | v2.1 读写与可视化 | v3 ↔ v2.1 转换与往返差异报告 |
| P4 | v3 拆分合并 | 集合关系、索引、视频、统计和来源完整 |
| P5 | 批量和异常验证 | 内存有界，取消/失败/重试可靠，源数据不变 |

各文件常用验证写 demo_xxx，在 main 中调用。关键完整性用必要自动化测试补充。小样本验证后逐步扩大，不默认一开始转换全部数据。

## 10. 参考资料

- 主要 H5 参考：https://github.com/Open-X-Humanoid/x-humanoid-training-toolchain/blob/main/scripts/convert_to_lerobot.py
- 补充 H5 参考：https://github.com/anarnuri/hdf5_to_lerobot_converter
- v2/v3 转换：https://github.com/jinnymo/lerobot-v3-v2-converter
- 官方格式：https://huggingface.co/docs/lerobot/lerobot-dataset-v3
- 官方编辑：https://huggingface.co/docs/lerobot/using_dataset_tools
- 核查的 SDK release：https://github.com/huggingface/lerobot/releases/tag/v0.6.1
- 深度实现：https://github.com/huggingface/lerobot/blob/v0.6.1/src/lerobot/datasets/depth_utils.py
- Preline UI：https://preline.co/docs/

会话核查的软件 release 为 v0.6.1，数据集格式标识仍为 v3.0；实施时验证并固定版本，不依赖浮动 main。
