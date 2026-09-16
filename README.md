# 阿里云控制台工具 (aliyun-controller)

这是一个调用阿里云 Python SDK 的终端用户界面（TUI）工具，基于 [Textual](https://github.com/Textualize/textual) 构建，用于在终端中完成账单查询与 DNS 解析管理等常用操作。

## 功能特性

- **流量统计**：查询指定月份的公网流出流量（IPv4 / IPv6 / 合计），明细表格 + 20GB 免费额度双色进度条
- **账单归纳**：按产品归纳指定月份的消费金额，附消费汇总面板（总额 / 产品数 / 占比最高项）
- **DNS 管理**：管理域名解析记录，支持搜索、排序以及增删改、启用/禁用
- **现代化 TUI**：艺术字首页、统一键位、页面内折叠菜单、状态栏与非阻塞异步加载

## 后续计划

- 加入 **ECS 控制** 的相关内容
- 加入 **OOS 控制** 的相关内容

## 界面与快捷键

| 场景 | 快捷键 | 作用 |
|---|---|---|
| **全局** | `Tab` | 切换焦点 |
| | `Ctrl+Q` | 退出程序 |
| | `Home` / `End` / `PgUp` / `PgDn` | 页面滚动到顶 / 底 / 翻页（焦点在文本框时自动让位给文本框） |
| **主菜单** | `1` / `2` / `3` / `4` / `5` | 直达流量查询 / 账单归纳 / DNS 管理 / 设置 / 退出 |
| | `↑` / `↓` · `回车` | 移动光标 · 确认 |
| | `q` | 退出程序 |
| **功能页** | `Ctrl+R` | 刷新数据 |
| | `Esc` | 返回上一级（折叠菜单打开时优先关闭菜单） |
| | `↑` / `↓` · `回车` | 表格内移动光标 · 操作所选行 |
| | `/` | 聚焦搜索框（实时模糊过滤） |
| | `←` / `→` | 月份选择器切换账期（焦点在选择器上时） |
| **DNS 记录页** | `Ctrl+N` / `Ctrl+E` / `Ctrl+D` | 新增 / 编辑 / 删除（作用于当前行） |
| **弹窗** | `Esc` | 取消并关闭 |
| | `回车` | 提交表单或确认 |

> 鼠标同样全程可用：单击按钮 / 选项 / 入口即可触发，表格单击移动光标、再次点击（双击）进入操作，滚轮滚动光标所在的滚动区域。

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
4. **设置**：查看 / 修改阿里云访问密钥

你也可以使用 `--dir/-D` 参数指定配置文件所在的目录：

```bash
aliyunctl -D /path/to/your/config/dir
```

### 账单查询

- 进入后默认查询当前月份的账单
- 顶栏的 `◀ YYYY-MM ▶` 月份选择器可逐月切换，切换后自动重新查询（范围：2020-01 至当月）
- 流量页附带 20GB 免费额度进度条：额度内绿色，超出部分红色（量程随用量自适应）
- 查询在后台线程执行，界面不会阻塞；结果以汇总面板 + 明细表格展示

### DNS 管理

- 选择要管理的域名，进入解析记录列表
- 支持按主机记录 / 记录值模糊筛选
- 支持按创建时间、二级域名、首字母排序（正序 / 逆序）
- 回车可对所选记录执行编辑、启用 / 禁用、删除操作，并可新增记录

## 权限要求

为了正常使用所有功能，你的阿里云 RAM 用户记得开放以下权限：

- `AliyunBSSReadOnlyAccess`：用于账单查询
- `AliyunDNSFullAccess`：用于 DNS 管理

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
