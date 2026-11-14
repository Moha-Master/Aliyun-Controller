# AGENTS.md — aliyun-controller

# 第一部分 · UI 开发统一规范

> 本部分在 aliyun-controller、`../clash-controller`、`../litellm-controller`、`../llm-price-compare`、`../yt-dlp-controller` 五项目间**保持同一份文本**（个别差异会显式标注；§7.1 键位对照与 §14 组件对照表目前只覆盖前四个项目）。任何一侧修订必须同步其余项目，保证各项目的 Agent 拿到相同标准。
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
- 副标题动态更新用 `set_subtitle()`；根屏（启动页/选择页）若需与首页一致的居中版式，采用 `Screen` + `.logo-page`（`ui.LOGO_LINES` + `OptionList`）；亦可复用 PageScreen 覆写 `action_page_back()` 为 `app.exit()`，并把返回按钮 label 改成 `◀ 退出`。
- 折叠菜单 `self.menu = [(label|callable, callback, danger)]`：语义对齐 Android ⋮，**只放次要/低频项**；主要操作必须以容器内按钮常驻可见；`menu` 为空时菜单按钮自动不渲染；危险项文本染红。
- 首页：`ui.LOGO_LINES` 艺术字（`#home-banner` **`text-align: left`**，整块靠 `#home-col` 居中，避免逐行居中造成错位）+ 版本副标题右对齐 + 菜单项 + 底部提示栏。列宽由 `ui.home_col_width()`（= `max(LOGO_WIDTH, 40)`，`LOGO_WIDTH` 由 `cell_len(line.rstrip())` 自动计算）在 `on_resize` 动态写回 `#home-col`，版本号因此始终对齐 Logo 右缘。窄终端 `width < LOGO_WIDTH + 6` 挂 `-sm` 降级为普通文本。整组居中用容器 `align: center middle`（`content-align` 不定位子件）。
- 首页菜单描述**右对齐**；描述超宽时用 `widgets.MenuMarquee` 在选项区域内来回跑马灯（不换行）。

## 5. 内容区版式与滚动

- 主模式「**小容器 + 大容器**」：小容器 `.panel`（`padding: 1 2; border: round`）或容器内 `.filter-row` 行放筛选/排序/主操作按钮；大容器是表格（`make_table()` 自带 `.tbl` 类 → `height: 1fr`）。排列方向可变（上大下小 / 上小下大 / 左右）。
- **一切内容皆入容器**：功能页所有可见组件必须包裹在 `.panel` 或 `.tbl` 内，严禁直接在 `#page` 下漂浮。
- 只读状态行用 `.kv-row/.kv-label/.kv-value`（标签定宽、值加粗）。
- **外层永不滚动**：`#page` overflow hidden；固定行 `height: auto`。`Vertical`/`Horizontal` 默认 `height: 1fr`，放进 auto 上下文必须显式 `height: auto`。全页只允许表格一个滚动条。
- Home/End/PgUp/PgDn 由 PageScreen 绑定（不加 priority，Input 聚焦时让位），目标 `_scroller()`：优先 `#page` 内第一个 VerticalScroll，否则 `#page`；表格滚动走 DataTable 自身键位。

## 6. 模态（widgets.py）

- **`Button` 全局单行**：各项目 `app.tcss` 统一 `Button { height: 1; border: none; min-width: 0; padding: 0 1 }` + `Button:focus { text-style: bold reverse }`（等价于旧 `compact=True`，调用处不再传）；变体色（primary/error/…）即背景色。**同属性下 app.tcss 元素规则会压过一切 widget `DEFAULT_CSS` 类/ID 规则**，加全局控件样式前必须审查小尺寸控件（见 §12）。
- 全部模态为 `ModalScreen[T]`，统一三段式：`.modal-title`（`border-bottom: hkey`）→ 内容区 → `.btn-row`（`border-top: hkey; dock: bottom; align-horizontal: right`）。
- 按钮语义：default=一般、primary=推荐、error=危险；顺序 `[取消] [主操作] … [危险放最右]`。
- `ConfirmModal`：**回车 = 触发当前焦点按钮**（不再无条件确认）。可逆操作 `default_yes=True`：`on_mount` 聚焦「确认」，回车即确认；不可逆操作 `default_yes=False`：确认按钮变 error 色且 `on_mount` 聚焦「取消」——回车仍然响应（等于放弃），必须 Tab 到「确认」或直接点击才放行。`on_key` 里不要再拦回车。
- `InputModal`：单行 + validator，留空=不更改语义由调用方决定；`[取消][确定]`。
- `FormModal`：字段 `kind: text | password | choice | switch | browse | note`（clash/litellm 另有 `textarea` 供长文本如私钥；`browse` = Input + 「选择…」弹 `PickModal` 回填，`choice` 也可带 `pick_items` 在 Select 旁加「更多…」按钮）。
  - 动态字段：`FormField.show_if: Callable[[dict], bool]` 按当前原始值过滤；`FormField.driver=True` 的 choice 变化时调用 `dynamic_fields()`，由 `FormModal.rebuild_fields()` 用 `remove_children()` + `mount_all()` **真实增删行**（保留同名字段值、清空 `#frm-error`、重建后聚焦 driver）。**严禁用行内 `display` 切换**（隐藏 Input 不进 Tab 焦点链）。
- 字段过多的复合表单（如模型参数、路由组成员勾选）用 **sheet 式模态**：`ModalScreen` + `.modal-box` 定宽（≤104）/ `height 88~90%` + `.modal-title` + `VerticalScroll` 内容 + docked `.btn-row`；不拆多步问询弹窗串联。Input/Select 一律 `compact=True`；`.frm-row { margin-bottom: 1 }`、无 hint 行高恰好 1；`.modal-box { min-height: 24 }`；底部错误行 `#frm-error`（validator 失败提示且不关闭）。
  - 密码框：占位符使用 `widgets.mask_secret()` 脱敏显示，**留空 = 保持原值**。
  - 布尔开关：表单内 `kind="switch"`（Switch 用 `border: none; height: 1` 压单行），**不要**独立动作按钮、不要 Checkbox。
  - **`Switch` / `Checkbox` / `RadioButton` 一律全局压成一行**（`height: 1; border: none; padding: 0 1`，focus 用 background-tint）：litellm 在 `app.tcss` 全局规则；aliyun/clash 在 `.frm-field` / `.kv-row` 上下文同名规则。
  - `extra_buttons`（按按钮 id 返回结果）与功能型按钮必须互斥：如 litellm 的 `browse`「选择…」按钮在 `on_button_pressed` 里按 `.frm-browse` 类名排除，否则会被当额外按钮误 dismiss（历史事故：编辑 Upstream provider 崩溃）。
  - 焦点：`on_mount` 聚焦第一个字段；Tab/Shift+Tab 轮切（Screen 原生焦点链）；Enter（Input.Submitted）= 提交；Esc / Ctrl+C = 取消。
- `OutputModal`：只读长文本，VerticalScroll + `[关闭]`。
- `PickModal`（从 litellm 移植）：模糊筛选单选列表，`ModalScreen[PickItem | None]`，搜索框 + OptionList + `HINT_PICK` + docked `.btn-row`；`PickItem.display()` 参与模糊匹配，`disabled` 项不可选。
- **定宽 `.modal-box` 必须有窄屏兜底**：写成 `width: 92; max-width: 95%`（aliyun/clash/litellm/lpc 现状）或 `width: 90%; max-width: 92`（yt-dlp 写法）皆可，禁止裸 `width: N`——固定宽度在窄终端（<100 列）会顶破屏幕。改完必须实测窄屏 `region.right <= screen.width`。
- **不要用行内 `display` 切换做就地编辑**——隐藏 Input 不进 Tab 焦点链、编辑态难以退出（已被否，用 FormModal）。
- 调用：worker 内 `await app.push_screen_wait(modal)`；事件回调里 `app.push_screen(modal, callback)`。

## 7. 键位总表（统一，勿另起方案）

| 键 | 行为 |
|---|---|
| `↑↓←→` | 移动焦点/光标 |
| `Tab` / `Shift+Tab` | 轮切焦点（表单内轮切字段） |
| `Enter` | 确认 / 提交 / 执行所选行（确认框里 = **当前焦点按钮**，见 §6） |
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

### 7.1 四项目键位设计对照

> 原则：能对齐就对齐；确无对应操作/确实多出操作的，允许差异并在此登记。

| 场景 / 页面 | aliyun-controller | clash-controller | litellm-controller | llm-price-compare |
|---|---|---|---|---|
| App 全局 | `Ctrl+Q` 任意退出；`Tab/Shift+Tab` 焦点轮切 | 同左 | 同左 | 同左；另有 `Ctrl+S` 全局打开设置 |
| 功能页基类 PageScreen | `Esc`/`Ctrl+C` 返回、`Ctrl+R` 刷新、`Home/End/PgUp/PgDn` 滚动 | 同左 | 同左 | 同左 |
| 首启 / 根屏 | `SetupWizardScreen`：`Enter` 提交、`Esc`/`Ctrl+C` 取消并退出 | `ProfileListScreen`：`↑↓` 移动、`Enter`/单击 连接、`1-9` 直连、`Ctrl+N/E/D` 增删改、`Ctrl+R` 刷新、`Esc`/`Ctrl+C` 退出 | `SetupWizardScreen`（模态向导）：`Enter` 提交、`Esc`/`Ctrl+C` 取消并退出 | `DashboardScreen`（看板即根屏）：`[`/`]` 切页、`g` 回总览、`1-9` 直达、`←→` 选列、`s` 排序、`Ctrl+R` 刷新、`Ctrl+S` 设置、`Esc/Ctrl+C` 退出；缺配置先弹 `SetupScreen` |
| 主菜单 | `HomeScreen`：`↑↓`、`Enter`、`1-5` 直达、`Esc`/`Ctrl+C` 退出 | `HomeScreen`：`↑↓`、`Enter`、`1-4` 直达、`Esc`/`Ctrl+C` 退出（切换端点走菜单项 `4`） | `HomeScreen`：`↑↓`、`Enter`、`1-4` 直达、`Esc`/`Ctrl+C` 退出 | ➖ 无独立主菜单（看板即根屏） |
| 列表页（带搜索） | `DomainListScreen` / `RecordListScreen`：`↑↓`、`Enter`/双击、`/` 搜索、`Ctrl+N/E/D`、`Ctrl+R`、`Esc` 返回 | `ConfigScreen`：同左但无搜索框故无 `/` | `ModelListScreen` / `RoutingListScreen`：`↑↓`、`Enter`/双击、`/` 搜索、`Ctrl+N`、`Ctrl+R`、`Esc` 返回；`DefaultEditorScreen`：`Ctrl+N`＋[保存/重置]按钮 | ➖ 无通用搜索列表页 |
| 监控 / 查看 | `TrafficScreen` / `SummaryScreen`：`◀▶` 切月、`Ctrl+R`、`Esc` 返回、滚轮浏览 | `OverviewScreen`：`↑↓/PgUp/PgDn` 滚动、`Ctrl+R`、`Esc` 返回（无账期故无 `◀▶`） | `BuildScreen`：`Ctrl+R` 重新构建、[导出 JSON] 按钮、`Esc` 返回 | 同首启/根屏（看板总览与模型 tab） |
| 设置 | `SettingsScreen`：点击条目/`Enter` 开 FormModal 保存、`Esc` 返回 | `SettingsScreen`：`Tab` 轮切、`Enter` 操作控件即时生效、`Ctrl+R`（无保存动作） | `SettingsScreen`：双击/`Ctrl+E` 编辑 Upstream、`Ctrl+N/D`、`Esc` 返回；`ScriptsScreen`：`/` 搜索、`Ctrl+N` 新建、双击 开脚本设置 | `SettingsScreen`：Tabbed（常规/场景/对比组），单击选择，`[✎ 编辑/重命名]` 弹模态，`[🗑 删除]` 弹 ConfirmModal，`[💾 保存]` / `Esc` 放弃（有改动先确认） |
| 日志 | ➖ 无独立日志页 | `LogScreen`：`↑↓/PgUp/PgDn` 滚动、`Ctrl+R` 重载、`Esc` 返回 | ➖ 无独立日志页 | ➖ 无独立日志页（进度/状态走底部状态栏） |
| 模态 | `Enter` 提交/确认、`Esc`/`Ctrl+C` 取消关闭、`Tab` 轮切字段 | 同左 | 同左；另有 `PickModal`（输入筛选 + 回车）与 sheet 式大表单（`ModelFormScreen` 等，按钮提交，无隐式回车） | 同左（SetupScreen、InputModal 重命名、ModelFormModal 动态定价、ConfirmModal 删除） |
| 文本编辑（Input/TextArea） | 控件原生编辑键优先；`Ctrl+R` 刷新禁用；底部提示栏切 `EDIT_HINT` | 同左 | 同左 | 同左 |

- 单字母键（`1-9`/`/`）只在**无输入框**页面保留；有 Input 的页面一律用 `Ctrl` 组合键或方向键。
- `1-9` 的位数按各项目菜单/列表实际条目上限确定（aliyun 1-5、clash 首页 1-4 / 端点页 1-9、litellm 首页 1-4 / 参数主页 1-3）。
- litellm 有搜索框的列表页（`ScriptsScreen`/`DefaultEditorScreen`）只用 `Ctrl+N` 与容器内按钮，不再占用会被 Input 抢占的 `Ctrl+E`/`Ctrl+D`（§7.2 约束）。

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

- **表格行单击只选中、双击才执行**：数据表用 `widgets.ClickTable`（`make_table()` 默认返回）。单击数据格 = 移光标并发 `RowHighlighted`（控制按钮据此刷新），**不发** `RowSelected`；同格 0.5s 内的第二次点击（`events.Click.chain >= 2`）才发 `RowSelected`，与 `Enter` 等价。表头点击仍是 `HeaderSelected`。
- **例外：选择器表格**（选中即生效，如格式列表、主从列表的从属表）显式传 `activate="click"`，单击即发 `RowSelected`；无 `RowSelected` 处理器的表格不必关心该参数。
- 按钮 / `OptionList` 菜单项仍单击触发（菜单语义，单击即确认）；滚轮天然作用于光标下滚动容器。
- 行点击判定、浮层外点击判定一律用 `event.screen_x / screen_y`（冒泡到 Screen 的 `event.x/y` 是相对坐标）。
- 双击执行的高危操作必须仍有 ConfirmModal 把关；`HINT` 一律写「双击/回车 <动作>」，不得写「单击 <动作>」。

## 9. 表格规范

- 数据展示一律 `DataTable` + `make_table()` / `load_rows()`；禁止手写空格对齐字符串伪表格。
- 内容列 `shorten(text, cap)` 截断（cap 按列典型宽度定）；右对齐列（数字/状态）用 `rcell(text, width)` 前导空格补齐，**表头同样 rcell**。
- 列宽：`fit_table_columns(table, weights, rows=即将装载的行数)` 按权重瓜分容器宽（返回值含每列左右 2 空格 padding）。
- **列宽/表头/单元格必须同一帧同源生成**：垂直滚动条用行数确定性预判（`rows > region.height - 3` 预留 2 列），不要依赖 `max_scroll_y`（装载前后会变）；布局稳定后整表重建一次（`call_after_refresh`），勿只改列宽不重建单元格。
- 列宽在装载函数与 `on_resize` 里重算（`on_resize` 直接定义即可，`Screen` 没有可 `super()` 的默认实现）。
- `.tbl` 加 `overflow-x: hidden` 兜底防横向滚动条；`:focus` 态边框也必须保持圆角（`.tbl:focus { border: round $primary }`），否则聚焦时出现直角，与 `.panel` 风格割裂。

## 10. 浮层（页内弹层）三要素

- 自定义 `Message` 类要能被 `@on(..., "#id")` 匹配必须提供 `control` 属性（返回 `self._sender`）。
- 浮层父容器必须声明 `layers: base overlay`，否则鼠标命中下层组件。
- 浮层本体：`position: absolute; overlay: screen; layer: overlay; display` 切换；定位用 `styles.offset`；右缘控件的浮层右对齐展开并防越界。
- Button 内容宽 = 区域宽 − padding(2) − `line-pad`(1×2)；`line-pad: 0` **非法**（须 ≥1），紧凑网格按钮按 `文本宽 + 4` 定 `width`/`min-width` 并加宽容器。

## 11. 取数与并发逻辑

- 阻塞调用（云 SDK / requests / paramiko / subprocess）**一律** `asyncio.to_thread`，包在 `@work(exclusive=True)` worker 里，`on_mount` 触发。
- 实时流：`@work(thread=True)` + `call_from_thread` 回 UI 线程；`on_unmount` 置停止位并 close response，防线程泄漏。
- 轮询：`set_interval` + busy 标志去重；`on_unmount` 停 timer。
- 展示刷新函数与列宽计算解耦：`_rebuild/_load`（数据 → 单元格 + fit）与 worker（取数）分开。

## 11.5 加载指示器与忙碌遮罩规范

- **设计原则**：对耗时 >300ms 的网络 / IO 操作必须有可见的加载状态，处理期间需阻断对底层（如旧数据行、提交按钮）的重复操作。
- **四种基础形态与适用场景**：
  1. **控件级 indeterminate 覆盖层（`ProgressCover`）**：
     - 场景：主要数据列表加载（Billing / Traffic / Domain / Record / Models / Routing 等）与列表型容器（如 upstream 模型列表、路由组成员列表）。
     - 机制：在 `App` 覆写 `get_loading_widget() -> ProgressCover`。取数时设 `widget.loading = True`，控件被覆盖且从命中测试中排除（**天然阻断对旧数据行的点击**）；完成后 `widget.loading = False` 并 reassert 焦点。
     - **关键约束**：`ProgressCover` 必须是**单个自渲染 widget**：`render()` 返回一条**固定 40 格、居中**的不定进度条 `Text`（`auto_refresh` 逐帧移动高亮，宽度与全屏 `BusyOverlay` 一致）。**绝不能是容器 + 子件**——Textual 的 `Widget._cover()` 只把 cover 交给合成器替换原控件区域，并不把它挂进 DOM 参与布局，容器的子件 `region` 恒为 0 而完全不可见（`ProgressBar` 自身 compose 出 `Bar` 子件，因此也不能直接用）。
     - 约束：定时轮询刷新（如 connections）仅在**首载 / 空表**时加覆盖，后续轮询不闪遮罩；`_rebuild` 完成后需显式重新 `focus()` 列表。
  2. **全屏 dots 忙碌遮罩（`BusyOverlay` mode="dots"）**：
     - 场景：表单提交、批量导入、模型参数构建 / 导出。
     - 机制：`async with busy(self.app, "正在保存…", mode="dots"): await asyncio.to_thread(...)`。`ModalScreen` 铺满，吞掉一切键盘鼠标输入。
  3. **全屏 indeterminate / percent 忙碌遮罩（`BusyOverlay` mode="bar"/"percent"）**：
     - 场景："bar" 适合阶段明显的远程拉取（如 clash 部署订阅、DNS 提交），可调 `ov.set_message("阶段…")` 换词；"percent" 适合模型总数固定、分批并行的拉取（如 lpc 一次性抓取）。
     - 机制：percent 模式开启时传入 `total`，每个模型完成时经 `self.call_from_thread(ov.advance)` 推进并自动刷新计数行。
     - 布局约束：`.busy-box` 必须**定宽**（`width: 46`）且标题 `text-wrap: nowrap` + `text-overflow: ellipsis; overflow: hidden`，内部固定为「标题 / 进度条 / 计数行」三行结构；否则文案一变容器就会因 auto 宽度重算而抖动，宽字符 Emoji（📡）或超长 URL 还会顶破边框或意外换行。
  4. **状态栏点阵 spinner**：
     - 场景：轻量、即时下发的单条请求（如 clash 调整开关、重载设置）。
     - 机制：调用 `self.app.spinner(True, "正在切换…")` 开启 status 栏 dots braille 轮转（`⠋⠙⠹…` @80ms），下发完成或 report 时自动复位。

## 12. Textual CSS / 8.2.8 通用陷阱

- 无 `:not()`、无 `z-index`、无 `top`/`left`（用 `offset` 样式属性）、无相邻兄弟选择器 `+`（解析直接报错，行距用显式类如 `.filter-row.gap-top`）。
- `max-height` 不接受 `none`，不限制就删声明。
- 8.2.8 无 `Spacer`（横向撑开用 `Static(classes="fill")` + `width: 1fr`）；`LoadingIndicator` 组件只能作为**普通挂载的子件**使用，**不可用作 `_cover` 覆盖层**（见 §11.5）；无 `.instant` 类。需要自渲染动画时，在 widget 的 `render()` 返回 `rich.spinner.Spinner` 并设 `auto_refresh`（如 1/12）。
- `cell_len` 在 `rich.cells`（复数模块名）。
- 自定义 `DataTable` 子类重写 `_on_click`：Textual 沿 MRO 逐类派发，接管与回落两条路径都要 `event.prevent_default()`，否则消息双发/双执行。
- 屏幕方法不要命名 `_render`（覆盖 `Widget._render` 渲染崩溃）。
- `on_mount` 回调里 `is_mounted` **仍是 `False`**（Textual 是派发完 Mount 事件**之后**才置位）：靠它守卫的写入会静默跳过（历史事故：`PageScreen.set_subtitle()` 写 `if self.is_mounted:` → 首屏顶栏副标题永远空白）。这类判定改用**控件是否已组合**（`try: query_one(...) except NoMatches: ...`）或 `call_after_refresh`，不要依赖 `is_mounted`。
- **反向陷阱同样存在**：`TabbedContent` 各 `TabPane` 的内容是**分阶段挂载**的，且卸载时**子件先于父件消失**——父件 `is_mounted` 仍为 `True` 时 `query_one(子件)` 会抛 `NoMatches`（历史事故：详情页 worker 起手 `query_one("#dt-sg-table")`、`HintBar.update()` 用 `if self.is_mounted` 守卫，在「进入即切页签 / App 正在退出」时崩）。规则：**任何跨层级的 `query_one` 都要 `try/except NoMatches`**，判定「查得到与否」而不是「挂没挂载」；表格类可选查询用 `screens/ecs.py::_find_table()` 这种返回 `None` 的包装。
- **级联优先级**：app.tcss（含 Screen CSS）对同一属性**始终压过** widget 自身 `DEFAULT_CSS`，与选择器特异度无关——给通用控件写全局规则前，先 grep 各控件 DEFAULT_CSS 里对该控件的 width/padding 定制（历史事故：全局 `Button{padding:0 1}` 掀翻 MonthPicker 3 格小按钮，rich `chop_cells` 除零崩溃）。
- `#topbar Button { margin }` 这类**后代选择器**会命中挂在顶栏里的组件内部按钮（MonthPicker 弹层等），且 id 前缀特异度压过 widget 类规则；顶栏间距规则必须限定到具体 id（`#topbar #top-back`）。
- `OptionList` 的 `DEFAULT_CSS` 自带 `border: tall $border-blurred`（直角框）。放进 `.panel` 圆角容器时必须显式 `border: none; background: transparent`（如 `#mh-list`），否则容器内套一个直角方框。
- 首页 Logo 每行**不要保留尾随空格**：`logo_text()` 对每行 `rstrip()`、`LOGO_WIDTH` 用 `cell_len(line.rstrip())`；否则 `text-align: center` 会按各行裁剪后的宽度独立居中，出现「某一行错位」。
- `textual.widgets` 各版本导出位置不同，`Option` 用 try/except import。
- `Select` 内部 `#label` 是 `width: 1fr`：放进 `Horizontal` 行且不给显式宽度时，auto 宽度会把整行剩余空间全吃掉，`▼` 箭头溢出容器被裁剪——行内 Select 必须显式定宽（如设置页模式 `width: 12`）。

## 13. 验证规范（四项目同一模板）

- `App.run_test(size=(120, 40))` 宽屏 + `(LOGO_WIDTH+6 以下, 28)` 窄屏各跑一遍。
- 配置目录指向 `tempfile.mkdtemp()`（写最小配置），服务层用 Fake 对象整体屏蔽网络。
- 必测清单：各页挂载无异常；`#page.virtual_size.height <= container_size.height`（单滚动条）；表格列宽和 ≈ `region.width - 2` 且无横向溢出；单击行只移光标、双击行执行且**不重复压栈**；`Ctrl+N/E/D/R`；模态紧凑度（min-height/单行输入/行距/Tab 轮切/validator 拦截）；折叠菜单浮层开合与外部点击；Esc 层级（模态 > 菜单 > 返回）；窄终端 logo `-sm` 降级与列宽重算。
- 断言 API 坑：DOMQuery 计数用 `len(query)`（无 `.count()`）；滚动上限 `max_scroll_y`（无 `scroll_max`）；Pilot 无 `shift_tab()`，用 `pilot.press("shift+tab")`；Fake 服务要**带状态**（setter 改 getter 返回值）。

## 14. 跨项目组件对照

| 组件 | aliyun-controller | clash-controller | litellm-controller | llm-price-compare |
|---|---|---|---|---|
| `ui.PageScreen` / `ui.LOGO_*` | ✅ | ✅ | ✅ | ✅（PageScreen 骨架；看板根屏无 LOGO） |
| `ui.MonthPicker` / `ui.QuotaBar` | ✅（账期/额度场景） | ➖ 无场景未搬运 | ➖ 无场景未搬运 | ➖ 无场景未搬运 |
| `widgets.ClickTable` / `HintBar` / `MenuMarquee` / `shorten` / `rcell` / `fit_table_columns` | ✅ | ✅ | ✅ | ✅（全套辅助；看板表自适应列宽） |
| `widgets` 四模态 | ✅（FormModal 无 textarea，另有 `browse` / `show_if` / 动态字段） | ✅（FormModal 多 `textarea` kind） | ✅（FormModal 多 `textarea` + `browse` 字段） | ✅（FormModal 无 textarea；设置页派生动态 pricing） |
| `PickModal` / `MultiPickModal` 选择弹窗 | ✅（`PickModal`；`MultiPickModal` 未移植） | ➖ | ✅（litellm 特有：项目选择 / 模糊多选） | ➖ |
| sheet 式大表单（ModalScreen） | ➖ | ➖（用 FormModal 足够） | ✅（ModelFormScreen / GroupFormScreen / ModelMetaFormScreen） | ➖（用 FormModal 足够） |
| 模糊筛选 `filter_fuzzy` | ✅ | ✅ | ✅ | ✅ |

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
  ecs.py       ECS 服务层（地域探测 / 实例生命周期 / 安全组规则 / 磁盘）
  ui.py        界面骨架：PageScreen / MonthPicker / QuotaBar / logo 常量（8.x 特性集中在这里）
  widgets.py   模态（ConfirmModal/InputModal/FormModal/OutputModal）+ 表格与模糊筛选辅助
  app.tcss     全局样式（模态三段式、顶栏、quota-bar、home banner、状态栏）
  screens/     home / setup / settings / billing / dns / ecs
```

分层与服务约定见第一部分 §2；本项目服务层失败统一抛 `AliyunApiError`（数据不合法抛 `ValueError`）。

## 本项目界面实例（对第一部分规范的映射）

- 首页：6 菜单项（流量/账单/DNS/云服务器/设置/退出），数字快捷键 1-6；版本号跟随 `ui.home_col_width()` 动态列宽右对齐 Logo 右缘，菜单描述右对齐、超宽走 `MenuMarquee`；`Esc`/`Ctrl+C` 退出程序（不绑 `q`）。
- 账单/流量页：上小下大——汇总 `.panel`（含 `ui.QuotaBar` 与刷新按钮）+ 明细 `.tbl`；账期切换在顶栏 `ui.MonthPicker`（Changed 消息自动重查）。
- 云服务器：地域页（`RegionListScreen` 并发探测有实例的地域，仅 1 个时 `switch_screen` 自动进入）→ 实例列表（搜索 + 首列「选」与 `空格` 标记 + 状态门控的 `[详情][标记][全选][清空][批量开/关/重启][开机][关机][重启]` + 折叠菜单放强制操作/切换地域/安全组管理）→ 实例详情（`TabbedContent` 四个页签，顺序固定为「基本信息 / 监控 / 网络与安全组 / 存储与备份」：基本信息 kv（名称 / 状态 / 规格 / 镜像 / 可用区 / 锁定原因 + `[开机][关机][重启][执行命令]` 按钮行） → 其下「监控」读数卡（CPU / 内存 / 磁盘 最新值 + 不可用原因）→ 网络 kv（IP / EIP 带宽 / VPC / 交换机 / 关联安全组 + `[调整带宽]`）→ 计费与时间；监控页（时间窗 + 三条占用条 + braille 折线图 + 光标读数）；网络与安全组表；存储与备份磁盘表）→ 安全组列表（新建 / 删除）→ 规则页（方向 Select + 搜索 + 增删改；编辑用 FormModal 动态字段）。关机统一先弹 `StopOptionsModal`（正常/强制 + 节省停机/保留实例），批量关机复用同一模态；批量操作全部到位才 `notify_ok`，否则 `OutputModal` 列出已到位 / 未到位 / 下发失败明细。
- DNS 域名页/记录页：`.panel` 小容器（搜索框 + 排序 Select + 增删改按钮）+ ClickTable（双击行进记录页/开编辑弹窗）；列 cap：主机记录 20、线路 14、记录值 40；TTL/状态右对齐 rcell。记录表单按类型动态渲染：线路（一级 Select + 「更多线路…」PickModal）、MX 优先级、权重（仅 A/AAAA/CNAME/ALIAS）、备注、TTL 档位；SRV/CAA/TLSA/SVCB/HTTPS/URL 使用结构化多栏并在提交时拼成单个 `Value`。
- 设置页：单 `.panel` 容器 kv 行（配置文件路径 / AK / Secret，均脱敏）→ 容器右下角 `[编辑访问密钥]` 打开 FormModal（ID 预填、Secret 密码框 placeholder 脱敏且留空=不变、Tab 轮切、保存/取消）；保存后 `app.reload_config()` 立即生效。
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
- MonthPicker 浮层常量 `POP_WIDTH = 26` 与 CSS `#mp-pop width` 必须一致；全局单行按钮生效后其网格按钮为 `width: 6`（含 padding/line-pad）、方向键 `width: 5`。
- 单元格点击行/列从 `event.style.meta["row"/"column"]` 取；ClickTable 用「`Coordinate == cursor_coordinate` 则不再二次处理」避免与原生双击/回车重复。
- `UpdateDomainRecord` 是**整条覆盖**语义：`Line` / `Priority` 不传会被重置为 `default` / 空。编辑时必须把 `line` 与 MX 的 `priority` 一起传回（`record_content_changed()` 也扩展了这两个比较维度）。
- 隐性 URL 的官方拼写存在 `FORWARD_URL` / `FORWORD_URL` 两种，读取已有记录时用 `normalize_record_type()` 归一化，避免编辑时类型丢失。
- 设置权重前本地要求「同主机记录 + 同线路存在 ≥2 条**可加权**记录（A/AAAA/CNAME/ALIAS）」——同名 TXT/MX 记录不计入，否则服务端会拒绝；权重与整组开关分两次调用（`SetDNSSLBStatus` 幂等 → `UpdateDNSSLBWeight`），故 `weight` 留空 = 不修改。
- 记录表单切换类型会真实增删行（`rebuild_fields`）：driver 之外的 Select（如线路）变化不触发重建；`_value_cache` 负责 A→SRV→A 往返时找回原记录值。
- ECS 没有「全地域实例总数」API：`discover_regions()` 先用 `DescribeRegions` 取地域，再用 8 线程并发 `DescribeInstances(max_results=10)` 读 `total_count`；结果只缓存在 `EcsQuerier._discovery`（进程内），`invalidate_discovery()` 清空。
- ECS 无 waiter：启停重启后必须自行 `DescribeInstanceStatus` 轮询（每页 ≤50、无 `next_token`）；超时返回最后状态，界面提示「已下发」而非成功。重启后先进 `Starting`，目标态是 `Running`。
- 安全组规则明细只能从 `DescribeSecurityGroupAttribute` 取（默认 500 条/页，必须显式 `max_results=1000` + 翻 `next_token`，且 `max_results` 最小 10）；`DescribeSecurityGroups` 只有规则计数。
- **`DescribeSecurityGroups` 不支持 `SecurityGroupIds`（复数、逗号分隔）**：传了会被服务端判为 `InvalidParameter (HTTP 400) The specified parameters are not valid.`，只能传单个 `SecurityGroupId`。需要按多个 ID 过滤时逐个查询再合并（`_page_security_groups(region, sg_id)`）。
- 修改规则是「按 rule_id 改单条」但未传字段可能被重置（Priority 默认 1）→ 必须读-改-写全量回填；不能改授权对象类型（CIDR ↔ 安全组、IPv4 ↔ IPv6），前缀列表规则只读。
- 按 `security_group_rule_id` 删除不存在的规则会报 `InvalidParam.SecurityGroupRuleId`，需提示「可能已被删除」并刷新；新增重复规则会静默成功但不新增，不能谎报。
- `DescribeInstancesRequest.instance_ids` 是**逗号分隔字符串**，`DescribeInstanceStatusRequest.instance_id` 才是 `List[str]`。
- 批量生命周期接口单次最多 100 台（`BATCH_LIMIT`，超出自动分批）；`StopInstancesRequest` 用 `force_stop` + `stopped_mode`，而 `RebootInstancesRequest` 用 **`force_reboot`**（单台 `RebootInstance` 才是 `force_stop`），`instance_id` 均为 `List[str]`。部分批次失败返回 `BatchResult(success, failed)`，全部失败才抛 `AliyunApiError`。
- 批量状态轮询 `wait_instances_status()` 复用 `DescribeInstanceStatus(instance_id=[...])`（≤100/次）；`wait_instance_status()` 是它的单台封装。
- `DescribeVpcsRequest` 只支持 `page_number/page_size`（无 `max_results/next_token`）；`CreateSecurityGroupRequest` 的 `vpc_id` 为空时传 `None`，`security_group_type` 不传即 normal。删除仍被引用的安全组会返回 `DependencyViolation`，服务层统一转成「安全组仍被实例或其他资源使用，请先解绑」。
- `RunCommandRequest` 的参数名是 **`type`**（不是 `command_type`），需配合 `command_content` / `content_encoding="PlainText"` / `timeout` / `name`；结果取 `DescribeInvocationResults` 的 `invocation.invocation_results.invocation_result[0]`，终态集合为 `Finished/Success/Failed/Stopped/Timeout`。
- `DescribeInstanceAutoRenewAttributeRequest.instance_id` 是**逗号分隔字符串**（不是 List），`page_number/page_size` 是**字符串**；查询失败不得阻塞实例详情（返回空 dict）。
- `DescribeEipAddresses` 用 `page_number/page_size` 翻页、`associated_instance_id` 过滤；`internet_charge_type=PayByTraffic` 时不提供改带宽入口，`ModifyInstanceNetworkSpec` 仅对 `PayByBandwidth` 且实例处于 Running/Stopped 时可用（会立即影响计费，必须二次确认）。没有 EIP 但有普通公网 IP 的实例同样可调（回落到 `InstanceInfo.internet_charge_type` / `bandwidth_out`）；本地上限按付费类型：包年包月 200 Mbps、按量付费 100 Mbps（服务端 0 也合法，界面限制 1 起）。`get_bandwidth_limit()` 用 `DescribeBandwidthLimitation` 查询规格上限，失败时回落到上述常量，不得因查询失败禁用入口。
- 到期提醒阈值 `EXPIRE_WARN_DAYS = 15`：列表列格式 `YYYY-MM-DD 剩N天`（窄列自动退化为 `剩N天`），≤15 天黄色、已过期红色；`PostPaid` 到期显示 `-`。
- **ECS 监控**：`DescribeInstanceMonitorDataRequest` **没有 `region_id` 参数**（地域靠 client endpoint），`start_time` / `end_time` 必须 ISO8601 带秒（`%Y-%m-%dT%H:%M:%SZ`，`%H:%MZ` 会 400 `InvalidStartTime.Malformed`），`period` 只接受 60/600/3600，`(end-start)/period ≤ 400` 点、只能查近 30 天、单台查询。当前分钟的点可能还是 0（未聚合完）→ `drop_incomplete_tail()` 必须丢弃。**ECS 接口没有内存、没有磁盘空间使用率**。
- **云监控 Cms** 用已装的 `alibabacloud-tea-openapi` 通用 `call_api`（无需新依赖）：`Params(action="DescribeMetricList", version="2019-01-01", style="RPC", ...)`，endpoint `metrics.{region}.aliyuncs.com`，query 含 `Namespace/MetricName/Dimensions(JSON 字符串)/Period/StartTime(ms)/EndTime(ms)/Length(≤1440)`。`Datapoints` 是 **JSON 字符串**；403 有两种形态：`DescribeMetricList` 抛 `ClientException`，`DescribeMetricLast` 返回 HTTP 200 + body `Success=false, Code=403`，两条都要判。`diskusage_utilization` 同一 timestamp 每个 device 一条，需本地按 ts 取 max；限流 50 次/秒，Metric List/Last/Data/Top 合计 100 万次/月，禁止秒级轮询（进程内缓存 60 秒）。
- **详情页「刷新」按钮挂顶栏**（`compose_toolbar()` 的 `#dt-refresh`），不放基本信息面板：60 列下 `[开机][关机][重启][执行命令]` 已占满面板宽，第 5 个按钮会越出圆角边框。
- **详情页自动刷新**：`AUTO_OVERVIEW_SECONDS = 60`（基本信息页读数卡，`window="1h"`）/ `AUTO_MONITOR_SECONDS = 15`（监控页折线）。`_tick_*` 统一经 `_can_auto_refresh(pane_id)` 门控——本屏必须 `app.screen is self` 且停在目标页签，否则让路；读数卡取数走 `_request_readout()` + `_ov_loading` 去重（挂载与切页签会各触发一次，不去重就会重复打接口）。
- **`get_series(..., max_age=0)` 绕过 60 秒缓存**：自动刷新必须传 0（否则拿到的是同一份旧快照），手动刷新 / 切页签走缓存（`invalidate()` 后再取）。
- **`TabPane.Focused` 会抢页签**：`TabbedContent` 收到「页签内控件获得焦点」就把 active 切回该页，`_load_monitor` 结束后无条件 `previous.focus()` 会在用户已切走页签时把页面拽回监控页。还原焦点前必须同时满足「焦点未被移走（`app.focused in (None, previous)`）」和「仍在监控页签」。测试里切页签前先 `query_one("#dt-tabs Tabs").focus()`，与真实操作一致。
- **内置 `Sparkline` 不能用**：它按数据自身 min/max 归一化（恒定 50% 与 0% 画出来一样）、`height>1` 是填充面积、塞进 DataTable 会被压成 2 列。折线图用自绘 braille（`ui.LineChart`，`StripRenderable` + Bresenham + 均值降采样）；braille 空单元必须写 `" "`，不能写 `U+2800`（部分字体会显示脏点）。
- `LineChart.CursorMoved` 的 `control` 返回消息里显式保存的 `chart`，**不能依赖 `Message._sender`**：`_sender` 是构造消息时的 active message pump（可能是 Screen），用它做 `is` 比较会把发消息的图自己也清掉。

## 依赖与质量

- `pyproject.toml`：`textual>=8.0`、`PyYAML`、`alibabacloud-*`。
- 依赖变更后同步更新 `requirements.txt`。

## 验证（本项目脚本）

- 校验脚本统一放在 `~/devchecks/aliyun-controller/`（`/tmp` 是 tmpfs，重启即清空，勿只留副本）；运行方式 `cd /webservices/aliyun-controller && ./venv/bin/python ~/devchecks/aliyun-controller/<script>.py`：`aliyun_ui_check2.py`（取数流程/键位/Esc 层级）、`aliyun_ui_check3.py`（月份浮层/ClickTable/列宽/QuotaBar/设置弹窗）、`dns_form_check.py`（类型动态字段/结构值拼装/线路 PickModal/保存编排/三层 Esc）、`dns_status_check.py`（保存差异分流）、`ecs_ui_check.py`（89 断言：地域探测/实例门控/详情页/规则增删改/Esc 层级/窄屏版面）、`ecs_p1_check.py`（约 101 断言：批量标记与下发参数/关机选项/安全组建删/云助手命令/续费到期/EIP 带宽/窄屏）、`ecs_scroll_check.py`（13 断言：详情页进入不自动滚底、页签键位与顺序、基本信息页滚动）、`ecs_service_check.py`（44 断言：SDK 请求构造/分页/探测/轮询/安全组按 ID 单个查询）、`ecs_review_check.py`（17 断言：端口归一/IPv4↔IPv6 拦截/切换地域栈/光标保持/返回刷新）、`ecs_monitor_check.py`（166 断言：监控纯函数/CMS+ECS 请求构造/403 两种形态降级/空数据/监控页签懒加载/窗口切换/Chart 像素与光标/读数卡与不可用原因/自动刷新门控与 `max_age`/未挂载守卫/带宽上限查询）、`ui_widgets_check.py`（27 断言：QuotaBar 三段比例、MonthPicker 挂载与卸载语义、HintBar 竞态、设置页 Secret 留空保持原值与脱敏显示），可作新脚本模板。
- **脚本取像坑**：容器内控件断言渲染内容要用 `widget.render_lines(Region(0, 0, w, h))`，传 `widget.region` 会把绝对偏移当原点、读回整片空白（曾据此误判「折线图没画出来」）；断言必须校验**像素本身**（braille 码点计数、刻度文本、光标列数增量），只断言行数等于控件高度等于没测。
- 做法：`ALIYUN_CONTROLLER_CONFIG_DIR` 指向临时目录并写 `config.yaml`；`app._billing = FakeBilling(); app._dns = FakeDns(); app._ecs = FakeEcs()` 屏蔽网络。

## 提交

- 不要执行 `git commit` 等写操作，由用户自行提交。
