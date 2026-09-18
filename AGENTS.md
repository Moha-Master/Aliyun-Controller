# AGENTS.md — aliyun-controller

# 第一部分 · UI 开发统一规范

> 本部分在 aliyun-controller 与 `../clash-controller` 两项目间**保持同一份文本**（个别差异会显式标注）。任何一侧修订必须同步另一侧，保证隔壁项目的 Agent 拿到相同标准。
> 依据均为本地 Textual 8.2.8 实测结论，逐条验证记录见 `docs/textual-dev-guide.md`。

## 1. 技术栈与代码质量

- Python >= 3.10；`textual >= 8.0`（依赖 compact Input、`overlay: screen` 等 8.x 特性）；UI 用到新特性时同步抬版本下限。
- 入口命令 `<pkg>ctl`，支持 `-D/--dir`（指定配置目录，main 在 import app 前写环境变量）与 `--version`。
- ruff：`line-length = 120`，`select = ["E","F","W","I","UP","B","C4"]`，忽略 `E501`；改动后必须零告警。
- 应用代码**禁止 `print`**（破坏 TUI）；调试信息走 `app.add_log` / 状态栏。
- 仓库不提交测试文件；验证用 `/tmp` 一次性脚本（模板见第二部分「验证」）。

## 2. 分层架构

- 展示层：`screens/`、`ui.py`、`widgets.py`、`app.tcss`。`ui.py` 只放可复用界面组件与页面骨架，不写业务取数。
- 服务层：顶层模块（不建 `modules/` 子包），零 Textual 依赖、不 print；失败统一抛项目异常类型（如 `AliyunApiError` / `RuntimeError`），由屏幕层捕获后 `notify_err`。
- 屏幕层通过 App 的懒加载 getter 拿客户端（`app.get_billing()` 模式）。

## 3. App 级约定

- 无 `Header` / `Footer`；App `compose()` 只 yield 一个 `#status`（`height: 1; dock: bottom`）。
- `ENABLE_COMMAND_PALETTE = False`。
- App 绑定 `Binding("ctrl+q", "quit", priority=True)`——任意焦点/输入态都能退出。
- 状态栏 API：`app.status(msg, kind)`，`kind ∈ "" | ok | warn | err`（对应绿/黄/红着色）；`app.notify_ok/notify_err/notify_warn`（status + 右下角 toast）。
- 不要用 `App` 已有只读属性名（`debug` 等）作实例属性（用 `debug_mode` 之类）。

## 4. 页面骨架（ui.PageScreen）

- 功能页一律继承 `ui.PageScreen`，结构固定：
  顶栏 `#topbar`（左 `◀ 返回` 按钮 + Title/SubTitle 靠左；右侧 `compose_toolbar()` 控件 + 可选折叠菜单按钮）→ `#page`（`height: 1fr; overflow-y: hidden`）→ 底部 `.page-hint` 提示栏贴底。
- 子类只写：`TITLE` / `HINT`（及可选 `SUBTITLE`）、`compose_page()`、`compose_toolbar()`、`reload_page()`；键位/Esc/滚动自动获得。
- 副标题动态更新用 `set_subtitle()`；根屏（启动页/选择页）若需与首页一致的居中版式，可脱离 PageScreen 采用 `Screen` + `.logo-page`（`ui.LOGO_LINES` + `OptionList`，键位/Esc/Ctrl+C 需自绑）；亦可复用 PageScreen 覆写 `action_page_back()` 为 `app.exit()`，并把返回按钮 label 改成 `◀ 退出`。
- 折叠菜单 `self.menu = [(label|callable, callback, danger)]`：语义对齐 Android ⋮，**只放次要/低频项**；主要操作必须以容器内按钮常驻可见；`menu` 为空时菜单按钮自动不渲染；危险项文本染红。
- 首页：`ui.LOGO_LINES` 艺术字居中 + 版本副标题右对齐 + 菜单项居中且文本左对齐 + 底部提示栏。窄终端在 `on_resize` 判断 `width < LOGO_WIDTH + 6` 挂 `-sm` 类降级为普通文本。整组居中用容器 `align: center middle`（`content-align` 不定位子件）。

## 5. 内容区版式与滚动

- 主模式「**小容器 + 大容器**」：小容器 `.panel`（`padding: 1 2; border: round`）或 `.filter-row` 行放筛选/排序/主操作按钮（按钮 `compact=True`）；大容器是表格（`make_table()` 自带 `.tbl` 类 → `height: 1fr`）。排列方向可变（上大下小 / 上小下大 / 左右）。
- 只读状态行用 `.kv-row/.kv-label/.kv-value`（标签定宽、值加粗）。
- **外层永不滚动**：`#page` overflow hidden；固定行 `height: auto`。`Vertical`/`Horizontal` 默认 `height: 1fr`，放进 auto 上下文必须显式 `height: auto`。全页只允许表格一个滚动条。
- Home/End/PgUp/PgDn 由 PageScreen 绑定（不加 priority，Input 聚焦时让位），目标 `_scroller()`：优先 `#page` 内第一个 VerticalScroll，否则 `#page`；表格滚动走 DataTable 自身键位。

## 6. 模态（widgets.py）

- 全部为 `ModalScreen[T]`，统一三段式：`.modal-title`（`border-bottom: hkey`）→ 内容区 → `.btn-row`（`border-top: hkey; dock: bottom; align-horizontal: right`）。
- 按钮语义：default=一般、primary=推荐、error=危险；顺序 `[取消] [主操作] … [危险放最右]`。
- `ConfirmModal`：回车=确认；不可逆操作 `default_yes=False`（确认按钮变 error 色、回车不再直接放行按钮，仅按钮点击生效）。
- `InputModal`：单行 + validator，留空=不更改语义由调用方决定；`[取消][确定]`。
- `FormModal`：字段 `kind: text | password | choice | switch | note`（clash 另有 `textarea` 供长文本如私钥）。Input/Select 一律 `compact=True`；`.frm-row { margin-bottom: 1 }`、无 hint 行高恰好 1；`.modal-box { min-height: 24 }`；底部错误行 `#frm-error`（validator 失败提示且不关闭）。
  - 密码框：占位符显示脱敏原值，**留空 = 保持原值**。
  - 布尔开关：表单内 `kind="switch"`（Switch 用 `border: none; height: 1` 压单行），**不要**独立动作按钮、不要 Checkbox。
  - 焦点：`on_mount` 聚焦第一个字段；Tab/Shift+Tab 轮切（Screen 原生焦点链）；Enter（Input.Submitted）= 提交；Esc / Ctrl+C = 取消。
- `OutputModal`：只读长文本，VerticalScroll + `[关闭]`。
- **不要用行内 `display` 切换做就地编辑**——隐藏 Input 不进 Tab 焦点链、编辑态难以退出（已被否，用 FormModal）。
- 调用：worker 内 `await app.push_screen_wait(modal)`；事件回调里 `app.push_screen(modal, callback)`。

## 7. 键位总表（统一，勿另起方案）

| 键 | 行为 |
|---|---|
| `↑↓←→` | 移动焦点/光标 |
| `Tab` / `Shift+Tab` | 轮切焦点（表单内轮切字段） |
| `Enter` | 确认 / 提交 / 执行所选行 |
| `Esc` / `Ctrl+C` | 关闭层级：**浮层 > 模态 > 返回上一页**；**根屏 / 主菜单为退出程序**（Textual 无默认 Esc，每屏必须自绑） |
| `Ctrl+Q` | **任意界面**直接退出程序（App priority 绑定，浮层 / 模态 / 输入态均生效） |
| `Home/End/PgUp/PgDn` | 功能页（PageScreen）滚动（不抢 Input 的文本导航） |
| `Ctrl+R` | 功能页刷新（调 `reload_page()`） |
| `Ctrl+N` / `Ctrl+E` / `Ctrl+D` | 条目页 新增 / 编辑 / 删除 |
| 单字母 `1-9` | 仅限**无输入框的主菜单 / 列表页**（快捷直达） |
| 单字母 `/` | 仅限**确有搜索框的列表页**（聚焦搜索；无搜索框的项目不设） |

- **无 `q` 绑定**：退出统一走 `Ctrl+Q`（任意界面）与 `Esc`/`Ctrl+C`（根屏 / 主菜单）。
- **提示栏只写本页真实有效的键**：`.page-hint` / `HINT` 必须与本页 `BINDINGS` + 继承绑定逐条对应，不得出现该页不可用的键。
- `Ctrl+C` 在 `Input` / `TextArea` 聚焦时仍是「复制文本」（Textual 框架行为），其余位置等价于 `Esc`。

### 7.1 两项目键位设计对照

> 原则：能对齐就对齐；确无对应操作/确实多出操作的，允许差异并在此登记。

| 场景 / 页面 | aliyun-controller | clash-controller |
|---|---|---|
| App 全局 | `Ctrl+Q` 任意退出；`Tab/Shift+Tab` 焦点轮切 | 同左 |
| 功能页基类 PageScreen | `Esc`/`Ctrl+C` 返回、`Ctrl+R` 刷新、`Home/End/PgUp/PgDn` 滚动 | 同左 |
| 首启 / 根屏 | `SetupWizardScreen`：`Enter` 提交、`Esc`/`Ctrl+C` 取消并退出 | `ProfileListScreen`：`↑↓` 移动、`Enter`/单击 连接、`1-9` 直连、`Ctrl+N/E/D` 增删改、`Ctrl+R` 刷新、`Esc`/`Ctrl+C` 退出 |
| 主菜单 | `HomeScreen`：`↑↓`、`Enter`、`1-5` 直达、`Esc`/`Ctrl+C` 退出 | `HomeScreen`：`↑↓`、`Enter`、`1-4` 直达、`Esc`/`Ctrl+C` 退出（切换端点走菜单项 `4`） |
| 域名列表 | `DomainListScreen`：`↑↓`、`Enter`/单击进入、`/` 搜索、`Ctrl+R`、`Esc`/`Ctrl+C` 返回（无 `Ctrl+N/E/D`） | —（无对应页） |
| 条目列表 | `RecordListScreen`：`↑↓`、`Enter`/单击编辑、`/` 搜索、`Ctrl+N/E/D`、`Ctrl+R`、`Esc`/`Ctrl+C` 返回 | `ConfigScreen`：`↑↓`、`Enter`/单击部署、`Ctrl+N/E/D`、`Ctrl+R`、`Esc`/`Ctrl+C` 返回（无搜索框故无 `/`） |
| 监控 / 查看 | `TrafficScreen` / `SummaryScreen`：`◀▶` 切月、`Ctrl+R`、`Esc`/`Ctrl+C` 返回、滚轮浏览 | `OverviewScreen`：`↑↓/PgUp/PgDn` 滚动连接表、`Ctrl+R`、`Esc`/`Ctrl+C` 返回（无账期故无 `◀▶`） |
| 设置 | `SettingsScreen`：点击条目/`Enter` 开 FormModal 保存、`Esc`/`Ctrl+C` 返回 | `SettingsScreen`：`Tab` 轮切、`Enter` 操作控件即时生效、`Ctrl+R`、`Esc`/`Ctrl+C` 返回（无保存动作） |
| 日志 | ➖ 无独立日志页 | `LogScreen`：`↑↓/PgUp/PgDn` 滚动、`Ctrl+R` 重载、`Esc`/`Ctrl+C` 返回 |
| 模态 | `Enter` 提交/确认、`Esc`/`Ctrl+C` 取消关闭、`Tab` 轮切字段 | 同左 |
| 文本编辑（Input/TextArea） | 控件原生编辑键优先；`Ctrl+R` 刷新禁用；底部提示栏切 `EDIT_HINT` | 同左 |

- 单字母键（`1-9`/`/`）只在**无输入框**页面保留；有 Input 的页面一律用 `Ctrl` 组合键或方向键。
- `1-9` 的位数按各项目菜单/列表实际条目上限确定（aliyun 1-5、clash 首页 1-4、clash 端点页 1-9）。

### 7.2 文本编辑模式（焦点在 Input / TextArea）

- 焦点进入 `Input` / `TextArea` 即视为**文本编辑模式**：控件原生编辑键位优先于页面 / App 的普通绑定（Textual 绑定链按「焦点控件 → 祖先 → Screen → App」查找），无需额外代码即生效。
- Textual 内置编辑键（勿重复实现）：`←→`/`Ctrl+←→`、`Home`/`Ctrl+A` 行首、`End`/`Ctrl+E` 行尾、`Ctrl+Shift+A` 全选、`Shift+方向` 选择、`Backspace`、`Delete`/`Ctrl+D`、`Ctrl+W/U/K` 删词 / 清左 / 清右、`Ctrl+X/C/V` 剪切 / 复制 / 粘贴；`TextArea` 另有 `↑↓`、`PgUp/PgDn`、`Ctrl+Z/Y` 撤销 / 重做、`Ctrl+Shift+K` 删行。
- 冲突规则（统一约定）：
  - **编辑态禁用页面刷新 `Ctrl+R`**：`PageScreen.check_action` 在焦点为 `Input`/`TextArea` 时对 `refresh_page` 返回 `False`。
  - `Ctrl+C`：有选中 → 复制；无选中 → `Input.action_copy` 抛 `SkipAction` 冒泡为「返回 / 取消」（无需额外代码）。
  - `PgUp/PgDn`：保留控件原生行为（`Input` 未绑则冒泡页面滚动，`TextArea` 为光标翻页）。
  - `Tab`/`Shift+Tab`：保留焦点轮切（`TextArea` 保持默认 `tab_behavior="focus"`；改 `"indent"` 会吞 `Tab`，不建议）。
  - `Ctrl+Q`：不受编辑模式影响，任意界面退出。
  - 终端限制：`Ctrl+V` 走 Textual 剪贴板（OSC52），不保证系统剪贴板；`Ctrl+A` 是**行首**（全选是 `Ctrl+Shift+A`）。
- 底部提示栏联动：焦点进出输入框时，页面 `#page-hint` 在 `HINT` 与 `EDIT_HINT` 间切换（`ui.PageScreen._refresh_hint`）；模态（`FormModal`/`InputModal`）在 `#frm-hint`/`#im-hint` 按焦点类型显示 `HINT_FORM`（非输入）/ `HINT_FORM_EDIT`（Input）/ `HINT_FORM_TA`（TextArea）。常量定义在 `widgets.py`。
- **约束**：有搜索框（Input）的列表页不要用 `Ctrl+E`/`Ctrl+D` 做条目操作（会被 Input 的「行尾 / 右删」抢占），改用 `Ctrl+N` 与容器内按钮或 `Enter`。

## 8. 鼠标交互

- **所有可操作行一律单击执行**：数据表用 `widgets.ClickTable`（`make_table()` 默认返回）。单击数据格 = 移光标 + 立即发 `RowSelected`；表头点击仍是 `HeaderSelected`。
- 按钮/选项/入口同样单击触发；滚轮天然作用于光标下滚动容器。
- 行点击判定、浮层外点击判定一律用 `event.screen_x / screen_y`（冒泡到 Screen 的 `event.x/y` 是相对坐标）。
- 单击即执行的高危操作必须仍有 ConfirmModal 把关。

## 9. 表格规范

- 数据展示一律 `DataTable` + `make_table()` / `load_rows()`；禁止手写空格对齐字符串伪表格。
- 内容列 `shorten(text, cap)` 截断（cap 按列典型宽度定）；右对齐列（数字/状态）用 `rcell(text, width)` 前导空格补齐，**表头同样 rcell**。
- 列宽：`fit_table_columns(table, weights, rows=即将装载的行数)` 按权重瓜分容器宽（返回值含每列左右 2 空格 padding）。
- **列宽/表头/单元格必须同一帧同源生成**：垂直滚动条用行数确定性预判（`rows > region.height - 3` 预留 2 列），不要依赖 `max_scroll_y`（装载前后会变）；布局稳定后整表重建一次（`call_after_refresh`），勿只改列宽不重建单元格。
- 列宽在装载函数与 `on_resize` 里重算（`on_resize` 直接定义即可，`Screen` 没有可 `super()` 的默认实现）。
- `.tbl` 加 `overflow-x: hidden` 兜底防横向滚动条。

## 10. 浮层（页内弹层）三要素

- 自定义 `Message` 类要能被 `@on(..., "#id")` 匹配必须提供 `control` 属性（返回 `self._sender`）。
- 浮层父容器必须声明 `layers: base overlay`，否则鼠标命中下层组件。
- 浮层本体：`position: absolute; overlay: screen; layer: overlay; display` 切换；定位用 `styles.offset`；右缘控件的浮层右对齐展开并防越界。
- Button 布局步进含 `line-pad`（默认 1，即 +2）：紧凑网格必须同时定 `width`/`min-width` 并加宽容器。

## 11. 取数与并发逻辑

- 阻塞调用（云 SDK / requests / paramiko / subprocess）**一律** `asyncio.to_thread`，包在 `@work(exclusive=True)` worker 里，`on_mount` 触发。
- 实时流：`@work(thread=True)` + `call_from_thread` 回 UI 线程；`on_unmount` 置停止位并 close response，防线程泄漏。
- 轮询：`set_interval` + busy 标志去重；`on_unmount` 停 timer。
- 展示刷新函数与列宽计算解耦：`_rebuild/_load`（数据 → 单元格 + fit）与 worker（取数）分开。

## 12. Textual CSS / 8.2.8 通用陷阱

- 无 `:not()`、无 `z-index`、无 `top`/`left`（用 `offset` 样式属性）、无相邻兄弟选择器 `+`（解析直接报错，行距用显式类如 `.filter-row.gap-top`）。
- `max-height` 不接受 `none`，不限制就删声明。
- 8.2.8 无 `Spacer`（横向撑开用 `Static(classes="fill")` + `width: 1fr`）、无 `Spinner`、无 `.instant` 类。
- `cell_len` 在 `rich.cells`（复数模块名）。
- 自定义 `DataTable` 子类重写 `_on_click`：Textual 沿 MRO 逐类派发，接管与回落两条路径都要 `event.prevent_default()`，否则消息双发/双执行。
- 屏幕方法不要命名 `_render`（覆盖 `Widget._render` 渲染崩溃）。
- `textual.widgets` 各版本导出位置不同，`Option` 用 try/except import。
- `Select` 内部 `#label` 是 `width: 1fr`：放进 `Horizontal` 行且不给显式宽度时，auto 宽度会把整行剩余空间全吃掉，`▼` 箭头溢出容器被裁剪——行内 Select 必须显式定宽（如设置页模式 `width: 12`）。

## 13. 验证规范（两项目同一模板）

- `App.run_test(size=(120, 40))` 宽屏 + `(LOGO_WIDTH+6 以下, 28)` 窄屏各跑一遍。
- 配置目录指向 `tempfile.mkdtemp()`（写最小配置），服务层用 Fake 对象整体屏蔽网络。
- 必测清单：各页挂载无异常；`#page.virtual_size.height <= container_size.height`（单滚动条）；表格列宽和 ≈ `region.width - 2` 且无横向溢出；单击行执行且**不重复压栈**；`Ctrl+N/E/D/R`；模态紧凑度（min-height/单行输入/行距/Tab 轮切/validator 拦截）；折叠菜单浮层开合与外部点击；Esc 层级（模态 > 菜单 > 返回）；窄终端 logo `-sm` 降级与列宽重算。
- 断言 API 坑：DOMQuery 计数用 `len(query)`（无 `.count()`）；滚动上限 `max_scroll_y`（无 `scroll_max`）；Pilot 无 `shift_tab()`，用 `pilot.press("shift+tab")`；Fake 服务要**带状态**（setter 改 getter 返回值）。

## 14. 跨项目组件对照

| 组件 | aliyun-controller | clash-controller |
|---|---|---|
| `ui.PageScreen` / `ui.LOGO_*` | ✅ | ✅ |
| `ui.MonthPicker` / `ui.QuotaBar` | ✅（账期/额度场景） | ➖ 无场景未搬运，需要时从 aliyun `ui.py` 移植 |
| `widgets.ClickTable` / `shorten` / `rcell` / `fit_table_columns` | ✅ | ✅ |
| `widgets` 四模态 | ✅（FormModal 无 textarea） | ✅（FormModal 多 `textarea` kind） |
| 模糊筛选 `filter_fuzzy` | ✅ | ✅ |

---

# 第二部分 · 本项目（aliyun-controller）

## 项目概览

- 基于 Textual 的终端 TUI，调用阿里云 SDK 完成账单 / 流量查询与 DNS 解析管理。
- 包名 `aliyun_controller`，命令 `aliyunctl`。
- 界面与分层风格与 `../clash-controller`、`../litellm-controller`、`../llm-price-compare` 保持一致。

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

分层与服务约定见第一部分 §2；本项目服务层失败统一抛 `AliyunApiError`（数据不合法抛 `ValueError`）。

## 本项目界面实例（对第一部分规范的映射）

- 首页：5 菜单项（流量/账单/DNS/设置/退出），数字快捷键 1-5；`Esc`/`Ctrl+C` 退出程序（不绑 `q`）。
- 账单/流量页：上小下大——汇总 `.panel`（含 `ui.QuotaBar` 与刷新按钮）+ 明细 `.tbl`；账期切换在顶栏 `ui.MonthPicker`（Changed 消息自动重查）。
- DNS 域名页/记录页：`.panel` 小容器（搜索框 + 排序 Select + 增删改按钮）+ ClickTable（单击行进记录页/开编辑弹窗）；列 cap：主机记录 20、记录值 40；TTL/状态右对齐 rcell。
- 设置页：条目展示（AK 脱敏 `LTAI…3456` + hover「点击编辑」）→ 点击条目或 `[编辑访问密钥]` 打开 FormModal（ID 预填、Secret 密码框留空=不变、Tab 轮切、保存/取消）；保存后 `app.reload_config()` 立即生效。
- 首次启动/配置损坏进入 `screens/setup.py` 向导屏。
- 文本编辑模式：DNS 域名页/记录页搜索框聚焦时 `#page-hint` 切为 `EDIT_HINT` 并禁用 `Ctrl+R`；`FormModal`/`InputModal` 底部 `#frm-hint`/`#im-hint` 按焦点类型切换 `HINT_FORM`/`HINT_FORM_EDIT`/`HINT_FORM_TA`（详见第一部分 §7.2）。

## 配置与文件

- 配置目录优先级：`-D/--dir` > 环境变量 `ALIYUN_CONTROLLER_CONFIG_DIR` > `~/.config/aliyun-controller`。
- `main.py` 在导入 `app` 之前写入该环境变量。
- 配置文件 `config.yaml` 字段：`access_key_id`、`access_key_secret`；`config.load_config()` 校验，`config.save_config()` 写盘。

## 新增功能指引

1. 服务逻辑放到顶层模块（不要再建 `modules/` 子包），只返回数据或抛异常。
2. 新页面直接继承 `ui.PageScreen`；如需新客户端，在 `app.py` 增加懒加载 getter（参考 `get_billing()` / `get_dns()`）。
3. 在 `screens/home.py` 的 `ITEMS` 与 `_open()` 注册入口（记得同步数字快捷键）。
4. 样式复用 `app.tcss` 的 `.page-hint` / `.tbl` / `.panel` / `.sub-toolbar` / `.filter-row` / `.kv-row` / `.modal-*` / `.btn-row`；能进 `ui.py`/`widgets.py` 复用的组件不要写死在单页里。

## 本项目陷阱与历史决定

- `aliyun_controller/modules/` 已删除并扁平化，勿恢复；`InquirerPy` 已移除，勿引入。
- 账单屏的结果方法叫 `_show_result` / `_show_error`（`_render` 陷阱的具体案例）。
- MonthPicker 浮层常量 `POP_WIDTH = 26` 与 CSS `#mp-pop width` 必须一致。
- 单元格点击行/列从 `event.style.meta["row"/"column"]` 取；ClickTable 用「`Coordinate == cursor_coordinate` 则不再二次处理」避免与原生双击/回车重复。

## 依赖与质量

- `pyproject.toml`：`textual>=8.0`、`PyYAML`、`alibabacloud-*`。
- 依赖变更后同步更新 `requirements.txt`。

## 验证（本项目脚本）

- `/tmp/opencode/aliyun_ui_check2.py`（35 断言：取数流程/键位/Esc 层级）、`/tmp/opencode/aliyun_ui_check3.py`（48 断言：月份浮层/ClickTable/列宽/QuotaBar/设置弹窗），可作新脚本模板。
- 做法：`ALIYUN_CONTROLLER_CONFIG_DIR` 指向临时目录并写 `config.yaml`；`app._billing = FakeBilling(); app._dns = FakeDns()` 屏蔽网络。

## 提交

- 不要执行 `git commit` 等写操作，由用户自行提交。
