# 阿里云控制台工具 (aliyun-controller)

这是一个调用阿里云 Python SDK 的终端用户界面（TUI）工具，基于 [Textual](https://github.com/Textualize/textual) 构建，用于在终端中完成账单查询、DNS 解析管理与云服务器运维等常用操作。

## 功能特性

- **流量统计**：查询指定月份的公网流出流量（IPv4 / IPv6 / 合计），明细表格 + 20GB 免费额度双色进度条
- **账单归纳**：按产品归纳指定月份的消费金额，附消费汇总面板（总额 / 产品数 / 占比最高项）
- **DNS 管理**：管理域名解析记录（含线路 / MX 优先级 / 权重 / 备注），支持搜索、排序以及增删改、启用/禁用
- **云服务器（ECS）**：自动探测有实例的地域，支持实例开关机 / 重启（含关机模式选择、批量标记操作）、基本信息与磁盘查看、安全组与规则增删改、云助手执行命令、EIP 带宽调整与到期 / 续费提醒、实例监控（CPU / 内存 / 系统盘折线图与光标取值）
- **现代化 TUI**：艺术字首页、统一键位、页面内折叠菜单、状态栏与非阻塞异步加载

## 后续计划

- 加入 **OOS 控制** 的相关内容

## 界面与快捷键

| 场景 | 快捷键 | 作用 |
|---|---|---|
| **全局** | `Tab` | 切换焦点 |
| | `Ctrl+Q` | 任意界面直接退出程序 |
| | `Home` / `End` / `PgUp` / `PgDn` | 页面滚动到顶 / 底 / 翻页（焦点在文本框时自动让位给文本框） |
| **主菜单** | `1` / `2` / `3` / `4` / `5` / `6` | 直达流量查询 / 账单归纳 / DNS 管理 / 云服务器 ECS / 设置 / 退出 |
| | `↑` / `↓` · `回车` | 移动光标 · 确认 |
| | `Esc` / `Ctrl+C` | 退出程序 |
| **功能页** | `Ctrl+R` | 刷新数据 |
| | `Esc` / `Ctrl+C` | 返回上一级（折叠菜单打开时优先关闭菜单） |
| | `↑` / `↓` · `回车` | 表格内移动光标 · 操作所选行 |
| | `/` | 聚焦搜索框（实时模糊过滤） |
| | `←` / `→` | 月份选择器切换账期（焦点在选择器上时） |
| **DNS 记录页** | `Ctrl+N` / `Ctrl+E` / `Ctrl+D` | 新增 / 编辑 / 删除（作用于当前行） |
| **ECS 实例列表** | `空格` | 标记 / 取消标记当前实例（批量操作依据） |
| | `/` · `双击/回车` | 搜索 · 打开实例详情 |
| **ECS 实例详情** | `基本信息 [执行命令]` · 网络 `[调整带宽]` · 顶栏 `[刷新]` | 打开云助手命令 / 带宽调整 / 重取当前页签数据（按钮入口） |
| | `←` / `→` | 切换「基本信息 / 监控 / 网络与安全组 / 存储与备份」页签 |
| | `Tab` → `←` / `→` | 监控页聚焦图表后移动光标取值；`Home` / `End` 到首末点 |
| | `Esc` | 监控页先清除光标，再按一次返回上一级 |
| **弹窗** | `Esc` / `Ctrl+C` | 取消并关闭 |
| | `回车` | 提交表单或确认 |

## 安装指南

### 通过 pip 安装（推荐）

```bash
pip install aliyun-controller
```

安装后，可以直接使用 `aliyunctl` 命令运行程序：

```bash
aliyunctl
```

> 需要 Python 3.10 及以上版本。

### 从源码运行（开发模式）

1. 克隆此项目到本地：
   ```bash
   git clone <项目地址>
   cd aliyun-controller
   ```

2. 创建虚拟环境（推荐）：
   ```bash
   python -m venv venv
   source venv/bin/activate  # Linux/Mac
   # 或在 Windows 上: venv\Scripts\activate
   ```

3. 安装依赖：
   ```bash
   pip install -e .
   ```

4. 创建阿里云 RAM 用户并授权：
   - 登录阿里云控制台。
   - 进入 RAM 访问控制。
   - 在左侧导航栏选择 用户 > 创建用户。
   - 设置登录名称和显示名称，勾选 为该用户自动生成 AccessKey。
   - 创建成功后，请务必保存好 AccessKey ID 和 AccessKey Secret，它们只显示一次。
   - 为新创建的 RAM 用户授权：
     - 在用户详情页，点击 添加权限。
     - 选择 AliyunBSSReadOnlyAccess 和 AliyunDNSFullAccess 权限。
     - 点击 确定 完成授权。

5. 配置阿里云访问密钥：
   默认情况下，程序会在 `~/.config/aliyun-controller` 目录下查找配置文件。
   首次运行或配置文件不存在时，程序会自动进入 Textual 配置向导，依次填写：
   - `access_key_id`
   - `access_key_secret`

   填写完成后会自动生成 `config.yaml`。之后也可从主菜单的「设置」中随时修改访问密钥。

   你也可以使用 `--dir/-D` 参数指定配置文件所在的目录：
   ```bash
   aliyunctl -D /path/to/your/config/dir
   ```

## 使用方法

安装后，可以直接使用 `aliyunctl` 命令运行程序：

```bash
aliyunctl
```

程序将显示 Textual 交互界面，你可以选择以下功能：

1. **流量查询**：查看指定月份的公网总流出流量
2. **账单归纳**：查看指定月份按产品汇总的账单明细
3. **DNS 解析管理**：管理域名解析记录
4. **云服务器**：管理 ECS 实例与安全组规则
5. **设置**：查看 / 修改阿里云访问密钥

你也可以使用 `--dir/-D` 参数指定配置文件所在的目录：

```bash
aliyunctl -D /path/to/your/config/dir
```

### 账单查询

- 进入后默认查询当前月份的账单
- 顶栏的 `◀ YYYY-MM ▶` 月份选择器可逐月切换（点击月份还会弹出年份 + 12 月的快捷选择浮层），切换后自动重新查询（范围：2020-01 至当月）
- 流量页附带 20GB 免费额度进度条：额度内绿色，超出部分红色（量程随用量自适应）
- 查询在后台线程执行，界面不会阻塞；结果以汇总面板 + 明细表格展示

### DNS 管理

- 选择要管理的域名，进入解析记录列表（新增「线路」列，显示默认 / 电信 / 联通等线路）
- 支持按主机记录 / 记录值 / 线路模糊筛选
- 支持按创建时间、二级域名、首字母排序（正序 / 逆序）
- 回车可对所选记录执行编辑、启用 / 禁用、删除操作，并可新增记录
- 支持的记录类型：`A`、`AAAA`、`CNAME`、`MX`、`TXT`、`NS`、`SRV`、`CAA`、`ALIAS`、`SVCB`、`HTTPS`、`TLSA`、显性 URL、隐性 URL
- 新增 / 编辑表单支持：解析线路（一级线路下拉 + 「更多线路…」可搜索全量线路）、MX 优先级、权重（0–100）、备注（≤50 字符）、TTL（1–86400 秒）
- 复合记录值使用结构化输入：SRV 4 栏、CAA 3 栏、TLSA 4 栏、SVCB / HTTPS 3 栏、显性 URL 2 栏；提交时自动拼成 API 需要的 `Value`
- 限制：
  - 权重仅 `A / AAAA / CNAME / ALIAS`，且同一主机记录 + 同一线路下至少 2 条**可加权**记录（同名的 TXT/MX 等不计入）；`0` 表示不参与返回（服务端可能拒绝）
  - URL 转发（显性 / 隐性）仅支持默认线路，主机记录不支持泛解析与下划线
  - `ALIAS` 仅企业旗舰版 / 尊享版可用，且与 DNSSEC 互斥
  - TTL 最小 1 秒，但免费版 / 个人版实际最小 600 秒
- 控制台有但 OpenAPI 做不到：单条记录内「记录值集合」的逐行启停；记录锁定 / 解锁（`Locked` 只读）

### 云服务器（ECS）

- 进入后自动并发探测各地域是否有实例：仅一个地域有实例时直接进入，多个地域时先选择地域（同 DNS 先选域名）
- 实例列表支持按名称 / ID / IP 模糊筛选，并按状态自动门控「开机 / 关机 / 重启」：中间态（创建中 / 启动中 / 停止中）不可操作
- 可按 `空格` 标记多台实例后批量开机 / 关机 / 重启（单次请求自动按 100 台分批）；关机前可选「正常 / 强制」与「节省停机 / 保留实例」
- 关机、强制关机、强制重启均有二次确认；操作后轮询到目标状态再刷新，超时会如实提示而不是谎报成功（批量操作会列出已到位 / 未到位 / 下发失败明细）
- 到期时间列显示剩余天数，15 天内高亮、已过期标红；副标题与状态栏会汇总临期实例数量
- 详情页分「基本信息 / 监控 / 网络与安全组 / 存储与备份」四个页签：基本信息含名称、状态、规格、镜像、可用区、锁定原因与开关机按钮，紧随其后是「监控」读数卡（CPU / 内存 / 磁盘 最新值）；网络卡片含私网 / 公网 IP、EIP 带宽、VPC、交换机与关联安全组；存储与备份为只读磁盘表
- 监控页提供 CPU / 内存 / 系统盘三条占用条与时间序列折线图，可切换 1 小时 / 6 小时 / 24 小时 / 7 天窗口；`Tab` 聚焦图表后用 `←` / `→` 移动光标读取该时刻数值，`Esc` 清除光标
- 自动刷新：基本信息页读数卡每 60 秒、监控页折线每 15 秒刷新一次，仅当对应页签可见且本页在最前时触发（弹窗或已切页签时自动让路）；「刷新」按钮在顶栏，`Ctrl+R` 等价
- 监控数据来源：CPU 优先取云监控（`CPUUtilization`），无权限时自动降级到 ECS 自带监控；**内存 / 系统盘使用率只能来自云监控**，需要 ① 实例已安装云监控插件 ② AK 具备云监控只读权限。任一条件不满足时页面会显示明确原因，不会用 0% 冒充数据
- 详情页可执行云助手 Shell 命令（显示状态 / 退出码 / 输出，输出过长会截断），也可在按固定带宽计费且实例处于稳定态时调整公网出网带宽（二次确认，会立即影响计费；上限通过 `DescribeBandwidthLimitation` 按实例规格查询，查询失败时回落到包年包月 200 / 按量付费 100 Mbps）
- 安全组管理入口在实例列表的折叠菜单中；列表页支持新建 / 删除安全组，规则页支持入 / 出方向筛选、搜索以及新增 / 编辑 / 删除
- 删除仍被实例引用的安全组会被服务端拒绝，界面会转换为「请先解绑」的中文提示
- 编辑规则按「读-改-写」回填全部原值，不会把未修改的优先级、策略等字段重置为默认值
- 限制：前缀列表规则只读；不能就地修改授权对象类型（CIDR ↔ 安全组、IPv4 ↔ IPv6），需新增规则后删除旧规则

## 权限要求

为了正常使用所有功能，你的阿里云 RAM 用户记得开放以下权限：

- `AliyunBSSReadOnlyAccess`：用于账单查询
- `AliyunDNSFullAccess`：用于 DNS 管理
- `AliyunECSFullAccess`：用于云服务器查询、开关机与安全组管理（只读场景可用 `AliyunECSReadOnlyAccess`，但无法执行启停与规则修改）
- `AliyunCloudMonitorReadOnlyAccess`：用于实例监控的内存 / 系统盘指标（若使用自定义策略，至少需要 `cms:QueryMetricList`、`cms:QueryMetricLast`、`cms:DescribeMonitoringAgentStatuses`；注意 Action 是 `Query*` 而不是 `Describe*`）
- 若使用自定义权限策略，至少需要：`ecs:Describe*`、`ecs:StartInstance(s)`、`ecs:StopInstance(s)`、`ecs:RebootInstance(s)`、`ecs:CreateSecurityGroup`、`ecs:DeleteSecurityGroup`、`ecs:AuthorizeSecurityGroup*`、`ecs:RevokeSecurityGroup*`、`ecs:ModifySecurityGroup*`、`ecs:RunCommand`、`ecs:DescribeInvocationResults`、`ecs:ModifyInstanceNetworkSpec`、`ecs:DescribeEipAddresses`、`ecs:DescribeVpcs`、`ecs:DescribeInstanceAutoRenewAttribute`、`ecs:DescribeInstanceMonitorData`、`ecs:DescribeBandwidthLimitation`、`cms:QueryMetricList`、`cms:QueryMetricLast`、`cms:DescribeMonitoringAgentStatuses`

## 开发

本项目遵循 Python 3.10+ 规范。

```bash
git clone https://github.com/Moha-Master/Aliyun-Controller.git
cd Aliyun-Controller
python -m venv venv
source venv/bin/activate
pip install -e ".[dev]"
```

## 许可证

MIT
