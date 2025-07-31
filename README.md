# 阿里云控制台工具

这是一个调用阿里云 Python SDK 在命令行中执行常用操作的简单工具，目前具有账单查询和 DNS 管理功能。

## 功能特性

- **账单查询**：查询指定月份的阿里云账单总额和明细
- **流量统计**：查询指定月份的公网流出流量总量
- **DNS 管理**：管理域名解析记录，包括增删改查操作

## 后续计划

- 加入**ECS控制**的相关内容
- 加入**OOS控制**的相关内容

## 安装指南

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
   pip install -r requirements.txt
   ```

4. 创建阿里云 RAM 用户并授权：
  - 登录阿里云控制台。
  - 进入 RAM 访问控制。
  - 在左侧导航栏选择 用户 > 创建用户。
  - 设置登录名称和显示名称，勾选 为该用户自动生成AccessKey。
  - 创建成功后，请务必保存好 AccessKey ID 和 AccessKey Secret，它们只显示一次。
  - 为新创建的 RAM 用户授权：
    - 在用户详情页，点击 添加权限。
    - 选择 AliyunBSSReadOnlyAccess 和 AliyunDNSFullAccess 权限。
    - 点击 确定 完成授权。

5. 配置阿里云访问密钥：
   ```bash
   cp .env.example .env
   ```
   然后编辑 `.env` 文件，填入你创建的的阿里云RAM用户的 AccessKey ID 和 AccessKey Secret：
   ```
   ALIBABA_CLOUD_ACCESS_KEY_ID=your_access_key_id
   ALIBABA_CLOUD_ACCESS_KEY_SECRET=your_access_key_secret
   ```

## 使用方法

运行主程序：
```bash
python main.py
```

程序将显示交互式菜单，你可以选择以下功能：

1. **查询总流出流量**：查看指定月份的公网总流出流量
2. **归纳账单**：查看指定月份的账单明细和总额
3. **DNS解析管理**：管理域名解析记录

### 账单查询

- 程序会默认查询当前月份的账单
- 你也可以输入其他月份（格式：YYYY-MM / YYYY-M）进行查询
- 支持分页查询和重新查询

### DNS 管理

- 选择要管理的域名
- 查看所有解析记录
- 添加、编辑或删除解析记录
- 支持按不同方式排序记录（创建时间、二级域名、首字母）

## 权限要求

为了正常使用所有功能，你的阿里云 RAM 用户记得开放以下权限：

- `AliyunBSSReadOnlyAccess`：用于账单查询
- `AliyunDNSFullAccess`：用于 DNS 管理

## 日志记录

程序会在运行目录下生成 `app.log` 文件，记录操作日志和错误信息。
