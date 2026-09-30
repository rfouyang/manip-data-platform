# 当前进度

更新日期：2026-09-25。

## 状态

第 1–14 步已由用户确认（第 14 步于 2026-09-25 确认 OK）。第 15b、16 步已完成，待用户检查；正在处理代码审查遗留问题（见“审查遗留问题修正”）。原第 15 步顺延为第 17 步（批量、异常恢复与整体验收）。H5 → v2.1 由平台自动经临时 v3.0 完成（2026-09-24）。转换输出只含 LeRobot 标准文件；LeRobot 深度 1 mm/级，超过 4095 mm 截断且不可恢复，逐 episode 截断像素数记录在任务结果中；深度编码自 2026-09-24 起为封闭 GOP（见下）。尚未进行全量 H5 转换；原始数据目录未改动。

## RoboMIND 2.0 分批转换工具（2026-09-29，已自检，待用户检查）

manip-train 需要 RoboMIND 2.0 天轶数据，本地放不下整个任务的 H5（用户要求每批约 10 GB，转换后删除再合并）。

- 新增 `tools/robomind_batch_convert.py`：ModelScope tree API 列出任务的 trajectory.hdf5（目录名为 `data/tienyi`）→ 按字节数切批 →
  8 路并发 curl 下载（单连接约 1 MB/s）→ `convert_job`（映射 `config/x_humanoid.json`）→ 验证通过后删除该批 H5 →
  全部批次后 `merge_job`。不登记到数据库；中断后重跑续上（已完成批次、已下完文件跳过，未下完的续传）。
- 未改动平台已有代码。
- 验证：demo 列出 `place_cup_in_box_with_right_hand` 152 个 episode，切为 9.9 / 10.0 / 1.3 GB 三批；
  同一流程完成该任务的全量转换（2026-09-30 00:33，约 1 小时 40 分钟）：三批 65 / 76 / 11 个 episode，每批 22 项验证通过、无跳过；
  合并为 152 个 episode、63,369 帧、7.5 GB（原始 H5 21.3 GB，其中无损深度视频占大部分），合并验证通过，输出在 manip-train 的 data/。
  RoboMIND 天轶样本画面确认末端为 Inspire 五指灵巧手。

## 审查遗留问题修正（2026-09-25，已自检，待用户检查）

用户确认第 14 步 OK，要求处理审查遗留问题；同时要求数据集列表显示 LeRobot 的 episode 数、删除 MANIP_FPS。

### 修正

- 工作进程残留：工作进程启动守护线程等待父进程退出（multiprocessing.parent_process().sentinel），服务被强制结束时立即 os._exit，不再继续写入或发布；临时输出由重启后的任务管理器清理。
- 重启：只把上次运行中的任务（running / validating / cancelling）标为 interrupted；排队中的任务保留排队，重启后按顺序执行（原先也被标为 interrupted）。
- RGB / 深度切换行：H5 转换写入每个 episode 前核对每路映射相机的 RGB 与深度切换行与取帧依据一致，不一致的 episode 不写入，记入 skipped_episodes（原因“RGB 与深度图像切换行不一致”）。真实数据 292 个 episode 扫描全部一致（49 秒）。
- RGB 抽查误差参与判定：新增验证项“抽查帧 RGB 平均误差不超过 8（0–255）”；历史 3 次转换实测每帧 2.0–2.35。相邻帧差别小，错帧由上一条负责。
- v3 → v2.1：dtype 为 image 的 feature 列入 dropped_features（info 不再声明），version_notes 按深度 / 语言 / image 分别说明（version_notes 与 version_report 增加 meta 参数）。
- config/x_humanoid.json：去掉未使用的 depth_sidecar 键，depth.policy 改为 lerobot_feature，“配套 HDF5”的说明改为与实际一致（只写 LeRobot 深度 feature，原始值保留在源 H5）。
- 批量替换：先替换并校验全部 episode，任一不合法（描述变空、正则或反向引用无效）时不写入任何草稿并指出 episode；不再逐 episode 重复读取全部数据（write_draft 为 save 与批量替换共用）。非法正则返回 400。
- 可视化：max_points < 1 返回 400；图像切换行缓存在并发请求下淘汰与读取不再抛 KeyError（原先会被误报为 404）。
- 拆分、合并的验证：不再把全部 data 读入内存；index 只读该列（column_values），逐 episode 比较时源与输出各只缓存一个 data 文件，列名读 schema（data_columns）。
- 数据集列表：LeRobot 数据集显示 episode 数与帧数（读 meta/info.json，目录已不存在时显示“—”）；H5 不显示（需逐个打开文件）。窄屏隐藏该列。
- 删除 MANIP_FPS（config/settings.py、.env.example、README）：未被使用，H5 转换的 FPS 来自映射文件，LeRobot 数据集自带 fps。

### 未修改（已评估）

- 取消超时后强制终止恰好落在“发布之后、登记之前”：需要取消请求在发布前最后一次取消检查之后到达，且登记（一次 SQLite 写入）超过 10 秒未完成，实际几乎不会发生；不为此增加状态。

### 验证

- pytest 71 项全部通过（新增：服务进程被强制结束后工作进程在 10 秒内退出、重启后任务标为中断并清理临时输出——去掉修复时该测试失败；重启保留排队任务并执行完成；深度晚一行的 episode 被跳过、其余正常转换；image feature 列入不写入；批量替换全有或全无；max_points 校验；数据集列表规模信息）。
- demo：task_manager、data_converter（真实 H5 2 个 episode，17 项验证通过、无跳过）、version_converter、data_splitter、data_merger、subtask_editor、data_visualizer、dataset_manager、dataset / split / merge / subtask / visualization API、context 全部通过。
- 测试实例截图确认数据集列表的“规模”列。
- 用户要求自行处理（2026-09-25）：8000 端口原服务已停止，确认真实数据库没有进行中的任务后以新代码启动正式服务（真实数据库与 output/）。只读检查：8 个页面加载无脚本错误；数据集列表接口返回 LeRobot 规模；H5 转换预检（292 个 episode 可转换，估算 62,971 帧，1 个 LFS 指针文件不可读并跳过）；服务端无错误日志，日志写入 log/manip_2026-09-25.log。未在正式服务上触发任何生成任务。
- 最新代码的完整界面操作（测试实例 8010，数据库副本，输出写临时目录；Edge 无头 + DevTools 协议按用户点击顺序执行）：H5 前 2 个 episode → v3.0（17 项验证通过，无跳过）；v3.0 → v2.1（32 项，未写入深度）；添加标签（11 项）；按编号拆分 1, 3-4（32 项）；合并 2 个数据集（167 项）；任务标注切分并保存草稿、通过“更多操作”批量替换（1 个 episode）与非法正则（显示“正则表达式无效”）、生成新数据集（26 项）、转逐帧任务（5 项）；统计帧数提交后在后台任务页取消；数据查看步进与播放；添加数据集弹窗显示目录不存在；新生成的 7 个数据集在列表中显示规模，“查看数据集”打开详情。全部符合预期。
- 发现并修正：LeRobot 数据集的当前帧信息误显示“图像 2 / 272”（相邻帧解码后相同导致切换次数少于帧数），改为只对 H5 显示图像序号。
- 顺带修正：转换预检的向量表中 1 维字段显示为单个编号（原先为“7–7”并在窄列换行）；页面加内嵌 SVG 图标，不再产生 favicon.ico 的 404 请求。

## 第 16 步：界面重设计（16a + 16b，2026-09-24 夜，已自检，待用户检查）

用户要求连续完成、次日上午统一检查，因此 16a（样板页）与 16b（其余页面）一并完成，未在中间停下确认。API 未改动。

### 改动

- 导航（templates/base.html）：分组“数据 / 加工 / 系统”，名称改为 数据管理、数据查看、格式转换、添加标签、任务标注、数据拆分、数据合并、后台任务；面包屑显示“分组 / 页面”，浏览器标题随页面变化。没有手动切换过主题时跟随系统深色设置。
- 公共样式：base.html 的 tailwindcss 块中定义 ui-* 组件类（卡片、步骤、标签、输入框、按钮、分段按钮、表格、徽标、详情），替换各页面重复的长类名。
- 公共脚本（static/app.js）：postJson、提示框（原 4 个版本合一）、加载中 / 空状态、统计卡片、表格、详情、预检状态与输出信息、数据集下拉框（fillDatasetSelect，带格式标注与空状态）、带进度条的任务状态与“查看数据集”链接、setupFlow（预检 → 生成 → 轮询，5 个加工页共用）、onPanelShow（页面显示时加载，替代各页面分别绑定导航按钮与检查 hash）。
- 加工页（格式转换、添加标签、数据拆分、数据合并）：一张卡片内编号步骤 ① 选择数据 ② 设置 ③ 预检 ④ 生成；预检结果为摘要卡片 + 可展开详情；输出名占位符显示实际默认名；页面说明缩为一句。格式转换页不再内嵌任务列表（统一到后台任务页），H5 → v2.1、v2.1 → v3.0 的说明只在选中时出现，“统计帧数”只对 H5 显示。
- 任务标注：数据集与计数在顶部工具栏，导入 / 导出 / 批量替换 / 放弃全部草稿收进“更多操作”；左侧 episode 列表，右侧图像与任务描述、常用子任务并排，下方时间线、按钮与分段表；两种生成方式改为标签页，生成前若有未保存修改会提示先保存。编辑逻辑（切分、快捷键、下拉建议、CSV）未改。
- 数据查看：图像（RGB 与深度并排）在最上方，下方一行播放控件（步进按钮改为 SVG 图标），当前帧信息压缩为一行，快捷键收进一行提示；曲线图例与坐标轴不再重叠。
- 数据管理：“添加数据集”按钮 + 原生 dialog（错误显示在弹窗内）；列表显示名称与目录、格式徽标；窄屏隐藏登记时间；详情中字段、任务描述、前几行数据、相机与数值字段为可展开区块；响应其他页面的“查看数据集”。
- 后台任务：状态筛选为分段按钮，类型名称与导航一致，成功任务显示帧数与验证项数，登记仍存在时显示“查看数据集”，空状态有说明。
- AGENTS.md 新增“界面与文案”约定。
- 与计划不同：数据集列表未加 episode 数（列表接口不返回，H5 需逐个打开文件统计，会拖慢列表）。

### 验证

- 独立测试实例（端口 8010，数据库副本与输出目录在临时目录，不影响 output/manip.sqlite3 与 output/）：8 个页面加载无脚本错误；实际完成 添加标签、格式转换（v3.0 → v2.1，前 2 个 episode）、数据拆分、数据合并、任务标注生成新数据集、转为逐帧任务，均显示一句结果与“查看数据集”并能跳转；未保存修改时生成被拦截；统计帧数提交后在后台任务页取消成功；数据查看的步进与播放正常。
- 截图检查（Edge 无头 + DevTools 协议）：1280 宽全部页面与各预检结果、深色模式、375 宽全部页面（均无横向溢出）。
- 对比度（frontend-design 的 contrast-checker）：浅色提示 4.83:1、链接 5.17:1、主按钮 5.17:1；深色链接由 blue-500（4.11:1，不达标）改为 blue-400（5.95:1）。
- pytest 66 项通过；app.main 服务 demo 与 UI 路由 demo 通过。
- 前端 JS 约 2,890 → 2,491 行。
- 未做：真实屏幕阅读器测试；Lighthouse 等性能测量（Tailwind 与 Preline 仍用 CDN，离线部署需另行处理）。

## 第 15b 步：拆分大文件与长函数、统一 API 异常处理（2026-09-24，已自检，待用户检查）

用户要求（2026-09-24 夜）：后续由我自行编写、运行、检查，不再逐步询问；次日上午统一检查。

### 改动

- `component/conversion/version_converter.py`（新）：v3.0 → v2.1、v2.1 → v3.0 的写出、验证与任务函数，以及 dropped_features、version_notes、LEROBOT_VERSION、两个任务类型常量；`data_converter.py` 只保留 H5 相关（1048 → 698 行），H5 → v2.1 从新模块导入 write_v21 与 validate_v21_output。`local_repo_id` 移到 `util/lerobot_helper.py`。version_converter 的 demo 改用合成小样本（原先依赖转换 demo 的输出）。
- `app/api_manip/common.py`（新）：`register_error_handlers` 在 `create_app` 中注册全局异常处理（LookupError 404、FileExistsError 409、ValueError 400，响应仍为 `{"detail": 原因}`）；删除 4 个 call_xxx 包装、call_visualizer 与 14 段路由内 try/except。task_api 的 ValueError → 409 保留。原先未捕获而返回 500 的这三类异常现在返回对应状态码。
- 长函数：转换预检提出 `field_issues`、`estimate_frames`（sample_frames 不再在循环中重复查数据集）；tag 预检提出 `task_usage`、`value_summary`；合并预检提出 `find_duplicates`；子任务 `rewrite_tasks` 拆为 `rewrite_frame_tasks`、`rewrite_episode_tasks`、`rewrite_task_info`，转逐帧 task 的分段组合提出 `frame_texts`；`SubtaskEditor.current` 改为返回 dict（原 4 元组与 docstring 不符）。
- 验证汇总新增 `failed_count`：失败项列表截断为 20 条时，报错信息中的“N 项”仍为全部失败数（原先最多显示 20）。
- 修正：conversion API 过时的 docstring；`main.py` demo 检查全部 8 个页面脚本（原先 6 个）；`create_demo_dataset` 的 return 约定；`lerobot_helper` 中重复的局部导入；current_state 中已过时的 sample3 说明。

### 验证

- pytest 66 项全部通过（新增 tests/test_api.py：404 / 409 / 400 映射、输出名越界 400、预检未通过的提交被拒绝且不创建任务；tests/test_validation.py：失败项截断但计数与报错按全部）。
- 全部 29 个模块的 demo 运行成功（含 H5 真实样本 2 个 episode 转换：H5 → v3 16 项、v3 → v2.1 32 项验证通过；version_converter 合成样本往返 41 / 38 项）。
- 源码约 6,441 → 6,482 行：API 层减少约 70 行，新模块的说明与 demo、拆分后的函数定义使总数略增；目标是可读性。

## 第 15a 步：合并重复逻辑、删除无用代码（2026-09-24，用户已确认 OK）

用户确认重构方案与顺序（15a → 15b → 16a → 16b，见 plan.md 第 8 节）。原则：功能与数据行为不变，API 不变。

### 改动

- 任务骨架：新增 `output_publish.staged_output`（上下文管理器：确认不覆盖已有结果、建临时目录，块内出错或取消时删除），9 个任务改用它，去掉各自重复的准备与 try/except；`convert_job` 改用 `publish()`（原先手写同样逻辑）。拆分与 H5 → v2.1 的中间临时目录仍在块内 try/finally 删除；拆分的视频片段目录在片段合并后即删除（原先等到发布后）。
- 验证清单：新增 `component/common/validation.py`（`Checks`：add、standard_dirs、sample_frames 首/中/末帧 RGB 与深度比对、summary；`require_passed`）。7 处 check 闭包、子任务的手写 dict、6 处汇总与“只含标准目录”检查改用它；拆分、合并、子任务保留“失败项最多列 20 条”。
- 预检末尾：`output_publish.output_fields`（输出路径、是否已存在、剩余空间、blocking、ready；目录已存在时追加阻塞原因）与 `require_ready`（5 处 submit 的 ready 检查）。
- `DatasetManager.get_v3` 替代拆分、子任务的 `v3_dataset` 与 tag 的内联检查；tag、拆分、子任务的 `output_path` 方法改为直接调用 `resolve_output`。
- `episode_table`（按 episode 读 parquet，只缓存当前文件）移到 `util/lerobot_helper.py`，转换、拆分、合并的验证共用。
- `DemoReporter` 统一放在 `task_manager.py`（原 7 份）。
- 删除无用代码：`check_episodes`、`verify_frames` / `DEFAULT_VERIFY_FRAMES`、`LEROBOT_ENTRIES`、`validation_frames`，以及 `h5_helper` 中 v3 → H5 残留的 lerobot/* 分支。MANIP_FPS 当时保留，2026-09-25 用户确认删除（未被使用：H5 转换的 FPS 来自映射文件，LeRobot 数据集自带 fps）。

### 可见的细微变化

- 验证项名称统一：转换中的帧比对由“第 N 帧 RGB 与源一致”改为“第 N 帧 RGB 一致”（与拆分、合并相同）。
- 预检的“输出目录已存在”统一排在阻塞原因末尾；合并预检只有这一条阻塞原因时也会显示编码参数差异说明。
- 剩余空间按输出目录最近的已存在上级目录所在磁盘计算（原先输出目录不存在时用仓库根目录）。

### 验证

- pytest 64 项全部通过（每个模块改完后分别运行，最后全量运行）。
- demo：validation、output_publish、data_tagger、data_splitter、data_merger、subtask_editor、tag/split/merge/subtask API、context 全部通过。
- 源码（app、component、util、config）约 6,690 → 6,441 行（含新增 validation.py）。

## 代码审查修正（2026-09-24）

用户要求审查代码，随后确认修复以下问题（与第 14 步的验收相互独立）。

### 修正

- 输出名路径穿越：转换、拆分、合并、tag、子任务的 output_name 此前直接拼接为 `output_dir / name`，传入 `../` 或绝对路径可写到输出目录以外（包括原始数据目录）。新增 `component/common/output_publish.py` 的 `resolve_output`：只接受 output_dir 下的一级目录名（不含路径分隔符、不以 . 开头），否则 ValueError（API 返回 400）。tag 的列名不合法时，默认输出名改用 tag 后缀，仍显示“列名不合法”的阻塞原因。
- 转逐帧 task 忽略清空的子任务草稿：清空后保存的空草稿被当作“无草稿”，退回已写入的子任务，与预检（按未标注统计）不一致。改为有草稿即使用草稿（subtask_editor.py subtask_to_task_job）。
- H5 转换静默截断：预检只阻塞映射字段问题，转换任务不检查；用到的字段或相机图像比 trajectory_length 长时多出的行被静默丢弃，直接调用提交接口也可绕过预检。`check_episode` 改为模块级函数，增加映射相机 color/depth 图像长度检查；预检与 `write_lerobot` 都调用，任务中发现问题即失败。当前 H5 数据 292 个有效 episode 全部通过检查。
- Windows 下 Ctrl+C：工作进程与服务共用控制台，同时收到 Ctrl+C 后异常退出，任务被记为 failed（而非 interrupted），停服期间还可能启动下一个任务。工作进程现在忽略 SIGINT，由 TaskManager 终止并标记 interrupted。
- 工作进程日志未写入日志文件：spawn 的子进程不继承 loguru 配置，任务中的日志只到终端。`setup_logger` 记录配置（`log_helper.log_config`），TaskManager 传给工作进程，工作进程以同样配置调用 `setup_logger`，追加写同一日志文件（文件名含日期，轮转不改名）；结束时 `logger.complete()` 写完队列中的日志。loguru 0.7.3 没有 `logger.reinstall()`，因此不采用传递 logger 对象的做法。测试与 demo 中没有配置，工作进程用默认终端输出。
- 数据集页 XSS：`statCard` 的标签（H5 metadata 键名）未转义，已加 escapeHtml。
- 合并验证只比较输出中存在的列：新增“feature 定义与来源一致”（复用 feature_differences，忽略视频编码参数与官方默认索引列）与“数据列与来源一致”两项检查。

### 验证

- pytest 64 项全部通过（新增 3 项：输出名不能跳出输出目录；清空子任务草稿后转逐帧 task 按未标注处理；H5 图像比 trajectory_length 长时拒绝转换且不留输出）。
- output_publish、log_helper demo 通过。独立脚本验证：调用 setup_logger 后，工作进程中的日志按时间顺序写入同一日志文件。
- 未实测：真实控制台中按 Ctrl+C 的行为（只做了代码层面的修改）。

### 审查中发现的其余问题：已于 2026-09-25 处理，见“审查遗留问题修正”。

## 第 14 步交付：子任务（2026-09-24，用户已确认 OK）

用户确认（2026-09-24）：参考官方 lerobot-annotate，用 LeRobot 0.6 语言标注（做法 B）保存子任务；另提供子任务转逐帧 task（做法 A），供只读 task 的模型训练；提供编辑子任务文本的页面。

### 实现

- component/subtask/subtask_editor.py：SubtaskEditor（草稿读写、校验、批量替换、导入导出、两类任务的预检与提交）；subtask_write_job（复制 → build_language 保留其他标注并替换草稿 episode 的 subtask 行 → write_language 调用官方 StagingValidator、LanguageColumnsWriter、_ensure_annotation_metadata_in_info → 验证 → 发布 → 登记）；subtask_to_task_job（逐帧 task_index、tasks 表、episode tasks、task_index 统计与 stats.json，按原列类型用 pyarrow 改写）。
- util/storage_helper.py：subtask_drafts 表（dataset_id、episode_index、segments JSON）；删除数据集登记时一并删除草稿。
- util/lerobot_helper.py：LANGUAGE_* 常量、language_rows、subtask_segments、plain_value、columns_equal；LeRobotEpisode.read_subtasks（H5Episode 返回空列表）。
- API：app/api_manip/subtask/api.py（episodes、episode 读写、discard、replace、export、import、write 预检/提交、to-task 预检/提交）。UI：导航“子任务”，app/ui_manip/subtask/panel.html + panel.js（← → 逐帧、Shift 10 帧；未保存修改切换时提示）。
- 适配：v3 → v2.1 的 dropped_features 加入语言列，说明中注明；tag、拆分、合并、转换的逐值比较改用 columns_equal；合并指纹对嵌套列按 JSON；数据集预览语言列显示为“style@秒: 内容”；可视化 /api/visualization/episode 返回 subtasks，页面显示时间线与当前子任务。

### 信息变化

- 子任务 timestamp 取起始帧在 parquet 中的 float32 时间戳（官方校验要求精确匹配）；页面与导出按帧号表示。
- 转逐帧 task 后原整体描述只以模板中的 {task} 形式保留；去掉语言列时子任务只存在于 task 文本中。
- v2.1 不含语言列（子任务丢失，说明中注明）。

### 验证

- tests/test_subtask.py 5 项：分段规则；草稿、替换、导入（任一 episode 非法时整体不导入）、导出、放弃；写入后官方读取每帧的子任务行与时间戳、视频逐字节相同、源不变；已有 plan、插话事件与其他 episode 子任务保留（官方校验通过）；转逐帧 task（官方读取 item["task"]、episode tasks、total_tasks）、v2.1 去掉语言列、合并指纹。全部测试 54 项通过。
- demo：subtask_editor（sample_v3，episode 0 三段，写入 24 项、转 task 5 项验证通过）、subtask api、context。
- 独立测试实例（端口 8010、独立数据库 output/demo/subtask_ui）浏览器实测：切分、编辑、保存草稿、预检、生成新数据集（后台任务，24 项通过）、转逐帧 task（模板 "{task}. {subtask}"，4 个任务）、CSV 导出导入（含逗号、引号、换行）、可视化时间线。
- 含子任务的数据集上实测：加 tag（11 项）、拆分（32 项）、合并（109 项）、v3 → v2.1（128 项）均通过。

### 修正：转为逐帧 task 可直接使用草稿（用户反馈，2026-09-24）

用户保存草稿后直接“转为逐帧 task”，预检提示“数据集中没有已写入的子任务”。原设计要求先“生成新数据集”再转换，两步不直观。改为：转换直接使用当前子任务（有草稿的 episode 用草稿，其余用已写入的）；保留语言列时草稿同时写入（官方写入器与校验），去掉时不写。没有任何子任务时提示先切分并保存草稿。新增测试 1 项，全部 55 项通过。

### 新增：修改 task；修正首次切分（用户反馈，2026-09-24）

- 修改 task：子任务页编辑区顶部为 task（整体描述）输入框，改动后显示原文；随“保存草稿”存为 task 草稿；批量替换新增范围选择（子任务 / task / 两者）；放弃草稿同时放弃 task 草稿。
- 只改 task、不加子任务：不产生子任务草稿（与数据集已有子任务相同的分段也不存草稿）；生成新数据集只改写任务表、逐帧 task_index、episode 任务列表与 task_index 统计，不写语言标注列；验证逐帧 task、官方读取 item["task"]、其他列与视频不变。
- rewrite_tasks 为生成新数据集与转逐帧 task 共用的逐帧 task 改写；转逐帧 task 的 {task} 与无子任务 episode 使用修改后的 task。
- 修正：episode 没有分段时第一次点“在当前帧切分”只新建第 0 帧开始的一段，第二次才在当前帧切开；现在第一次即切成“0 → 当前帧”“当前帧 → 结尾”两段（当前帧为 0 时只建一段）。
- 验证：新增测试 2 项（只改 task 的输出无语言列、task 与子任务同时修改），全部 57 项通过；subtask api / editor / storage demo 通过；测试实例（8010）浏览器实测首次切分与只改 task 的生成（5 项验证通过，输出无语言 feature）。

### 新增：常用子任务快速选择（用户要求，2026-09-24）

- episodes 接口返回 subtask_texts（本数据集当前子任务描述与使用段数，含草稿，按次数降序）。
- 页面：分段表上方“常用子任务”标签（编号 1–9，显示使用段数；当前 episode 新输入未保存的描述也会作为标签出现）；点击或按数字键把描述填入当前帧所在段（没有分段时先建第 0 帧开始的一段）；S 键在当前帧切分。描述输入框聚焦或输入时弹出下拉建议，按输入过滤（不区分大小写），↑↓ 选择、Enter 填入、Esc 关闭，仍可输入新描述。
- 验证：子任务测试 8 项通过（新增常用列表排序断言）；测试实例浏览器实测 S 切分、数字键填入、标签点击与下拉过滤。

### 注意

- 端口 8000 上运行的服务仍是旧代码，需要重启才能看到“子任务”页。
- （已解决）tag、拆分、合并的组件与 API demo 已改用合成小样本 create_demo_dataset，不再依赖 output/sample3。

## 调研：在一个 episode 内按时间线分割任务描述（2026-09-24，已按做法 B + 转 task 实现，见第 14 步）

- 做法 A（逐帧 task_index）：v3.0 每帧有 task_index，episode 元数据 tasks 为该 episode 用到的任务列表；官方读取时每帧 item["task"] = tasks[task_index]（dataset_reader），录制时 add_frame 的 task 可按帧变化。实测官方读取按时间段返回不同子任务；平台 v3 → v2.1、加 tag、拆分全部通过；v2.1（lerobot 0.3.3）同样支持。注意：官方 modify_tasks 按 episode 设置单一任务，会覆盖逐帧分段；整体任务描述不再单独保留（可写入子任务文本或另加 tag 列）。
- 做法 B（lerobot 0.6.x steerable 标注）：data 中新增 language_persistent / language_events 列（info.json 登记 dtype = language），style="subtask" 的行带生效起始 timestamp，整个 episode 的行复制到每一帧；task 保持整体描述；训练用 language_render.active_at / render_messages 取当前子任务；官方 lerobot-annotate 可用 VLM（默认 Qwen，需要本地或远程模型服务）自动切分。实测官方读取正确；但平台 v3 → v2.1 失败（语言列无统计，且 v2.1 不支持该类型），加 tag 与拆分的验证对嵌套结构列比对失败，需适配。
- 建议：以做法 A 为主；如需保留整体描述或使用官方 VLM 自动标注与 recipe 训练，再适配做法 B。待用户确认方案与分段的录入方式后再实现。

## H5 → v2.1 直接选择（用户反馈，2026-09-24）

- 用户反馈格式转换页 H5 的 v2.1 选项不能选。此前按“H5 → v3 → v2.1 两步完成”由用户手动两次转换；现理解为平台自动经 v3.0 完成：H5 数据集可直接选 LeRobot v2.1。
- data_converter.py：转换方向表改为 (源格式, 目标格式) → (任务类型, 输出后缀)，每个源格式第一项为默认目标（H5 默认 v3.0）；新增 `H5_TO_V21_JOB`（conversion_h5_to_lerobot_v21）与 `h5_to_v21_job`：H5 → 临时 v3.0（写入并验证，`build_v3` 与 H5 → v3 共用）→ v2.1（`write_v21` 写入并验证）→ 发布 → 登记 v2.1；临时 v3.0 目录无论成功失败都删除，只保留 v2.1。v2.1 不含深度（深度 feature 不写入）。结果含 v3_validation 与 validation 两段验证。
- 预检：H5 选 v2.1 时返回 via 与 dropped_features，页面提示“先生成临时 v3.0 并验证，再转为 v2.1 并验证，完成后只保留 v2.1；不写入深度”。任务表显示两段验证结果。
- 验证：pytest 49 项通过（新增：H5 直接转 v2.1 与“先转 v3.0 再转 v2.1”meta 逐字节一致、数值一致、无深度、无临时目录残留；H5 的方向、默认目标与小样本提交）。浏览器实测真实 H5 小样本（2 个 episode、575 帧）：v3.0 验证 16 项、v2.1 验证 32 项全部通过，输出目录无残留。

## 旧数据清理（用户要求，2026-09-24）

- 用户要求删除修复前生成的数据（深度为开放 GOP 编码），将重新生成：删除数据集登记 2 sample3、3 sample3_v21、4 sample_sub、6 sample_sub_tag2；目录 output/sample3、sample3_v21、sample_sub、sample_sub_tag2 与全部测试输出 output/demo（约 27 GB）已移到 Windows 回收站（可恢复，由用户清空）。
- 保留：1 place_red_pepper_on_red_tray（原始 H5）、5 pick_up_bottles（用户外部 v3 数据，无深度）。
- 以 output/sample3 为样本的 demo（tag、split、merge 组件与对应 API 的 main）需在重新生成 sample3（H5 → v3 小样本转换，输出名 sample3）后才能运行；pytest 不依赖这些数据，47 项通过。

## 第 13 步交付：合并（2026-09-24）

### 实现

- component/merge/data_merger.py：`DataMerger.preview / submit`，任务 `merge_job`（MERGE_JOB = merge_datasets），`merge_params(preview)`。
  - 写入：官方 `aggregate_datasets`（视频整文件复制或 `concatenate_video_files` 流拷贝拼接，不重新编码；数据改写 episode/index/task 编号；任务合并去重；统计汇总）。写临时目录 → 验证 → 发布 → 登记。
  - 预检：至少 2 个 v3.0 数据集，不能重复选择或同一目录；以第一个为基准，按官方 features_equal_for_merge 规则逐项列出差异（缺少/多出字段、各键取值；视频 feature 忽略 crf、g、preset 等编码参数键），FPS、机器人类型不同也列出，有差异时不能合并；编码参数不同时说明合并后官方把该键记为 null；按内容指纹（除索引列外全部列逐帧字节 + 任务描述）检测重复 episode 并警告（不阻止）。选择顺序即合并顺序。
  - 验证：输出只含标准目录；官方 LeRobotDataset 可打开；episode 数与帧数为各来源之和；任务为并集；全局 index 连续；逐 episode 数据列逐值一致、任务文本一致；每路视频（含深度）抽查首/中/末帧逐像素一致。结果 report 记录每个来源对应的输出 episode 范围、重复 episode、编码参数说明与索引列定义的规范化。
- API：`POST /api/merge/preview`、`POST /api/merge`（202）。UI“合并”页：勾选 v3.0 数据集（勾选顺序即合并顺序，可上移/下移/移除）、输出目录名、预检（概况、输出 episode 范围、与基准比对差异、重复警告）、提交与任务状态。

### 发现并修复：深度视频在 episode 边界的官方读取丢帧

- 现象：含深度的两个子集合并后，官方 LeRobotDataset（PyAV）读取第二个数据集第一帧报 FrameTimestampError；视频数据完整，顺序解码可得全部帧。
- 原因：LeRobot 深度编码 x265 默认开放 GOP，episode 末尾可能出现引用本段更早帧的 B 帧；多个 episode 拼接后，官方 PyAV 读取先跳到目标前一个关键帧再解码，FFmpeg 把下一个 episode 的首帧判为重复 POC 丢弃。用官方代码按相同画面、相同顺序录制同样复现，属于 LeRobot/FFmpeg 的问题，与合并无关；H5 → v3 全量转换也可能出现（此前小样本只抽查了 2–3 个 episode）。
- 修复：
  - 平台深度编码改为封闭 GOP：`x265-params = lossless=1:open-gop=0`（util/lerobot_helper.py `DEPTH_X265_PARAMS`），仍为无损、1 mm/级。实测 5 个 episode 交替拼接官方读取全部成功；只去掉 B 帧（bframes=0）无效。真实数据 2 个 episode 深度视频 64.6 MB → 66.2 MB（+2.5%）。
  - H5 → v3 验证新增“官方读取每个 episode 各路视频首帧”（必须全部成功）。
  - 平台读取（`LeRobotEpisode.decode`）在官方读取报错时改为从该 episode 起点（关键帧）顺序解码（`decode_from_episode_start`），可视化与验证可读取此前编码的数据。
  - 拆分、合并结果新增 `official_read_failures`（`official_read_failures()` 逐个读取各 episode 首帧）：来源为开放 GOP 编码的旧数据时，可能有个别首帧官方随机读取失败，作为警告列出（数据完整）。
- 此前已生成的深度数据（如 output/sample3、sample_sub 等）仍为开放 GOP，需要时可重新转换。

### 验证

- pytest 47 项通过（新增 tests/test_data_merger.py 3 项：两数据集合并与重编号、字段不一致拒绝与重复警告、含深度子集合并）。
- 真实数据（服务 API + 工作进程 + 浏览器）：带 tag 的 pick_up_bottles 按 tag 抽出 green（444 个 episode）与 red（397 个），在合并页先 green 后 red 合并：841 个 episode、857,429 帧、36 个任务，用时 66 s，4210 项验证通过，官方首帧读取无失败，自动登记。

## 第 12 步交付（2026-09-23）

### 业务拆分（用户要求，2026-09-24）

- 原“拆分合并”拆为三个独立业务与页面：数据加 tag（#tag）、拆分（#split）、合并（#merge，第 13 步实现前为说明页），各页独立选择数据集。AGENTS.md 业务目录同步改为 dataset/、conversion/、visualization/、tag/、split/、merge/。
- 组件：component/tag/data_tagger.py（`DataTagger.preview / submit`，任务 `tag_job`，TAG_JOB = tag_column）；component/split/data_splitter.py（`DataSplitter.options / preview / submit`，任务 `subset_job`，SPLIT_JOB = split_subset）。原 component/dataset_edit 删除；两者共用的 `data_files` 移到 util/lerobot_helper.py。
- API：`POST /api/tag/preview`、`POST /api/tag`；`GET /api/split/options`、`POST /api/split/preview`、`POST /api/split`。原 /api/dataset-edit/* 删除。
- 页面：app/ui_manip/tag/、split/、merge/ 各自 panel.html（tag、split 各自 panel.js）；共用的提示框、v3.0 数据集下拉框与任务状态轮询放在 static/app.js（`alertHtml`、`fillV3DatasetSelect`、`pollTaskInto`）。
- “数据加 tag”页选中数据集后直接列出该数据集的全部任务描述（episode 数与帧数），便于写正则。
- /static 下的文件响应加 Cache-Control: no-cache（页面脚本此前已加），服务更新后浏览器不再使用旧的共用脚本。
- 测试：tests/test_dataset_editor.py 拆为 test_data_tagger.py、test_data_splitter.py；pytest 41 项通过；各模块 demo、服务启动检查通过；浏览器实测三页导航、tag 预检、按任务预检抽取。

### 修正：数据集列表不刷新（2026-09-24）

- 用户反馈：登记目录时提示“该目录已登记为数据集 6（sample_sub_tag2）”，但数据集页只显示 5 个。原因：数据集 6 由数据加 tag 任务完成后自动登记，而数据集页的列表只在页面首次加载、登记和删除时读取。现改为每次切到数据集页都重新读取列表，并保留当前选中行的高亮。浏览器实测：后台登记新数据集后切回数据集页，列表由 6 行更新为 7 行。

### 数据加 tag：整列统一值（用户要求，2026-09-24）

- “数据加 tag”页新增取值方式“整列统一值”：所有帧的该列填同一个值（不看任务描述），用于标记数据来源、批次等，便于后续合并多个数据集后区分或抽取。原方式为“从任务描述抽取（正则）”。
- `DataTagger.preview(dataset_id, mode, **kwargs)`：mode 为 regex（pattern、default）或 constant（value，不能为空）；`tag_params(preview)` 由预检结果生成任务参数（提交、demo、测试共用）。写入仍为同一个 `tag_job`（每个 task_index 对应的值在预检时确定），结果 report 记录 mode、pattern/default 或 value。
- API：`POST /api/tag/preview`、`POST /api/tag` 的请求体增加 mode（默认 regex）与 value，pattern 改为可选。
- 验证：pytest 44 项通过（新增统一值测试）；浏览器实测方式切换、空值提示，并在 cherry_3_0（745 个 episode、357,592 帧）上经服务提交 source = cherry_batch1，11 项验证通过、自动登记。

### 数据集预览（用户要求，2026-09-23）

- 用户反馈：写正则、选字段之前需要先看到数据集的字段与实际数据。数据集页点击一行（或“查看”）即选中该数据集，先显示“字段与数据预览”，episode 列表随后加载。
- `DatasetManager.preview`（`GET /api/datasets/{id}/preview?rows=5`，最多 50 行），只读少量数据：
  - LeRobot v3.0 / v2.1：概况（episode、帧、FPS、任务数、机器人）、全部 feature（类型、形状、各维名称、视频编码）、全部任务描述及其 episode 数与帧数、episode 0 的前 5 行（全部非视频列，附任务描述）。
  - H5：metadata、全部数值字段与相机图像字段、任务描述（language_instruction）、第一个可读 episode 的前 5 行（时间戳与各数值字段）。
- 修正：详情中相机表对无深度相机（depth_resolution 为空）的 LeRobot 数据集报错；各页面脚本共用全局作用域，数据集页的预览函数与格式转换页同名被覆盖，已改名为 renderDatasetPreview；页面脚本响应加 Cache-Control: no-cache，服务更新后浏览器不再使用旧脚本。
- 验证：浏览器实测 H5、v3（cherry_3_0）、v2.1（pick_up_bottles，55 个任务描述）、带 tag 的 v3；pytest 41 项通过（新增预览测试 2 项）。

### 由任务描述生成列

- 用户要求：从 task 描述生成一个新列，列名可自定义（默认 tag），用户填写正则表达式抽取。
- component/dataset_edit/dataset_editor.py：`DatasetEditor.preview_tag / submit_tag`，任务函数 `tag_job`（TAG_JOB = dataset_edit_tag）。
  - 规则：正则在每个任务描述中 search；有捕获组取第 1 组，否则取整个匹配；多于 1 个捕获组、列名不合法或已存在、输出已存在时不能提交。未匹配的任务取“未匹配时的值”，不填则为空字符串（用户确认，2026-09-24：部分任务匹配不上是正常情况），预检中逐个任务列出，页面显示为“（空）”；拆分页可按空值选取。
  - 写出：调用官方 `add_features`，新列为逐帧字符串 feature `{"dtype": "string", "shape": (1,), "names": null}`，值按每帧的 task_index 取；视频整文件复制；输出写临时目录 → 验证 → 发布 → 登记。
  - 官方 `LeRobotDatasetMetadata.create` 会用默认定义覆盖 5 个索引列（timestamp、frame_index、episode_index、index、task_index）；由官方 v2.1 → v3 转换得到的数据集因此去掉这些列上的 fps 键。验证允许这一变化（dtype/shape/names 须不变），并在结果 report.normalized_features 中列出。
  - 验证：输出只含标准目录；官方 LeRobotDataset 可打开；episode 数与帧数不变；新列定义；其他 feature 定义不变；各 data 文件原有列逐值一致、新列按 task_index 取值；视频文件清单一致且逐字节相同。
- component/common/output_publish.py：临时目录、删除与发布（转换与数据集编辑共用，由 data_converter 移出）。
- API：`POST /api/dataset-edit/tag/preview`、`POST /api/dataset-edit/tag`（202）。UI“拆分合并”页：数据集、列名、正则、默认值、输出目录名、预检（值汇总与各任务取值）、提交与任务状态。
- v2.1 转换支持字符串列：parquet 写为 string；字符串列没有统计，写 episodes_stats 时跳过。带 tag 的数据集 v3 → v2.1 → v3 验证通过，官方 0.3.3 可读取 tag。
- 验证：pytest 35 项通过（新增 tests/test_dataset_editor.py 3 项）。真实数据（平台转换的 pick_up_bottles v3，1298 个 episode、1,308,071 帧）经服务 API 与工作进程：正则 `(?i)now pick up the (\w+) bottle` 得到 green 444 / red 397 / yellow 457 个 episode，用时 40 s，15 项验证通过，自动登记无问题。

### 抽取子数据集

- 用户确认：选择方式为指定 episode（编号或范围，如 0-99, 120）、按任务（勾选任务）、按字符串列取值（如 tag）；视频不重新编码。
- component/dataset_edit/dataset_editor.py：`DatasetEditor.subset_options / preview_subset / submit_subset`，任务函数 `subset_job`（SUBSET_JOB = dataset_edit_subset）。
  - 整 episode 选入；按任务或取值时，若 episode 同时含未选的任务或值，预检给出说明（仍整 episode 选入）。编号越界、格式错误拒绝；空选择、输出已存在不能提交。
  - 写出：`LeRobotDatasetMetadata.create` 参数与官方 split_dataset 相同；数据与 episode 元数据、统计用官方 `_copy_and_reindex_data`、`_copy_and_reindex_episodes_metadata`（官方私有函数，依赖 lerobot 0.6.1 固定版本）；视频 `copy_videos_lossless`：源文件中 episode 全选时整文件复制，部分选中时按 episode 流拷贝（`split_video(..., strip_parameter_sets=False)`）后用官方 `concatenate_video_files` 合并，时间偏移按片段时长累加。新 episode 按源编号顺序重新编号。
  - 验证：输出只含标准目录；官方 LeRobotDataset 可打开；episode 数与帧数；feature 定义（索引列按官方默认定义）；全局 index 连续；逐 episode 数值列逐值一致、任务文本一致、任务列表与长度一致；每路视频（含深度）抽查首/中/末帧逐像素一致。
  - 结果 report：选择条件、新旧 episode 对应（episode_mapping）、未选 episode 列表。
- API：`GET /api/dataset-edit/subset/options`、`POST /api/dataset-edit/subset/preview`、`POST /api/dataset-edit/subset`（202）。UI“拆分合并”页新增“抽取子数据集”卡片（三种选择方式、预检、提交与任务状态）。
- util/lerobot_v21_helper.py `split_video` 增加 strip_parameter_sets（默认 True 用于还原 v2.1；v3 内部重组传 False，packet 原样拷贝）。

### 验证

- pytest 39 项（新增 tests/test_dataset_editor.py 共 7 项：取值规则、预检与阻止条件、生成列、编号解析、选择方式、部分/全部选中与重编号、含深度按 tag 抽取）。
- 真实数据（服务 API + 工作进程）：带 tag 的 pick_up_bottles v3（1298 个 episode）按 tag = red 抽取 397 个 episode、419,679 帧，分布在全部 27 个视频文件中，用时 146 s，2387 项验证通过，自动登记无问题；逐一比较 397 个 episode 的视频压缩数据与源逐字节相同（未重新编码）。体积 5.65 GB → 1.81 GB。
- 已知问题（Windows，偶发）：全量测试 4 次中有 1 次 H5 → v3 测试失败，LeRobot 编码深度视频时临时帧文件被其他进程占用（WinError 32）、PyAV 打开临时 mp4 报 Invalid argument；单独重跑通过。疑似杀毒/索引扫描新写入的临时文件，与本步改动无关；实际转换中表现为任务失败，可重试。未处理。

## 第 11 步交付（v3.0 ↔ v2.1）

### 格式依据与用户确认

- v2.1 以 lerobot 0.3.3（PyPI 上最后一个 v2.1 版本，CODEBASE_VERSION = v2.1）为准；0.4.0 起为 v3.0。
- 实测：0.3.3 加载带深度 feature 的 v2.1 会报错（`_assert_type_and_shape` 要求 key 含 image 的统计为 (3,1,1)，深度为 (1,1,1)）；去掉深度后正常。用户确认 v2.1 不含深度（2026-09-23）。
- 实测：datasets 4.x 写 parquet 特征元数据为 `List`，0.3.3 依赖 datasets ≤3.6；平台按 `Sequence` 写，并把向量列转为 fixed_size_list，与 0.3.3 写出的一致。

### 实现

- util/lerobot_v21_helper.py：`V21Metadata`（v2.1 只读元数据，提供与 v3 LeRobotDatasetMetadata 相同的读取接口）；v2.1 写出（`v21_features`、`v21_info`、`write_episode_table`、`split_video`、`load_v3_episode_stats`、jsonl 读写）；`convert_v21_to_v3` 调用官方脚本的各步函数（不调用会原地改写源目录的 convert_dataset）。
- util/lerobot_helper.py：`load_metadata` 按 codebase_version 分派，`LeRobotEpisode` 与 `list_episodes` 同时支持 v3.0 与 v2.1（数据集详情、可视化复用）。
- component/common/dataset_access.py：识别 `lerobot_v2.1`；其他 LeRobot 版本拒绝。
- component/conversion/data_converter.py：方向 H5 → v3.0、v3.0 → v2.1、v2.1 → v3.0；`preview_version`（概况、feature 保留/不写入、说明、输出与空间）；`submit_version`（v3 → v2.1 支持 episode 数小样本；v2.1 → v3 整体转换，传 episode 数时拒绝）。
- `v3_to_v21_job`：临时目录 → 逐 episode 写 parquet 与流拷贝拆分视频（每 episode 检查取消）→ meta 4 个文件 → 验证 → 发布 → 登记为 lerobot_v2.1。
- `v21_to_v3_job`：临时目录 → 官方函数转换 → 验证（官方 LeRobotDataset 打开）→ 发布 → 登记为 lerobot_v3.0。官方函数内部整体处理，只能在阶段之间响应取消；超过取消超时由 TaskManager 终止进程。
- 验证（两个方向）：输出只含 data/meta/videos；v2.1 的 meta 只含 4 个文件；episode 数、总帧数、fps、feature 列表；统计 count 与只含 5 项；逐 episode 全部数值列逐值相等；每路视频抽查首/中/末帧与源逐像素相等（流拷贝）。
- 结果记录（任务结果 report）：来源、方向、lerobot 版本、repo_id、说明（含不写入的 feature）。
- UI：格式转换页目标格式 H5 → v3.0（v2.1 选项禁用并提示两步）、v3.0 → v2.1、v2.1 → v3.0；版本转换预检显示概要、说明与 feature 表（写入/不写入）；任务表显示版本转换结果。
- tools/lerobot_v21/：隔离环境（pyproject.toml、uv.lock，lerobot==0.3.3，CPU 版 torch）与 check_v21.py（`create_sample` 用官方代码创建 v2.1 样本，`check_dataset` 用官方 LeRobotDataset 读取并输出摘要）。平台服务不依赖此环境。

### 信息变化（v3.0 → v2.1）

- 用户确认（2026-09-23）：v2.1 → v3 用官方逻辑，v3 → v2.1 用平台逻辑，要求 v2.1 → v3 → v2.1 与原始 v2.1 完全一致。平台 v3 → v2.1 按官方转换的逆操作实现：
  - feature 定义原样写回（含视频 info、video_info 等），只去掉官方 convert_info 给非视频 feature 加的 fps；
  - meta 文件换行固定为 LF（官方以文本模式写，Windows 上为 CRLF；Hub 与 Linux 上为 LF）；
  - H.264：官方 concat 合并视频时经 h264_mp4toannexb 把 SPS/PPS 复制到每个关键帧，拆分时去掉与 avcC 头相同的副本；
  - mp4 不加 faststart（moov 在 mdat 之后，同 v2.1 编码写出）。
- 不写入深度 feature（用户确认）；再转回 v3.0 也没有深度。
- 逐 episode 统计去掉 q01–q99 分位数（v2.1 没有）；v2.1 → v3.0 的统计由官方函数汇总，同样不含分位数。
- 无法还原、也不影响数据的差异：mp4 容器中的 encoder 标签（FFmpeg 版本）与 btrt 最大码率由本机 FFmpeg 写入；原始 parquet 若由 datasets 4.x 写（特征元数据为 List，官方 0.3.3 无法加载），平台写为 0.3.3 可加载的 Sequence，数据表与类型相同。

### 验证

- pytest 32 项全部通过（另见下方往返一致性修正后新增的 H.264 往返与 SPS/PPS 测试）。新增 tests/test_lerobot_version_conversion.py：v3 → v2.1 → v3 数值与帧一致、深度明确去掉、布局与 meta 文件；部分 episode 与取消后无残留；方向、预检与提交规则；官方 0.3.3 往返（隔离环境存在时运行）。
- 官方样本往返：0.3.3 创建的 v2.1 → 平台 v3.0 → 平台 v2.1，meta 4 个文件逐字节一致，parquet 表与 schema（含元数据）一致，0.3.3 读取的抽样帧（state、task、图像均值）完全一致。
- 真实数据（output/sample3，3 个 episode、847 帧，服务 API + 工作进程，独立输出 output/demo/step11_service）：v3 → v2.1 用时 3.5 s，44 项验证通过，登记为 lerobot_v2.1（详情无问题，可视化单帧 RGB 约 16 ms）；v2.1 → v3 用时 3.5 s，41 项验证通过；v2.1 传 episode 数被拒绝（400）。官方 0.3.3 读取该 v2.1：3 个 episode、847 帧，9 个抽样帧的 state 与图像均值与 v3 源一致。
- 修正了 API 路径中 target/output_name 参数与版本预检、提交函数参数冲突的问题（单元测试补充了这种调用方式）。

## 外部数据集核查：pick_up_bottles（2026-09-23，只读）

- v2.1（C:/Users/rfouy/workspace/data/ste_lerobot_data/pick_up_bottles，5.5 GB）：1298 个 episode、1,308,071 帧、1 路 camera_head（H.264 640×360，30 fps）、state/action 26 维、55 个任务。元数据、逐 episode 数据（行数、frame_index、全局 index、timestamp、task_index、统计 min/max/count）与全部 1298 个视频的帧数一致，无问题。
  - 不能被官方 v2.1 代码（lerobot 0.3.3，datasets ≤3.6）加载：parquet 特征元数据为 datasets 4.x 的 `List`，报错 “Feature type 'List' not found”。
  - 非标准字段：视频 feature 多一个 `video_info`；names 为 channel（官方为 channels）；state/action 没有 names。平台读取不受影响。
- v3（C:/Users/rfouy/workspace/data/ste_lerobot_data_v3/pick_up_bottles，15 GB）：官方 0.6.1 可加载；全部数值列、任务、episode 长度与 v2.1 逐值一致。视频被重新编码为 AV1（约 10.8 KB/帧，原 H.264 约 3.9 KB/帧），抽查 36 帧与 v2.1 平均误差约 3.2（0–255），帧对齐正确；残留与 info 矛盾的 `video_info`（仍写 libx264）和空 images/ 目录；非视频 feature 没有官方转换脚本会加的 fps。判断不是官方 v2.1 → v3 脚本的输出。
- 平台 v2.1 → v3（官方转换函数、视频流拷贝）：177 s，15,581 项验证全部通过，输出 5.3 GB，仍为 H.264，位于 output/demo/pick_up_bottles_check/。
- 往返（v2.1 原始 → 官方 v2.1 → v3 → 平台 v3 → v2.1，util/lerobot_v21_helper.py `compare_v21`）：2600 个文件无缺失与多余；meta 4 个文件逐字节一致；1298 个数据表与 schema 一致；1298 个视频逐帧压缩数据、时间戳与编码头一致。剩余差异：parquet 特征元数据 List（原始，datasets 4.x）→ Sequence（平台，0.3.3 可加载）；mp4 容器 encoder 标签 Lavf58.76.100 → Lavf61.7.100 与 btrt 最大码率。v3 → v2.1 用时约 500 s，15,584 项验证通过。
- 修正前发现并已修正：meta 在 Windows 上写成 CRLF；H.264 关键帧带有官方合并插入的 SPS/PPS；feature 被规范化（丢 video_info、补 names）。

## 外部数据集核查：cherry_3_0（2026-09-23，只读）

- v3.0（C:/Users/rfouy/workspace/data/cherry_3_0，2.4 GB）：745 个 episode、357,592 帧、1 个任务 “pick up cherry”，1 路 camera_head（AV1 640×360，30 fps，约 6.9 KB/帧），state/action 26 维，12 个视频文件。官方 0.6.1 可加载。
- 内部一致性全部通过：meta 与 data 帧数、全局 index、各 episode 的 from/to index 与行、frame_index、timestamp、task_index、统计 count/min/max、无 NaN/Inf；每个视频文件帧数 = 所含 episode 长度之和，episode 时间段首尾相接、起点都在关键帧上；抽样帧可解码。
- 与 pick_up_bottles v3 同一生成方式：残留与 info 矛盾的 `video_info`（libx264）、空 images/ 目录、names 为 channel、state/action 无 names、非视频 feature 无 fps（0.6.1 不要求）。没有原始数据，无法评估重新编码的画质损失。

## LeRobot 格式合规（2026-09-23，用户要求严格遵循格式、输出小巧）

- 输出只含 LeRobot v3.0 标准目录 data/、meta/、videos/。移除了 platform/ 目录：配套 HDF5（source_raw.hdf5：源行号、原始深度 PNG 字节、整数秒时间戳、干预标记）与 conversion.json 都不再写入；util/h5_helper.py 的 SidecarWriter 已删除。
- 来源与语义记录（repo_id、映射、源 episode、截断像素数、验证结果）改为保存在转换任务结果的 report 字段（SQLite）。
- 验证新增“输出只含 LeRobot 标准目录”；去掉配套文件相关检查。真实数据 2 个 episode：15 项验证通过，体积由约 139 MB 降为 69 MB，其中深度视频 64.6 MB（约 93%）、RGB 视频 5.8 MB。
- repo_id 不写入 LeRobot 数据集文件（meta/info.json 没有该字段）。传入 root 时 SDK 直接读 root，repo_id 只作标识；未传 root 时默认目录为 $HF_LEROBOT_HOME/{repo_id}，本地找不到 meta 才按 repo_id 从 Hub 下载。离线实测任意 repo_id + root 读取结果一致。平台使用 `local/<输出目录名>`（`local_repo_id`）。
- LeRobot 编码视频后保留空的 images/<feature>/ 目录（官方 push_to_hub 也忽略 images/）；发布前由 `remove_empty_image_dir` 删除，其中仍有文件时拒绝发布。
- 深度编码保持无损（用户确认，2026-09-23）：HEVC gray12le lossless，与 LeRobot 默认一致；不改为有损以减小体积。
- output/sample3 已删除 images/ 与 platform/，现在只有 data、meta、videos（102 MB）。
- pytest 26 项通过。

## 第 10 步交付

### v3 读取与可视化

- util/lerobot_helper.py `LeRobotEpisode`：与 H5Episode 相同的接口（length、cameras、series_names、camera_info、series_info、read_series、read_timestamps、read_is_intervene、read_color、read_depth、image_change_indices、metadata、check、info）。行号即 frame_index，每帧都有图像；图像按 meta/episodes 中的 from_timestamp + frame_index / fps 从视频解码单帧（PyAV），深度按 feature 记录的量化参数还原为毫米；数值首次使用时从 parquet 读取该 episode 的行。episode_id 为 episode_000000 形式。只支持以视频保存的图像 feature；深度按“RGB feature 名 + _depth”与相机配对。
- component/common/dataset_access.py：`detect_format` 识别 v3.0（`lerobot_v3.0`），v2.1 仍拒绝（第 11 步）；`list_episodes`、`open_episode` 按格式分派。
- util/h5_helper.py：数值字段改为从文件发现（顶层非相机分组下含 data 的子分组），没有 master 的 H5 也能打开；`read_is_intervene`、无深度相机返回 None；`series_info` 读取属性中保存的逐维名称。
- DataVisualizer.get_episode 新增 format、timestamp_unit、series_labels；干预标记改为通过 episode 方法读取。
- 可视化页：v3 显示“帧”与“秒（frame_index / fps）”、无干预标记时注明“该格式没有”、无深度相机时提示；叠加对应字段在 LeRobot 中为 state / action；曲线图例与当前帧数值表显示关节名；深度悬停单位改为毫米。

### 转换产物自动登记

- TaskReporter 新增 `register_dataset`；H5 → v3 在验证并重命名发布后登记为数据集（转换任务结果含 registered_dataset_id），格式转换页在任务结束后刷新数据集列表。

### v3 → H5（已移除，2026-09-23）

- 曾实现并验证（真实数据 2 个 episode 往返，30 项验证通过）；用户确认不需要后移除：DataConverter 的 preview_export / export_h5_job 及相关函数、util/h5_helper.py 的 write_trajectory / encode_color / encode_depth、context 中的任务登记、格式转换页的 v3 → H5 选项与结果显示、对应测试。移除前的文件备份在会话 scratchpad，不在项目内。
- 保留：H5 读取改为从文件发现数值字段（无 master 也能打开）等读取侧改动；转换产物自动登记（H5 → v3 仍使用）。
- 现状：v3 数据集作为源时预检/提交返回“暂不支持从 lerobot_v3.0 转换”，格式转换页 v3 源只显示禁用的 v2.1 选项（第 11 步接入）。任务表中旧的 v3 → H5 任务记录只显示任务消息。
- output/demo/step10_* 下保留当时的演示输出（含导出的 H5），不是登记数据集。

### 验证（移除后）

- pytest 26 项全部通过（移除了 2 个 v3 → H5 测试，改为 v3 数据集登记/详情与“v3 源暂不支持转换”测试）；ruff 未发现未使用导入；context 与转换 API demo 正常。

### 结果（真实数据，v3 可视化）

- 可视化 API：v3 单帧 RGB / 深度约 60 ms。第 9 步截断的帧（第 3 个 episode 第 14 帧）在 v3 深度中读出最大 4095，与记录一致。用户已测试 v3 可视化，无问题。

## 第 9 步交付

> 2026-09-23 更新：下文中的配套 HDF5（platform/source_raw.hdf5）、conversion.json 及相关验证已按用户要求移除，见上方“LeRobot 格式合规”。以下保留为第 9 步当时的记录。

### 依赖

- 加入 `lerobot[dataset]==0.6.1`（数据集格式 v3.0；含 datasets 4.8.5、pyarrow 25.0.1、pandas 2.3.3、av 15.1.0、torch 2.11.0）。为满足其约束，numpy 由 2.5.3 降为 2.2.6（要求 <2.3），opencv-python-headless 由 5.0.0.93 降为 4.13.0.92（要求 <4.14）；项目只用 imdecode/cvtColor/imwrite 等基础接口。
- Windows 上 torchcodec 需要系统 FFmpeg 动态库，加载失败；LeRobot 会退回 PyAV，平台显式指定 `video_backend="pyav"`。PyAV 自带 libsvtav1（RGB）与 libx265（深度）编码器。
- lerobot 的依赖 draccus 安装了顶层 `tests` 包，遮住项目 tests 目录；已新增空的 tests/__init__.py。
- lerobot 0.6.1 只读写 v3.0，自带 v2.1 → v3.0 脚本，没有 v3.0 → v2.1（v2.1 处理方式见 plan，已由用户确认）。

### 实现

- util/lerobot_helper.py：v3.0 创建、逐 episode 保存（`parallel_encoding=False`，逐路编码，资源有界；默认进程池在 Windows 上会重新执行主模块）、读取，以及深度/RGB 读回换算。
- 深度编码：LeRobot 官方深度 feature，线性量化 `depth_min=0, depth_max=4.095 m, use_log=False`，12 位代码正好 1 mm/级，HEVC（x265 lossless=1）。源深度为整数毫米，≤4095 mm 时 LeRobot 深度与源逐像素一致；超出部分截断为 4095，逐 episode 记录超限像素数，原值保存在配套文件。
- util/h5_helper.py `SidecarWriter`：配套 HDF5，按输出 episode_index 分组保存源行号、原始深度 PNG 字节（不重新编码，与源逐字节一致）、camera_observations 与 puppet 各字段的整数秒时间戳和干预标记。
- component/conversion/data_converter.py：
  - `submit_conversion(dataset_id, output_name=, max_episodes=, task=)`：输出目录已存在时拒绝；max_episodes 用于小样本。
  - `convert_job`（工作进程）：写入 `.tmp-<名称>-task<id>` → 状态 validating 验证 → 写 platform/conversion.json → 重命名发布。失败或取消时释放 LeRobot 文件句柄（finalize 幂等）并删除临时目录；删除失败记录日志，不掩盖原始异常。
  - 取消检查：每个 episode 开始及每 30 帧。
  - 只有 1 张图像的 episode（丢弃末帧后 0 帧）不写入，记录在 skipped_episodes。
  - 验证 `validate_output`：episode 数、总帧数（meta 与 parquet）、fps；逐 episode 全量比较 state/action 与源 puppet（float32）、action[t]=state[t+1]、frame_index 连续、timestamp=frame_index/fps、配套文件源行号；每个 episode 抽查首/中/末帧的深度（与源一致，超限按截断值比较）、RGB 平均误差和配套原始深度字节。
- 输出目录结构：LeRobot v3.0 标准文件（data/、meta/、videos/）+ platform/source_raw.hdf5 + platform/conversion.json（来源、映射、字段语义、精度变化、逐 episode 记录与验证结果）。
- API：`POST /api/conversion/convert`（202；目录已存在 409）。UI 后台任务卡片新增 episode 数输入与“提交转换”按钮，任务表显示转换结果。
- tests/test_lerobot_conversion.py（合成 64×64 数据）：state/action 取帧与错位、master 不参与、深度含 0 值读回一致、配套文件内容；取消后无残留；已有输出不覆盖；单图像 episode 跳过并记录；超限深度像素计数。

### 输出字段（与用户确认的一致）

| 字段 | 类型 | 内容 |
| --- | --- | --- |
| observation.state | float32 [16] | 当前帧 puppet，名称按 SDK PDF |
| action | float32 [16] | 下一张相机图像时刻的 puppet |
| observation.images.camera_top | video 480×640×3 | RGB，AV1 yuv420p crf 30 |
| observation.images.camera_top_depth | video 480×640×1 | 深度，is_depth_map，depth_unit mm，线性 1 mm/级，HEVC gray12le 无损 |
| timestamp、frame_index、episode_index、index、task_index | 自动生成 | timestamp = frame_index / 30 |

### 小样本结果（真实数据）

- 2 个 episode（demo）：575 帧，23.8 s（约 24 帧/s，全量 62,854 帧估计约 45 分钟）；验证 22 项全部通过；float32 最大误差 6.0e-8 rad；抽查 6 帧 RGB 平均误差 2.06（0–255），深度逐像素一致。
- 体积：RGB 视频约 10 KB/帧，深度视频约 112 KB/帧，配套 HDF5 约 120 KB/帧；全量估计约 15 GB。
- 3 个 episode（真实服务任务）：847 帧，验证 31 项全部通过。第 3 个 episode（0106_102458）有 2 帧共 15 个像素超过 4095 mm（最大 4294 mm），LeRobot 中截断为 4095，配套文件保留原值。
- 官方 API 在新进程读取：codebase_version v3.0，特征与上表一致，任务文本为 “right hand put the red pepper on the red tray”。

## 第 8 步交付

- component/common/task_manager.py：`TaskManager.start / stop / submit / get / list / cancel / retry`。
  - 串行执行，每个任务一个 spawn 工作进程。工作进程只接收可序列化参数（db 路径、任务 id、模块级任务函数、params），自行创建所需资源。
  - 状态存 SQLite tasks 表：queued → running → succeeded / failed / cancelled；另有 cancelling、interrupted。validating 已预留，第 9 步转换验证阶段使用。
  - 进度：任务函数通过 `TaskReporter.progress(done, total, message)` 写入；前端每秒轮询，全部结束后停止轮询。
  - 取消：排队中的任务直接标为 cancelled；运行中的标为 cancelling，任务在 episode 边界调用 `check_cancel` 后退出。超过 cancel_timeout（默认 10 s）未退出则终止工作进程。
  - 重试：只允许 failed / cancelled / interrupted，新建记录（retry_of 指向原任务），原任务保留为历史。
  - 服务启动时，未完成的任务（queued / running / validating / cancelling）标为 interrupted；服务正常停止时终止运行中的工作进程并标为 interrupted。
  - 服务被强制结束时，Windows 上工作进程会成为孤儿。工作进程写进度与最终状态都只在任务仍为活动状态时生效，`check_cancel` 发现状态已不是 running/validating 即退出，因此孤儿进程不会覆盖重启后的 interrupted。
  - 失败时记录错误信息与完整回溯；工作进程异常退出（无最终状态）时由调度线程标为 failed 并记录 exitcode。
- util/storage_helper.py：新增 tasks 表与 `add_task / get_task / list_tasks / update_task（可带状态条件的原子更新）/ update_tasks_status`。
- 第一个真实任务“全量取帧统计”（component/conversion/data_converter.py `submit_frame_count` 与模块级 `frame_count_job`）：逐 episode 统计 RGB 图像起始行、按 last_frame=drop 计算输出帧数、核对 RGB 与深度的帧边界是否一致；不写任何数据文件。`load_mapping` 与 `output_frame_count` 改为模块级函数，供工作进程直接使用。
- context 组装 TaskManager 并登记任务类型；main 在 lifespan 中启动与停止调度线程。
- API：`POST /api/conversion/frame-count`（202，返回任务记录）；公共任务接口 app/api_manip/task_api.py：`GET /api/tasks`、`GET /api/tasks/{id}`、`POST /api/tasks/{id}/cancel`（状态不允许时 409）、`POST /api/tasks/{id}/retry`（202；状态不允许时 409）。
- UI 格式转换页新增“后台任务”卡片：提交全量取帧统计，任务表显示状态、进度条、结果或当前 episode、提交时间，以及取消/重试按钮。
- tests/test_task_manager.py：成功与串行顺序、失败记录回溯、协作取消与排队取消、取消超时终止进程、重试规则与非法操作、孤儿工作进程不覆盖 interrupted、重启标记 interrupted。

### 全量取帧统计结果（真实数据，2026-09-23）

- 292 个可读 episode，398,769 行 → 63,146 张相机图像 → 丢弃末帧后输出 62,854 帧。每个 episode 138–375 张图像。
- 没有 0 帧 episode；全部 episode 的 RGB 与深度帧边界一致。
- 预检中按抽样估算的 63,448 张图像偏高约 0.5%，实际转换按每个 episode 的实际图像边界取帧。

## 第 7 步交付

### 用户已确认的数据语义（2026-09-23）

- state = 当前输出帧的 puppet，action = 下一输出帧（下一张相机图像起始行）的 puppet，即 action[t] = state[t+1]。master 为遥操作臂数据，不用于 action/state，不写入 LeRobot。每个 episode 最后一帧没有下一帧，明确丢弃，不补值、不复制。“下一帧”指下一张相机图像的时刻，最后一帧丢弃，两点均已由用户明确确认（2026-09-23）。（此前的“action = 同帧 master”方案已由用户改为此方案。）
- 关节值含义参考 SDK PDF：关节位置 rad（4.5 双臂关节 MotorStatus.pos），正方向按整机坐标系 X 前、Y 左、Z 上与右手定则（1.3.3）；夹爪对应灵巧手“张开百分比”，此处每侧 1 维 0~1。配置中每段向量新增 meaning 字段，预检页向量表新增“含义”列。
- 取帧：按相机图像起始行取帧，输出约 30 FPS（实测约 6.3 行/帧），不重采样数值。
- 单位：关节弧度、夹爪归一化 0~1、深度毫米（均由实测范围推断并经用户确认）。
- LeRobot 数据集登记维持现状，按计划第 10–11 步接入。

### 关节名称（2026-09-23，按用户要求参考 SDK PDF）

- 来源：doc/天轶2.0 Pro ROS2 SDK-250926.pdf 的 1.2 全身关节参数与 3.1 MotorName。左臂电机 ID 11–17、右臂 21–27，依次为 shoulder_pitch、shoulder_roll、shoulder_yaw、elbow_pitch、wrist_yaw、wrist_pitch、wrist_roll，名称加 left_/right_ 前缀。SDK 标注关节位置单位为 rad，与已确认单位一致。
- H5 第 1–7 维按电机 ID 升序对应，这是推断，源文件本身没有名称。
- 用户确认 PDF 给出的关节限位不准确（2026-09-23），不用于校验数据范围。此前据 PDF 限位得出的“右肘实测 -1.1°~98.4° 超限、正方向或零点可能不同”的疑点随之撤销。
- 夹爪：SDK 中手部是 6 自由度灵巧手，position 为“手指张开的百分比”；H5 每侧只有 1 维 0~1。名称保留 left_gripper/right_gripper，可能是整体张开比例（推断）。

### 实现

- config/x_humanoid.json：映射配置，含 fps、取帧方式、action/state 来源、16 维向量顺序与单位、相机与深度策略、任务文本来源，以及不写入 LeRobot 的字段及原因。关节名称按 SDK PDF 的电机 ID 顺序命名（见下方“关节名称”）。
- component/conversion/data_converter.py：`DataConverter.load_mapping / vector_layout / check_episode / sample_frames / output_path / preview`。preview 只读，不写任何文件。
- 预检内容：目标格式与源格式校验、逐 episode 映射字段存在性/维数/长度检查、不可读 episode 列表、抽样统计行/帧比例并估算输出帧数、输出目录是否已存在（防覆盖）、可用磁盘空间，汇总为 blocking 与 ready。
- app/api_manip/conversion/api.py：`GET /api/conversion/mapping`、`GET /api/conversion/preview`。
- UI 格式转换页：选择数据集/目标格式/输出目录名，运行预检后显示概要、16 维向量表（维度区间、单位、名称）、不写入字段及原因、不可读 episode 警告。目标格式中 v2.1 与 H5 暂时禁用。
- tests/test_data_converter.py：映射区间、缺字段阻塞、输出目录已存在阻塞、不支持目标与不存在数据集拒绝、取帧按相机图像。

## 第 6 步交付

- DataVisualizer 新增：
  - `get_series(dataset_id, episode_id, names, **kwargs)`：按 start/stop/max_points 返回曲线数据。点数超过 max_points（默认 2000）时按等间隔抽样，stride 随响应返回，仅用于绘图。
  - `get_frame(dataset_id, episode_id, index)`：返回该行各字段的原值、整数秒时间戳和干预标记，不抽样、不换算。
  - `available_series`：排除空数组字段（样本中 4 个 pose 字段），空字段不出现在曲线与数值表中，也不补零。
  - get_episode 的响应新增 series_names。
- API：`GET /api/visualization/series`（fields 为逗号分隔字段名）与 `GET /api/visualization/frame`；字段不存在/为空、行范围无效返回 400。
- UI 数据可视化页新增：
  - state/action 曲线（ECharts 6.1.0）：字段下拉选择，默认勾选“叠加对应的 master / puppet 字段”，同一字段的 master 用实线、puppet 用虚线，各维度同色，便于对比控制端与执行端。
  - 曲线即时间轴：红色游标线跟随当前行；点击曲线跳转到对应行；内置 dataZoom 可缩放查看局部。
  - 当前帧数值表：显示未抽样的原值（6 位小数）。播放时不逐帧请求数值表，暂停后自动补一次。
  - 图表通过 ResizeObserver 跟随容器宽度变化，切换面板或改变窗口后不会错位。
- tests/test_data_visualizer.py 增加抽样 stride、空字段拒绝、行范围拒绝和当前帧原值的测试。

## 第 5 步交付

- component/visualization/data_visualizer.py：`DataVisualizer.get_episode / get_color / get_depth`。
  - get_episode 返回行数、相机信息、每行整数秒时间戳和干预标记，以及每个相机的图像起始行 image_starts（RGB 字节变化处）。image_starts 按 (文件路径, mtime, 相机) 缓存，最多 64 条；首次约 0.25 s，缓存后约 10 ms。
  - get_color：按 camera_color_channel 解码为 RGB，再为显示重新编码为 JPEG（质量 95）。原始 JPEG 字节通道顺序与浏览器预期相反，直接返回会把红色显示为蓝色，因此不能直接透传。显示图像不是原始字节。
  - get_depth：返回原始 uint16（小端、行优先），不换算、不缩放。
  - 行号越界和相机不存在返回 400。
- component/common/dataset_access.py 新增 `open_episode`，read_episode 复用它。
- app/api_manip/visualization/api.py：`GET /api/visualization/episode|color|depth`，参数用查询字符串（dataset_id、episode_id、camera、index）。
- UI 数据可视化页：
  - 数据集 / episode / 相机选择，不可读 episode 在列表中禁用。
  - 行滑块、行号输入；可按行（←/→）或按相机图像（Shift+←/→）跳转。
  - 显示行号、相机图像序号及其覆盖的行范围、是否为新图像、整数秒时间戳、干预标记。
  - RGB 与深度并排。深度在浏览器端用 Turbo 伪彩色绘制，显示范围可手动调整，“自动”取当前帧非零值 2%–98% 分位，切换帧时保持不变；0 显示为黑色；悬停显示原始 uint16 值，注明单位未确认。
  - 进入页面时刷新数据集列表；深度请求丢弃过期响应。
- 按用户要求补充：
  - RGB 与深度标题旁显示分辨率（按“宽 × 高”，字段存储顺序为 [height, width]）；下方显示该 episode 的行数、相机图像数，以及按整数秒时间戳粗估的行率与图像率，并注明非精确帧率。
  - 自动播放：播放/暂停按钮与空格键；播放步进可选“按相机图像”（默认，避免重复图像）或“按数值行”；目标帧率 10/15/30/60；可选循环；可关闭“播放时同步深度”以提高帧率。右侧显示实际测得帧率。拖动滑块、手动跳转、切换 episode/相机时自动停止播放。
- tests/test_data_visualizer.py：显示 JPEG 保持红色（通道正确）、深度原值不变、越界与错误相机拒绝。

## UI 布局调整（按用户要求参考 Preline 官方 Admin Dashboard 模板）

- 官方 Admin Dashboard 为 Pro 模板，页面类名经混淆，不能直接复用；按其结构用 Preline 公开的应用布局写法重做：左侧固定侧边栏导航（w-64，图标 + 文字，当前页高亮），顶部 sticky header（面包屑、服务状态、主题切换），右侧内容区 lg:ps-64。
- 小屏下侧边栏为 Preline HSOverlay offcanvas，由 header 左侧按钮打开，选择页面后自动关闭。为此加载 preline.js 5.0.0（jsdelivr 固定版本），并在 text/tailwindcss 中声明 hs-overlay-open 变体（摘自 preline 的 overlay/variants.css）。
- 深色配色对齐模板：页面 neutral-900、卡片与侧边栏 neutral-800；面板标题改为模板式的紧凑标题与说明，去掉原徽章。
- 修复：text/tailwindcss 块内的中文注释会使 Tailwind 浏览器编译失败导致整页无样式，已移出并写入 AGENTS.md。
- 修复：窄屏下深度 canvas（固有宽 640）和表格撑宽 grid 导致横向溢出，相关 grid 改为 grid-cols-1（minmax(0,1fr)）。
- 已用无头 Edge 截图检查：1440 宽数据集页、可视化页（RGB 红色正确、深度伪彩色与自动范围正常）；600 宽窄屏布局无溢出。未检查：深色模式截图、offcanvas 实际点击展开、真机手机。

## 第 4 步补充：删除登记

- `Storage.delete_dataset`、`DatasetManager.remove`、`DELETE /api/datasets/{id}`；只删除 SQLite 登记记录，不删除或修改磁盘数据。
- 数据集列表每行增加“删除”按钮，确认框说明不会删除数据文件；删除正在查看的数据集时关闭详情。
- 删除后可重新登记同一目录；demo 与 tests/test_dataset_manager.py 已覆盖。

## 第 4 步交付

- util/storage_helper.py：`Storage` 用 SQLite 保存数据集登记（id、name、path 唯一、format、created_at）。每次操作单独连接，便于线程池和后续工作进程使用。只存登记信息，episode 内容始终从源文件读取。
- component/common/dataset_access.py：`DatasetAccess` 负责格式识别（找到 trajectory.hdf5 即为 h5；发现 meta/info.json 时识别 LeRobot 版本并提示当前未支持）、episode 列表（只读元信息和字段形状，不解码图像）、episode 详情。episode_id 为 episode 目录相对数据集根目录的路径（如 success_episodes/0106_102314），拒绝跳出根目录的路径。
- component/dataset/dataset_manager.py：`DatasetManager.register / list / get / inspect / episode`。相对路径以数据目录 data/ 为基准；重复路径、目录不存在、不支持格式均拒绝。inspect 汇总 episode 数、可读数、问题数、总行数、任务指令、zh_description.txt 描述，以及首个可读 episode 的相机与字段结构。
- app/api_manip/dataset/api.py：`GET/POST /api/datasets`、`GET /api/datasets/{id}`、`GET /api/datasets/{id}/episodes/{episode_id}`。错误映射：重复 409、参数/不可读 400、不存在 404。
- app/context.py 实例化 Storage、DatasetAccess、DatasetManager；app/main.py 挂载 dataset 路由。
- UI：数据集 panel 提供注册表单、已登记列表、数据集详情（统计、相机与字段表、episode 列表及状态）和 episode 详情（元信息、时间戳范围、检查结果）。panel.js 由 content.py 的 `/ui/{panel}/panel.js` 白名单路由提供；app.js 新增共用 `escapeHtml`、`fetchJson`。
- util/h5_helper.py 新增 `is_lfs_pointer`，episode 列表对 LFS 指针给出明确原因。
- Settings 新增 db_path（MANIP_DB_PATH，默认 output/manip.sqlite3）。
- tests/test_dataset_manager.py：注册与详情、重复/不存在/LeRobot 未支持拒绝、episode 路径越界与不可读文件。

## 第 3 步交付

- util/h5_helper.py：`find_episodes(root)` 发现 episode；`H5Episode` 只读打开单个文件，提供 metadata、camera_info、info、check、read_series、read_timestamps、read_encoded、read_color、read_depth、image_change_indices。
- 数值按切片读取，图像按帧解码，不整体加载文件。
- read_color 按 `camera_color_channel` 返回 RGB uint8；样本为 rgb，解码后不交换通道（见下方发现 2）。
- read_depth 返回原始 uint16 像素值，不换算单位；非 uint16 单通道时报错。
- check 核对逐帧字段长度与 trajectory_length；4 个 pose 字段为空数组，作为 `empty` 报告，不补零。
- image_change_indices 找出编码字节变化的行，用于得到实际相机帧；需读取全部图像字节，只用于预检/转换。
- demo：直接运行 util/h5_helper.py（右键运行或 `uv run python util/h5_helper.py`），无命令行参数。先读取第一个 episode，核对分辨率，输出 3 帧的 RGB、原始深度和伪彩色预览到 output/demo/h5_helper/，再扫描全部 episode 结构（不解码图像）。
- 按用户要求：各文件 main 不使用 argparse，参数直接在代码中设置；返回值由命名好的局部变量组成，不在 return 中写复杂表达式。app/main.py 的 main 默认启动服务，将 `demo_only` 改为 True 则只运行自检。
- tests/test_h5_helper.py：用小型合成 H5 验证颜色通道、深度 uint16 保真、空字段报告和长度不一致检测。
- 日志统一改用 loguru 0.7.3（按用户要求）：各文件 `from loguru import logger`，删除 logging.basicConfig，消息占位符由 %s 改为 {}。按 loguru 写法新增 util/log_helper.py：`setup_logger(log_dir, level)` 执行 logger.remove()/add()，输出终端与 log/manip_YYYY-MM-DD.log（每日 0 点轮转、保留 30 天、UTF-8、enqueue=True 便于后续多进程），并用官方 InterceptHandler 接管 uvicorn 等标准库日志；uvicorn.run 传 log_config=None。app/main.py 的 main 用 `@logger.catch(onerror=exit_with_error)` 统一记录入口异常并以状态码 1 退出。Settings 新增 log_level（MANIP_LOG_LEVEL，默认 INFO）。已在 8011 端口真实启动验证：uvicorn 启动、请求访问日志和应用日志都写入同一日志文件。
- 新增依赖：h5py 3.16.0、numpy 2.5.3、opencv-python-headless 5.0.0.93；dev 新增 pytest 9.1.1。pyproject 配置 pytest pythonpath。

## 第 3 步发现（影响后续映射）

1. 行率与相机帧率不同：数值行约 188–190 行/秒；RGB/深度每约 6 行（2–9 行）才变化，折算约 30 张/秒，RGB 与深度边界一致（抽查 3 个 episode）。30 FPS 对应相机，不是 H5 行率。第 7 步需确定取帧方案，不静默重采样。
2. 颜色通道：`camera_color_channel=rgb`，JPEG 实际按 RGB 数组经 OpenCV 编码写入。常规 BGR→RGB 会把红色托盘显示为蓝色；已改为按该字段处理并目视核对。plan 中原“解码后 BGR→RGB”规则已更正。此解释由样本推断，外部 H5 可能不同。
3. 全量结构：292 个可读 episode schema 完全一致，共 398,769 行，长度 868–2381，无长度不一致或缺字段。
4. `data/place_red_pepper_on_red_tray/success_episodes/0106_104528/data/trajectory.hdf5` 是 137 字节 Git LFS 指针，不是 HDF5，需要重新拉取 LFS 数据或排除。
5. 深度原始值样本范围约 400–3300（非零），零值约 1.4%；单位仍未确认（若为 mm 则约 0.4–3.3 m，属推测）。
6. 时间戳仍为整数秒。

## 第 2 步交付

- app/ui_manip/content.py 提供根页面路由；app/main.py 挂载 UI 静态资源和路由。
- app/ui_manip/templates/base.html 提供顶部标题栏、服务状态、主题切换，以及位于页面上方的横向业务导航；主内容为单列布局。
- 数据集、格式转换、数据可视化、拆分合并各自拥有独立 panel.html，页面结构按业务分开。
- app/ui_manip/static/app.js 管理 panel 切换、URL hash、主题记忆和健康状态；app.css 仅保留必要规则。
- 新增 Jinja2 3.1.6，并更新 uv.lock；完整版本仍以锁文件为准。
- 当前环境有 Node 24.19.0、没有 npm。UI 采用 Preline UI 组件样式（Tailwind 工具类），通过 Tailwind CSS 4 浏览器 CDN 生成；尚未建立本地前端构建。
- 已移除 daisyUI CSS/主题。深色模式按 Preline 约定在 html 上切换 `.dark` 类（Tailwind `@custom-variant dark`），head 内联脚本在首帧前恢复主题，localStorage 键仍为 manip-data-theme。
- 导航激活状态由 `aria-current=page` 驱动样式；服务状态点改为 Tailwind 颜色类。
- 暂无下拉、弹窗、Tab 插件等交互组件，未加载 preline.js；首次使用 Preline 插件时再加入。
- 删除空的 app/content.py，页面入口统一放在 app/ui_manip/content.py。

## 第 1 步交付

- 使用 uv 0.12.7 和已有的 uv 管理 Python 3.12.14 创建项目 `.venv`，未使用全局 Conda Python。
- 新增 pyproject.toml、uv.lock、.python-version、.gitignore、.env.example 和 README.md。
- 运行依赖：FastAPI 0.141.1、Uvicorn 0.53.0、python-dotenv 1.2.3；demo 依赖 httpx2 2.13.0。完整版本以 uv.lock 为准。
- config/settings.py：读取 .env/环境变量，默认 localhost:8000 和 FPS=30；路径相对仓库根目录解析。
- app/context.py：建立 AppContext，本步持有 settings。业务组件尚未实现，后续按步骤从 component 导入并实例化，不提前添加空壳类。
- app/main.py：FastAPI 启动/关闭、context 生命周期、/api/health、/docs、demo_service 自检（main 中 demo_only 开关）。
- UI/API/component 按四个业务建立目录，以 .gitkeep 保留；仅 component 设置 common。
- 没有创建本地 .env；默认配置可直接运行，用户按需从 .env.example 复制。

## 已完成

- 确认范围：H5/v2.1/v3.0 六方向转换、三种格式可视化、v3 拆分合并。
- 确认 FastAPI + Preline UI（原为 daisyUI，2026-09-23 按用户要求更换），以及 app → component → util 分层。
- 确认 app/main.py 服务入口、app/context.py 顶层对象，以及 ui_manip/api_manip 按业务拆分。
- 仅 component 设置 common；其他业务各一个子目录。
- 确认少量参数、可选 kwargs 默认值、重要复用参数存成员变量、demo_xxx/main、简洁实现风格。
- 前期统计 data 中 293 个 HDF5，约 81.8 GB；抽查三个 episode 的结构、首帧解码和时间戳。
- 样本为 RGB JPEG、16 位深度 PNG、双臂 master/puppet 数据，含整数秒时间戳和空 pose 字段。
- 用户说明常见 FPS 为 30，作为可覆盖默认值。
- 查阅 X-Humanoid 转换脚本、v2/v3 转换项目、官方数据集与编辑接口。发现官方深度处理包含量化，计划保留配套原始深度。
- 新增根目录 AGENTS.md、doc/plan.md 和本文件（2026-09-23 按用户要求从根目录移入 doc/），作为后续实施依据。

## 当前代码情况

原有文件状态：

- app/main.py：已实现服务入口。
- app/ui_manip/content.py：已实现页面入口。
- util/h5_helper.py
- util/lerobot_helper.py
- util/data_conversion_helper.py

三个原有 util helper 仍为空，留待相关步骤实现。UI 四个业务 panel 已建立；API 和 component 业务类仍未实现。

## 未确认事项

1. 关节名称已按 SDK PDF 填入，H5 维度按电机 ID 升序对应；PDF 限位不准确，未用于校验。
2. 相机内参与外参未在源数据中提供，转换后不写入相机标定信息。
3. 夹爪 0~1 的物理含义未确认；SDK 中灵巧手为 6 维“张开百分比”，H5 每侧只有 1 维，可能是整体张开比例（推断）。
4. `0106_104528` 的 Git LFS 指针文件是否补齐或永久排除。
5. Windows 上 lerobot 0.6.1 已实测可用（PyAV 编解码；torchcodec 因缺系统 FFmpeg 动态库不可用，已显式使用 PyAV）。v2.1 所用旧版 lerobot 的版本与 Windows 兼容性待第 11 步核实。
6. 全量取帧统计确认当前数据集没有 0 帧 episode；转换时仍对 0 帧 episode 明确报告，不静默跳过。
7. 全量 H5 → v3 转换（约 45 分钟、约 15 GB）尚未执行，待用户决定时机；全量中超过 4095 mm 的深度像素总数未知（小样本 3 个 episode 中有 15 个）。

## 下一步

用户检查第 14 步（先重启服务，端口 8000 上的服务仍是旧代码）：

```powershell
uv run python -m app.main
```

1. “子任务”：选 v3.0 数据集，点左侧 episode；拖动滑块或用 ← →，在当前帧切分，填写描述，保存草稿。
2. 预检并“生成新数据集”；完成后选新数据集，在“数据可视化”查看子任务时间线。
3. 在新数据集上“转为逐帧 task”（模板如 {task}. {subtask}），供 pi0.5 训练。

用户明确回复 OK 后进入第 15 步（批量、异常恢复与整体验收）。若提出修改，只修改第 14 步并重新自检。

## 验证记录

- 第 10 步：pytest 28 项通过（新增：v3 → H5 平台产物往返 H5 → v3 → H5 → v3、外部 v3 导出、v3 登记与预检；更新 v2.1 拒绝登记的测试）；lerobot_helper、h5_helper、dataset_access、data_visualizer、context、visualization api、task api、服务 demo 通过。8011 端口真实服务完成 H5 → v3 → H5 与自动登记（结果见第 10 步交付）。自检中发现并修复 v3 预检参数重复传入导致的 500。8012 端口无头 Edge：可视化页对 v3 数据集显示 episode、帧/秒说明、无干预标记提示、关节名数值、640 × 480 深度；截图确认 RGB、伪彩色深度与 state/action 曲线正常；格式转换页对 v3 源显示 H5 目标、v2.1 禁用。测试服务均已停止。未在有界面的浏览器中人工点击。

- 第 9 步：lerobot_helper demo（深度 0 与 400–3100 mm 读回误差 0）、data_converter demo（2 个真实 episode 转换并验证）、context demo 通过；pytest 25 项通过（新增 4 项转换测试）。8011 端口真实服务：3 个 episode 转换成功并通过 31 项验证；另一转换在第 1 个 episode 后取消，临时目录已删除；向已存在目录提交转换返回 409；无头 Edge 渲染格式转换页，“提交转换”按钮、转换结果文本和重试按钮正常。测试服务已停止。另有 09:22 由 .venv\Scripts\python.exe app\main.py 启动的服务进程（非本次测试启动）仍在运行，未处理。未在有界面的浏览器中人工点击。

- 第 8 步：storage_helper、task_manager、context、task_api、conversion api、服务 demo 通过；pytest 21 项通过（新增 task_manager 6 项）。8011 端口真实服务：
  - 提交全量取帧统计返回 202（0.7 s，含列出 episode）；运行期间 /api/health 约 2 ms、数据集详情约 1 s，API 未被阻塞；292 个 episode 用时约 45 s，结果见“全量取帧统计结果”。
  - 运行到 30/292 时取消，约 1 s 内进入 cancelled（停在 31/292）；重试生成新任务（retry_of=2）并成功完成；对 succeeded 任务重试返回 409。
  - 运行中强制结束服务进程：工作进程成为孤儿；重启后任务标为 interrupted，孤儿进程在下一个 episode 边界退出，未覆盖状态；重试该任务成功，结果与首次一致。
  - 无头 Edge 渲染格式转换页：数据集下拉、任务表、结果文本和取消/重试按钮正常出现，说明 panel.js 可执行（本机无 Node，未做单独语法检查）。未在有界面的浏览器中人工点击检查。
  - 测试服务已停止，无残留 python 进程。

- 第 7 步 action 改为下一帧 puppet：pytest 15 项通过（新增末帧丢弃与估算帧数测试，确认 master 字段不再参与检查）；data_converter demo 在真实数据集上 ready=True，估算相机图像 63,448 张，丢弃 292 个末帧后输出约 63,156 帧，抽样 episode 333 张图像输出 332 帧；conversion api demo 通过。本机 Bash/PowerShell 中没有 Node，panel.js 未做语法检查，也未在浏览器中查看。

- 第 7 步关节名称修改：pytest 14 项通过；data_converter demo 在真实数据集上预检 ready=True（292 个可转换、1 个阻塞，估算 63,448 帧）。（当时与 PDF 限位的核对结论已因用户确认限位不准确而撤销。）

- uv sync --locked、uv sync --check：锁文件和环境一致。
- uv pip check：17 个已安装依赖无冲突。
- 三个模块 demo：配置读取、context 依赖复用、服务生命周期均通过。
- 显式 port/fps/output_dir 覆盖和相对路径解析检查通过。
- 首次测试客户端提示 httpx 弃用，已改用 httpx2 并重新验证，无该警告。
- 真实 uv run python -m app.main 启动，/api/health、/openapi.json、/docs 均返回 200。
- Ctrl+C 后看到 Application shutdown complete 和 Service stopped；Windows 终端中断返回码为 1，应用关闭流程实际已完成。
- 自检服务已停止，没有遗留运行服务；未扫描或转换原始数据，未执行后续业务测试。
- 前期 H5 检查属于抽样结构和首帧解码验证，不代表全量数据或任何转换功能已通过测试。
- 第 2 步：UI 路由 demo、服务 demo、compileall、uv sync --check 和 uv pip check 均通过。
- 测试客户端验证根页面、四个 panel 标记、健康接口、OpenAPI 和 API 文档均正常。
- 真实浏览器验证四个导航 panel、URL hash、corporate/dark 主题切换和服务状态；CDN 样式加载正常。
- 之前的第 2 步验收服务已停止，端口 8000 已释放，由用户自行启动检查。
- Preline UI 改版：UI 路由 demo 和服务 demo 通过；渲染页面含 4 个导航按钮、4 个 panel，无 daisyUI 引用。尚未在真实浏览器中复查 Preline 样式和深色模式。
- 第 3 步：h5_helper demo 在真实首个 episode 上通过（分辨率核对、3 帧 RGB/深度解码、切片读取、相机帧边界）；全量结构扫描 293 个文件（292 可读、1 个 LFS 指针）；pytest 2 项通过；服务自检、compileall、uv sync --check、uv pip check 通过。图像只解码了抽查帧，未全量解码；未执行任何转换。
- 第 4 步：storage/dataset_access/dataset_manager/dataset api/context/content/服务 demo 通过；pytest 5 项通过；panel.js 与 app.js 经 Node 24.19.0 语法检查通过。8011 端口真实服务：页面含注册表单和数据目录提示，panel.js 正常返回、非白名单路径 404；注册 53 ms，293 个 episode 详情汇总 766 ms（292 可读、1 个问题、398,769 行），episode 详情正常。未在浏览器中点击检查页面交互和样式。
- 第 5 步：data_visualizer、visualization api、dataset api（含删除）、服务 demo 通过；pytest 8 项通过；3 个 JS 文件经 Node 语法检查通过。8011 端口真实服务（curl 计时）：episode 帧索引首次 257 ms、缓存后 9 ms；单帧 RGB 约 6–12 ms、原始深度 614,400 字节约 6–11 ms；删除登记后数据集列表为空、数据目录未变。未在浏览器中点击检查可视化页面交互与绘制。
- 播放与布局验证（无头 Edge + DevTools 协议真实驱动）：点击播放后 3 秒推进 90 张相机图像，实际约 29.7 FPS（目标 30、含深度同步）；暂停后行号不再变化；开启循环并按行播放时从末尾回到开头。后端连续取 30 帧 RGB+深度约 1.26 s（含 curl 进程开销）。截图确认分辨率 640 × 480 显示、播放控件、侧边栏布局；1440 宽与 600 宽均正常。
- 第 6 步：data_visualizer 与 visualization api demo 通过；pytest 10 项通过；panel.js 语法检查通过。真实浏览器（无头 Edge + DevTools 协议）验证：ECharts 载入并生成 14 条曲线（7 维 × master/puppet），曲线状态显示“1048 个点，按每 2 行抽样”；点击曲线 60% 处跳转到第 1262 行（共 2095 行）；播放中游标 xAxis 与当前行一致；暂停后当前帧数值表刷新；无控制台错误；图表宽度随容器为 1049 px。
- 第 7 步：data_converter 与 conversion api demo 通过；pytest 14 项通过；panel.js 语法检查通过。真实数据预检：292/293 可转换，1 个 LFS 指针 episode 被标记跳过，398,769 源行按实测 6.3 行/帧估算约 63,263 输出帧 @30 FPS，输出目录不存在、可用空间 1130 GB，ready=True。浏览器（DevTools 协议）验证预检页面渲染：概要、16 维向量表 4 行、不写入字段表 4 行、不可读 episode 警告均正确。未执行任何转换，未写入输出数据。
- master/puppet 滞后分析（4 个 episode 抽样）：最佳滞后 18–20 行，corr≈0.999；滞后为 0 时 corr 0.916–0.994。该结论来自抽样，未全量验证。

后续主要实现或验证完成后更新本文件。当前进度以 doc/current_state.md 为准，不在根目录另建重复文件。
