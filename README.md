# Clash Hub

Clash 订阅聚合管理工具 —— 多源合一、智能重命名、流量监控、模板库。

## 🎯 核心特性

- **多源订阅聚合**：支持将多个机场订阅（`http(s)` 链接）与自定义节点（分享链接或 Clash `proxies` YAML）合并，统一输出为一份出站配置。
- **智能化节点管理**：
  - 独立页面管理导入节点，支持批量导入、单节点测速、编辑和删除。
  - 智能重命名功能，为不同订阅添加自定义前缀，解决同名节点冲突。
  - 支持关键字排除（黑名单）及多协议过滤（白名单 + 黑名单，支持 ss, ssr, trojan, hysteria2 等）。
- **流量与状态监控**：
  - 自动解析 `subscription-userinfo` 头部，直观显示各订阅的已用流量、总流量和到期时间。
  - 后台定时刷新订阅与流量状态（默认每 6 小时）。
  - **自动保护机制**：当流量耗尽或订阅过期时，自动禁用该订阅。
- **高可用连通性检测**：
  - 先校验节点拉取与解析情况，提供精准的节点可用性检测。
  - **基础检测**：内置 `httpx`，对 `http` / `socks` 代理进行连通性测试。
  - **高级检测**：支持调用本地 Mihomo 核心（需配置路径）进行真实 URL 延迟测试（适用于 `ss` / `vmess` / `trojan` 等复杂协议）。
  - 支持批量检测，并对不可用的订阅自动实行禁用。
- **灵活的配置模板**：支持丰富的预设模板，亦可通过管理后台编写和实时预览自定义 YAML 模板。
- **安全与多客户端支持**：
  - 提供深/浅色主题的现代化 Web 响应式后台，密码鉴权保护。
  - 聚合链接通过动态 UUID 路由保护，随时支持一键「重置密钥」轮换 UUID 防止泄露。
  - 首页提供多种客户端快捷导入入口（Clash / Clash Meta / Shadowrocket / v2rayN / V2rayNG 等）。

---

## 🚀 快速部署

### 方式一：Docker Compose (推荐)

使用 Docker 部署是最简单且环境隔离的方式。当前项目使用 GitHub Actions 自动构建并推送镜像，无需在服务器本地构建，大幅提升部署速度。

1. **准备环境变量**
在部署目录下创建 `.env` 文件，配置管理后台密码：
```bash
echo "ADMIN_PASSWORD=你的强密码" > .env
```

2. **拉取并启动服务**
确保当前目录下有 `docker-compose.yml` 文件，执行以下命令：
```bash
docker compose pull
docker compose up -d
```

启动后，访问 `http://<你的IP>:8080` 进入管理后台，密码为你刚刚在 `.env` 中设置的值。

### 方式二：本地直接运行 (Windows / Linux / macOS)

**前置条件**：已安装 [Python 3.10+](https://www.python.org/downloads/)（Windows 安装时请勾选 *Add Python to PATH*）。

```bash
git clone <repo-url> && cd "Clash Hub"

# 1. 创建虚拟环境
python -m venv .venv

# 2. 激活虚拟环境
# Windows (PowerShell): .\.venv\Scripts\Activate.ps1
# Windows (Git Bash): source .venv/Scripts/activate
# Linux/macOS: source .venv/bin/activate

# 3. 安装依赖
pip install -r requirements.txt

# 4. 运行服务
cd app
uvicorn main:app --host 127.0.0.1 --port 8000
```

浏览器打开：`http://127.0.0.1:8000` 进入管理后台。

---

## 📖 使用流程

1. **登录后台**：访问 Web 后台并使用密码登录。
2. **添加订阅源**：
   - 在「订阅管理」中添加你的机场链接（仅支持 `http(s)` 链接），可配置节点前缀。
   - 在「节点导入」中批量粘贴零散的节点分享链接或 Clash 节点配置。
3. **选择模板与规则**：进入「配置页」，选择预设模板或创建自定义模板，按需设置关键字过滤和协议过滤。
4. **获取链接**：点击「订阅链接」复制专属于你的 UUID 聚合 URL。
5. **导入客户端**：将 URL 填入你的 Clash / Meta 等客户端中即可起飞。

---

## ⚙️ 环境变量与配置

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `ADMIN_PASSWORD` | 管理后台登录密码 | `admin888` |
| `TZ` | 系统时区设置 | `Asia/Shanghai` |
| `CLASH_HUB_MIHOMO` | Mihomo 核心可执行文件路径（用于高级节点连通性测速）。也可在后台「设置」页面中直接配置。 | *(未配置)* |

---

## 📂 目录结构

```text
├── .github/                 # GitHub Actions 自动构建工作流
├── app/
│   ├── main.py              # FastAPI 主路由
│   ├── database.py          # 数据库引擎与连接
│   ├── models.py            # SQLAlchemy 数据模型
│   ├── migrations.py        # 数据库迁移脚本
│   ├── auth.py              # JWT 鉴权模块
│   ├── aggregator.py        # 核心聚合引擎
│   ├── preset_templates.py  # 预设 YAML 模板库
│   ├── proxy_latency.py     # 节点测速与连通性检测
│   ├── proxy_uri.py         # 节点链接解析
│   ├── scheduler.py         # 定时任务 (流量刷新)
│   ├── static/              # 静态资源
│   │   ├── css/             # CSS 样式 (分模块)
│   │   └── js/              # JS 逻辑 (按页面拆分)
│   └── templates/           # 页面模板 (HTML/Jinja2)
│       ├── index.html       # SPA 页面入口
│       ├── base.html        # 基础 HTML 结构
│       └── partials/        # 页面各个 Tab 及局部组件拆分
├── data/                    # SQLite 数据库持久化目录 (包含应用数据)
├── .env.example             # 环境变量配置模板
├── Dockerfile               # Docker 构建配置
├── docker-compose.yml       # Docker 编排配置
└── requirements.txt         # Python 依赖清单
```

---

## 💻 技术栈

基于 **Python 3.10+** 构建，核心使用 **FastAPI** / **SQLAlchemy (async)** / **aiosqlite** / **httpx** / **APScheduler**。前端采用原生 HTML + JavaScript 并结合 **Tailwind CSS** 进行响应式样式构建。
