# Clash Hub

Clash 订阅聚合管理工具 —— 多源合一、智能重命名、流量监控、模板库。

## 功能

- **多源聚合**: 合并多个 **机场订阅**（`http(s)` 链接拉取）与 **节点导入**（分享链接或 Clash `proxies` YAML），统一进一份出站配置
- **订阅管理**: 仅维护机场订阅 URL；支持前缀、刷新、流量与到期展示；记录 **添加时间 / 更新时间**
- **节点导入**: 独立页面，**批量导入**会创建「批次」并在树下展示多个节点；支持单节点测速、编辑、删除；批次与节点均有 **添加 / 更新时间**
- **旧数据迁移**: 首次启动若数据库中仍有「非 http(s)」的旧版订阅行，会自动迁入「节点导入」并删除原行（一次性）
- **批量操作**: 订阅列表支持复选框与全选，可批量启用/禁用/删除；「批量检测」仅对勾选的订阅执行检测（不可用且原为启用时会自动禁用）
- **智能重命名**: 为不同订阅添加自定义前缀, 避免节点名冲突
- **流量监控**: 自动解析 `subscription-userinfo` 头, 聚合显示用量/总量/到期
- **自动禁用**: 流量耗尽或订阅过期时自动禁用
- **协议过滤**: 白名单 + 黑名单双向过滤 (ss, ssr, trojan, hysteria2...)
- **关键词排除**: 过滤掉名称含指定关键词的节点
- **配置页**: 模板（预设 + 自定义）与过滤设置同页展示，右侧实时 YAML / 可视化预览
- **首页**: 流量概览 + 多客户端订阅导入（Clash / Clash Meta / Shadowrocket / v2rayN / V2rayNG）与「重置密钥」
- **定时刷新**: 每 6 小时自动更新订阅流量数据
- **订阅可用性检测**: 先校验拉取与解析；**仅当解析结果恰好 1 个节点** 时做延迟类探测：**`http` / `socks5` / `socks`** 使用内置 **httpx** 经代理访问测试 URL（默认 `https://www.gstatic.com/generate_204`）；**`ss` / `ssr` / `vmess` / `vless` / `trojan` / `hysteria2` / `hysteria`** 需在后台「设置」填写 **Mihomo** 可执行文件路径（或 `PATH` 中有 `mihomo` / 环境变量 `CLASH_HUB_MIHOMO`），由 Mihomo 临时进程做与 Clash 一致的 **URL 延迟**；若未配置或失败则退回 **TCP 建连**（仅表示端口可连）。多节点订阅只做拉取/解析。单条检测仅提示；批量检测会对不可用的已启用订阅自动禁用
- **管理后台**: 深/浅主题 Web UI, 密码鉴权, 订阅 CRUD
- **UUID 链接**: 聚合订阅通过 UUID 路径对外提供，可在后台轮换 UUID

## 快速部署

### Docker Compose (推荐)

```bash
git clone <repo-url> && cd "Clash Hub"

# 修改默认密码 (可选)
# 编辑 docker-compose.yml 中的 ADMIN_PASSWORD

docker compose up -d
```

访问 `http://<你的IP>:8080` 进入管理后台, 默认密码: `admin888`

### 本地开发

```bash
cd app
pip install -r ../requirements.txt
uvicorn main:app --reload --port 8000
```

### Windows 本机运行 / 自测

**前置条件**：已安装 [Python 3.10+](https://www.python.org/downloads/)（安装时勾选 *Add Python to PATH*）。

**方式 A：直接跑 Python（适合改代码调试）**

*路径请改成你本机仓库目录。*

**A1 — PowerShell**

```powershell
cd "D:\zhou\Documents\git\Clash Hub"

python -m venv .venv
.\.venv\Scripts\Activate.ps1

pip install -r requirements.txt
$env:ADMIN_PASSWORD = "你的密码"   # 可选，默认 admin888

cd app
uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

> 若无法执行脚本：`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`。

**A2 — Git Bash**

```bash
cd "/d/zhou/Documents/git/Clash Hub"
# 若盘符不同，把 /d/ 换成 /c/ 等；路径含空格时请保持引号

python -m venv .venv
source .venv/Scripts/activate

pip install -r requirements.txt
export ADMIN_PASSWORD="你的密码"   # 可选，默认 admin888

cd app
uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

> Windows 下可在 **Git Bash**、PowerShell 或 CMD 中执行；请确保当前目录是本项目下的 `app`（与 `main.py` 同级），避免多份拷贝时改到一份、跑到另一份。

浏览器打开：**<http://127.0.0.1:8000>** 。管理后台里列表上的「添加/更新」等时间统一按 **东八区（Asia/Shanghai）** 显示。数据库（SQLite）里存的是 UTC 时刻；接口 JSON 中带 `+00:00` 偏移，避免浏览器把无时区 ISO 误解析成本地时间。  
聚合订阅地址形如：`http://127.0.0.1:8000/sub/<UUID>`（首页展示完整链接；泄露后可「重置密钥」轮换 UUID）。

管理员 API 补充：`POST /api/settings/reset-uuid` 轮换订阅 UUID；`GET /api/preview` 返回当前聚合 YAML 与节点/组统计（需登录）。节点导入相关：`GET/POST /api/import-batches`、`POST /api/import-batches/import`、`PUT /api/import-batches/{id}`（`name` 改名；`set_all_nodes_enabled` 批量启用/禁用该批次下全部节点）、`POST /api/import-batches/{id}/set-all-nodes-enabled`（与上一项等价，可选）、`DELETE /api/import-batches/{id}`、`PUT/DELETE /api/imported-nodes/{id}`、`POST /api/imported-nodes/{id}/check`（测速：对解析出的**第一个** proxy 做延迟探测，与机场订阅多节点行为不同）。

**方式 B：Docker Desktop（与服务器一致）**

安装 [Docker Desktop for Windows](https://www.docker.com/products/docker-desktop/) 并启动后，在项目根目录（PowerShell / CMD / Git Bash 均可）：

```bash
cd "/d/zhou/Documents/git/Clash Hub"
docker compose up --build
```

浏览器打开：**<http://127.0.0.1:8080**（`docker-compose.yml`> 里映射的是 `8080:8000`）。

**简单自检**

- 能打开登录页、用密码登录即服务正常。
- 添加一条机场订阅或导入节点后，在浏览器或 Clash 里访问聚合 URL，应返回 YAML 文本。

> 当前仓库未配置 `pytest` 等自动化测试；若需要可后续补充测试用例与 `pytest` 依赖。

## 使用流程

1. 登录管理后台
2. 在「订阅管理」添加机场订阅（仅 `http(s)` 链接、可选前缀）；在「节点导入」批量粘贴分享链接或 Clash proxies（按批次管理）
3. 选择预设模板，或创建多条命名自定义模板后点「选用」
4. 配置过滤规则 (可选)
5. 点击「订阅链接」复制聚合后的 URL
6. 在 Clash 客户端中导入该 URL

## 目录结构

```
├── app/
│   ├── main.py              # FastAPI 主路由
│   ├── database.py           # 数据库引擎
│   ├── models.py             # SQLAlchemy 模型
│   ├── migrations.py         # 启动时 schema/数据迁移
│   ├── auth.py               # JWT 鉴权
│   ├── aggregator.py         # 核心聚合引擎
│   ├── preset_templates.py   # 预设模板库
│   ├── scheduler.py          # 定时任务
│   └── templates/
│       └── index.html        # 管理后台 SPA
├── data/                     # SQLite 数据持久化
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

## 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `ADMIN_PASSWORD` | 管理后台密码 | `admin888` |
| `TZ` | 时区 | `Asia/Shanghai` |

## 技术栈

Python 3.10+ / FastAPI / SQLAlchemy (async) / aiosqlite / httpx / APScheduler / Tailwind CSS
