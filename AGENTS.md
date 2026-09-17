# AGENTS.md — aliyun-controller

## 项目概览

- 基于 [Textual](https://github.com/Textualize/textual) 的终端 TUI，调用阿里云 SDK 完成账单 / 流量查询与 DNS 解析管理。
- 包名 `aliyun_controller`，命令 `aliyunctl`，Python >= 3.10，Textual >= 8.0。
- 界面与分层风格与 `../litellm-controller`、`../llm-price-compare`、`../clash-controller` 保持一致。
- `docs/textual-dev-guide.md` 是针对本地 Textual 8.2.8 逐条实测过的 API 参考（布局 / 键位 / 弹窗 / 滚动 / 进度条），实现 UI 前先查它。

## 开发命令

```bash
python -m venv venv && source venv/bin/activate
pip install -e ".[dev]"

./venv/bin/aliyunctl                 # 启动 TUI
./venv/bin/aliyunctl -D /path/dir    # 指定配置目录
./venv/bin/aliyunctl --version

ruff check aliyun_controller         # 改动后必须零告警
```

## 架构

```
aliyun_controller/
  main.py      入口：argparse(-D/--dir, --version) + 日志降噪 + App().run()
  app.py       AliyunControllerApp：配置/客户端缓存、状态栏、bootstrap 配置向导（无 Header/Footer）
  config.py    纯数据层：路径解析 + load/save/validate
  client.py    AliyunApiError / format_api_error / build_open_api_config
  billing.py   账单服务层（BSS OpenAPI，返回结构化数据含明细行）
  dns.py       DNS 服务层（Alidns）
  ui.py        界面骨架：PageScreen / MonthPicker / QuotaBar / logo 常量（8.x 特性集中在这里）
  widgets.py   模态（ConfirmModal/InputModal/FormModal/OutputModal）+ 表格与模糊筛选辅助
  app.tcss     全局样式（模态三段式、顶栏、quota-bar、home banner、状态栏）
  screens/     home / setup / settings / billing / dns
```

分层约定：

- `screens/` 与 `ui.py`、`widgets.py` 是展示层；`billing.py`、`dns.py`、`client.py`、`config.py` 不含任何 Textual 依赖，也不 `print`。
- 服务层失败统一抛 `AliyunApiError`（数据不合法抛 `ValueError`），由屏幕层用 `notify_err` 展示。
- `ui.py` 只放可复用界面组件，不写业务取数逻辑。

## 通用界面规范（本项目已落地，其他项目对齐时照抄）

- 页面结构：App 级只有一个 dock-bottom 的 `#status` 状态栏，**不要** `Header` / `Footer`，并 `ENABLE_COMMAND_PALETTE = False`。
  - 首页（`screens/home.py`）：艺术字 logo 居中（`ui.LOGO_LINES`，宽度 < `LOGO_WIDTH+6` 时在 `on_resize` 挂 `-sm` 类降级为普通文本）+ 版本副标题右对齐 + 菜单项居中且文本左对齐 + 底部提示栏贴底。
  - 功能页一律继承 `ui.PageScreen`：顶栏（左 `◀ 返回` + Title/SubTitle 靠左，`compose_toolbar()` 右侧控件，`self.menu` 提供折叠菜单）→ `#page`（1fr，overflow hidden）→ 底部提示栏贴底。折叠菜单是页面内浮层（`position: absolute` + `overlay: screen` + `display` 切换），Esc 优先关菜单。
- 滚动：外层容器永不滚动；固定行 `height: auto`，表格用 `make_table()`（自带 `.tbl` 类 → `height: 1fr`），保证只有一个滚动条。`Vertical`/`Horizontal` 默认 `height: 1fr`，放进 auto 行必须显式写 `height: auto`（表单行被撑高就是这个坑）。
- 内容区版式（主模式）：**小容器 + 大容器**。小容器 `.panel` 内放筛选（搜索框）、排序选择、主操作按钮（`.filter-row` 行，按钮 compact）；大容器为表格 `.tbl { height: 1fr }`。排列方向可变：上大下小（DNS 记录页：搜索/刷新 + 排序/增删改）、上小下大同左（流量/账单页：汇总面板含进度条与刷新，表格在下），也可左右。整组居中（如首页）用 `align: center middle`，**不要**指望 `content-align` 定位子件。
- 顶栏右侧折叠菜单（`self.menu`）：语义对齐 Android ⋮ 溢出菜单，只放**次要 / 与当前页面关联不大**的设置；主要操作必须以容器内按钮常驻可见。没有次要项就不出现菜单按钮（PageScreen 在 `menu` 为空时自动不渲染）。
- 模态：统一三段式 `.modal-title`（下分隔线）→ 内容 → `.btn-row`（上分隔线、`dock: bottom`、右对齐）。按钮语义：default=一般、primary=推荐、error=危险；顺序 `[取消] [主操作] … [危险放最右]`。表单字段用 `compact=True` 的 Input/Select，行高 1、字段间距 1 行（`.frm-row { margin-bottom: 1 }`）；`FormModal > .modal-box` 有 `min-height: 24` 保证纵向留白。布尔型操作（如记录启停）作为表单中的 `kind="switch"` 条目（Switch 控件，CSS 里 `border: none; height: 1` 压成单行），不要独立动作按钮。一般文本不加背景色。
- 设置类页面（纯配置项，如密钥）：一个大容器直接呈现条目，值脱敏显示（如 `LTAI…3456`）+ hover 提示，行距 1 行；**点击条目（或容器内「编辑」按钮）打开与表单页同款的 `FormModal`**——ID 预填当前值、Secret 密码框留空=不更改、Tab/Shift+Tab 轮切焦点、回车或 [保存] 提交、[取消]/Esc 退出。**不要用行内 display 切换做就地编辑**（隐藏 Input 不进 Tab 焦点链、编辑态难以退出，已被否）。保存后 `app.reload_config()` 立即生效。行点击判定用 `event.screen_x/y` + `row.region.contains`。
- 键位（统一，勿另起单字母方案）：方向键移焦点；`Tab` 轮切；`Enter` 确认；`Esc` 返回/取消（Textual 无默认 Esc 行为，每屏必须自绑）；`Home/End/PgUp/PgDn` 页面滚动（Screen 绑定**不加 priority**，Input 聚焦时让位给文本框）；`Ctrl+R` 刷新、`Ctrl+Q` 退出（App 内置 priority）、`Ctrl+N/E/D` 新增/编辑/删除（条目页）。单字母绑定只在无输入框页面保留（首页 `q`、`/` 聚焦搜索）。
- 鼠标：**所有可操作行一律单击执行**——数据表用 `widgets.ClickTable`（`make_table()` 已默认返回它）：单击数据单元格 = 移光标 + 立即发 `RowSelected`；表头点击仍是 `HeaderSelected`。按钮/选项/入口同样单击触发；滚轮天然作用于光标下滚动容器。
- 表格列：内容列用 `widgets.shorten(text, cap)` 截断（主机记录 20、记录值 40），行宽用 `fit_table_columns(table, widths)` 按容器均分；右对齐列（TTL/状态）用 `rcell(text, width)` 并把表头也 `rcell` 化。列宽在 `_rebuild()` 与 `on_resize` 里重算。
- 月份/额度：账期切换用 `ui.MonthPicker`（`◀ [YYYY-MM] ▶`：◀▶ 逐月、点击月份弹**页内浮层**选月——年份 ◀▶ + 4×3 月份按钮网格（当前月 primary、越界 disabled），Esc / 点击浮层外关闭；`Changed` 消息自动重查；Message 类要有 `control` 属性才能被 `@on(..., "#id")` 匹配）。浮层三要素见 `docs/textual-dev-guide.md` §8.4。`ui.QuotaBar` 三段百分比宽度色块拼双色进度条（勿把 `1fr` 与 `%` 混用），额度内绿、超额红。

## 关键约定（Textual 通用）

- 取数：`on_mount` 中 `@work(exclusive=True) async def _load()` + `asyncio.to_thread(阻塞调用)`。阿里云 SDK 全是同步阻塞调用，必须放线程。
- 反馈：统一 `self.app.status(msg, kind)` / `self.app.notify_ok/notify_err/notify_warn`；`kind` 取 `"" | "ok" | "warn" | "err"`。
- 模态：`ModalScreen[T]`；`push_screen_wait` 只能在 worker 内 `await`，否则用 `push_screen(modal, callback)`。
- 表格：数据展示一律 `DataTable` + `widgets.make_table()` / `widgets.load_rows()`，不要手写空格对齐的字符串表格。
- 复用 `widgets.py`：`ConfirmModal`、`InputModal`、`FormModal`（`kind`: text/password/choice/note）、`OutputModal`、`filter_fuzzy`。

## 配置与文件

- 配置目录优先级：`-D/--dir` > 环境变量 `ALIYUN_CONTROLLER_CONFIG_DIR` > `~/.config/aliyun-controller`。
- `main.py` 在导入 `app` 之前写入该环境变量。
- 配置文件 `config.yaml` 字段：`access_key_id`、`access_key_secret`；`config.load_config()` 校验，`config.save_config()` 写盘。
- 首次启动或配置损坏时进入 `screens/setup.py` 的 `SetupWizardScreen`；主菜单「设置」可改密钥。

## 新增功能指引

1. 服务逻辑放到顶层模块（不要再建 `modules/` 子包），只返回数据或抛异常。
2. 新页面直接继承 `ui.PageScreen`（键位/顶栏/提示栏/折叠菜单自动获得）；如需新客户端，在 `app.py` 增加懒加载 getter（参考 `get_billing()` / `get_dns()`）。
3. 在 `screens/home.py` 的 `ITEMS` 与 `_open()` 注册入口（记得同步数字快捷键）。
4. 样式复用 `app.tcss` 的 `.page-hint` / `.tbl` / `.panel` / `.sub-toolbar` / `.modal-*` / `.btn-row`；能进 `ui.py`/`widgets.py` 复用的组件不要写死在单页里。

## 常见陷阱

- 不要让屏幕方法命名为 `_render`，它会覆盖 `Widget._render` 导致渲染崩溃（账单屏用的是 `_show_result` / `_show_error`）。
- 避免使用 Textual `App` 已有的只读属性名（如 `debug`）作为实例属性。
- 一切阻塞调用走 `asyncio.to_thread`，否则界面卡死。
- 应用代码禁止 `print`（会破坏 TUI）。
- Textual CSS 无 `:not()` / `z-index` / `top` / `left`；`Spinner`、`.instant` 类在 8.2.8 不存在（见 `docs/textual-dev-guide.md`）。
- Textual CSS 不支持相邻兄弟选择器 `+`（`.a + .a` 直接解析报错），行间距用显式类（如 `.filter-row.gap-top`）；`max-height` 不接受 `none`，不限制就删掉该声明。
- `content-align` 只管自身内容，不管子件排布；容器内子件整体居中用 `align: center middle`。
- 8.2.8 `textual.widgets` 没有 `Spacer`，横向撑开用 `Static(classes="fill")`（`width: 1fr`）。
- 自定义 Message 要支持 `@on(..., "#id")` 必须提供 `control` 属性（返回 `self._sender`，没有公开的 `.sender`）。
- 单元格点击的行/列从 `event.style.meta["row"/"column"]` 取；子类 `on_click` 先于原生 `DataTable._on_click` 派发（MRO 逐类 `_on_x` 与 `x` 都会被调用），据此可做单击执行且不与双击/回车重复（比较 `Coordinate(row, col) == cursor_coordinate`）。**接管和回落两条路径都要 `event.prevent_default()`**，否则父类 handler 二次执行 → 消息双发、需要按两次 Esc 才能返回。
- 冒泡到 Screen 的鼠标事件 `event.x/y` 是相对坐标，定位浮层外点击/行点击一律用 `event.screen_x/screen_y`。
- 浮层（月份面板/折叠菜单）的父容器必须声明 `layers: base overlay`，否则鼠标命中下层组件；Button 布局步进含 `line-pad`（默认 1，即 +2），紧凑网格要同时定 `width/min-width` 并把容器加宽。
- `cell_len` 在 `rich.cells`（复数模块名）。
- 表格列宽/表头/右对齐单元格必须**同一帧同源**生成：垂直滚动条是否出现要用行数确定性预判（`fit_table_columns(..., rows=n)`），不要依赖 `max_scroll_y`（装载前后会变），更不要只重设列宽/表头而不重建单元格（表现为首屏右对齐列偏移、刷新后自愈）。`_rebuild` 会再 `call_after_refresh` 完整重建一次以对齐最终布局。
- `aliyun_controller/modules/` 已删除并扁平化，勿恢复；`InquirerPy` 已移除，勿引入。

## 依赖与质量

- `pyproject.toml`：`textual>=8.0`、`PyYAML`、`alibabacloud-*`；`[tool.ruff]` 配置为 `line-length = 120`，`select = ["E","F","W","I","UP","B","C4"]`，忽略 `E501`。
- 依赖变更后同步更新 `requirements.txt`；UI 用到新 Textual 特性时同步抬版本下限。

## 验证

- 仓库**不提交**测试文件；验证使用一次性脚本放在 `/tmp`（本次为 `/tmp/opencode/aliyun_ui_check.py`，可作为模板）。
- 做法：把 `ALIYUN_CONTROLLER_CONFIG_DIR` 指向临时目录并写入 `config.yaml`，用 `App.run_test(size=(120, 40))` 驱动；注入 `app._billing = FakeBilling(); app._dns = FakeDns()` 屏蔽网络。
- 至少覆盖：宽窄终端降级、各页挂载、`#page.virtual_size.height <= container_size.height`（单滚动条）、表格内部可滚动、月份切换、QuotaBar 像素宽度、折叠菜单开合、模态紧凑度（`.modal-box` 高度）、`Ctrl+R/N/E/D`、Esc 层级（模态 > 菜单 > 返回）。
- 断言 DOMQuery 用 `len(query)`，没有 `.count()`；滚动上限是 `max_scroll_y`，没有 `scroll_max`。

## 提交

- 不要执行 `git commit` 等写操作，由用户自行提交。
