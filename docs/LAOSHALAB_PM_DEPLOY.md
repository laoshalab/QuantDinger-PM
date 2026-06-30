# laoshalab/QuantDinger-PM 部署指南

本仓库是 [QuantDinger](https://github.com/brokermr810/QuantDinger) 的 **Polymarket 预测市场** fork，在 v4.0 后端上恢复了按需分析能力，并在仓库内 vendoring 了 `QuantDinger-Vue/` 前端源码。

仓库地址：<https://github.com/laoshalab/QuantDinger-PM>

## 与上游的差异

| 项目 | 说明 |
|------|------|
| 后端 | `POST /api/polymarket/analyze`、`GET /api/polymarket/history` |
| 计费 | 默认 15 积分/次（`BILLING_COST_POLYMARKET_DEEP_ANALYSIS`），**分析成功后才扣费** |
| 前端 | AI 资产分析 → **预测市场** Tab + 分析弹窗 |
| 前端镜像 | **不能**使用上游 `ghcr.io/brokermr810/quantdinger-frontend`（不含 PM UI），需本地构建 |

## 前置条件

- Docker + Compose v2
- 至少 4GB 可用内存
- 已配置 LLM（OpenRouter / OpenAI / AtlasCloud 等，见 `backend_api_python/env.example`）

## 快速部署（推荐）

```bash
git clone git@github.com:laoshalab/QuantDinger-PM.git
cd QuantDinger-PM

cp backend_api_python/env.example backend_api_python/.env
./scripts/generate-secret-key.sh

# 编辑 backend_api_python/.env：
#   ADMIN_USER=your_admin
#   ADMIN_PASSWORD=your_secure_password
#   LLM_PROVIDER=...
#   OPENROUTER_API_KEY=...   # 或其他 LLM key

# 必须带 build override，从仓库内 QuantDinger-Vue 构建前端
docker compose -f docker-compose.yml -f docker-compose.build.yml up -d --build
```

访问：

- Web UI：<http://localhost:8888>
- API 健康检查：<http://localhost:5000/api/health>

## 使用 Polymarket 功能

1. 登录 Web UI
2. 进入 **AI 资产分析**
3. 切换到 **预测市场** Tab
4. 点击 **开始分析**，粘贴 Polymarket 链接或市场标题
5. 若标题匹配多个市场，会从候选列表中选择
6. 分析成功后消耗积分（计费开启时），可在弹窗 **历史记录** Tab 查看

### API 示例

```bash
TOKEN="<your_jwt>"

curl -s -X POST http://localhost:5000/api/polymarket/analyze \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"input":"https://polymarket.com/event/your-market-slug","language":"zh-CN"}'

curl -s "http://localhost:5000/api/polymarket/history?page=1&page_size=20" \
  -H "Authorization: Bearer $TOKEN"
```

## 计费配置

在 `backend_api_python/.env` 或管理后台 **Settings → Billing**：

```env
BILLING_ENABLED=true
BILLING_COST_POLYMARKET_DEEP_ANALYSIS=15
```

- 分析**失败**（LLM 超时、市场未找到等）**不会扣费**
- 积分不足时返回 HTTP 400，`msg: Insufficient credits`

## 仅更新后端

```bash
docker compose -f docker-compose.yml -f docker-compose.build.yml up -d --build backend
```

## 仅更新前端

```bash
docker compose -f docker-compose.yml -f docker-compose.build.yml up -d --build frontend
```

## 开发模式（Vue 热更新）

```bash
cd QuantDinger-Vue
pnpm install
# backend 已在 5000 端口运行
pnpm run serve
```

开发服务器默认 `http://localhost:8000`，API 通过 Vite 代理到后端。

## 数据库

首次启动会自动执行 `backend_api_python/migrations/init.sql`，创建：

- `qd_polymarket_markets`
- `qd_polymarket_ai_analysis`
- `qd_polymarket_asset_opportunities`

从上游 v3.0.7（已删除 PM 表）升级时，重启 backend 即可自动建表，无需手工迁移。

## 故障排查

| 现象 | 处理 |
|------|------|
| 看不到「预测市场」Tab | 确认使用了 `docker-compose.build.yml` 构建前端，并 hard refresh（Ctrl+Shift+R） |
| `/api/polymarket/*` 404 | 确认 backend 为 laoshalab fork 最新版，检查 `docker compose logs backend` |
| 分析一直 loading | 检查 LLM 配置与 `docker compose logs backend`；前端超时 120s |
| 积分被扣但无结果 | 请升级到最新版（已改为成功后才扣费） |
| Polymarket API 失败 | 确认服务器可访问 `gamma-api.polymarket.com` |

## 合规说明

Polymarket 模块为 **只读 AI 研究**，不提供下单、不提供投资建议。UI 弹窗内已展示风险提示；运营方需自行遵守当地法规。

## 相关文档

- [Human API OpenAPI](./api/openapi.yaml) — `Polymarket` tag
- [CHANGELOG](./CHANGELOG.md) — V3.0.7 删除记录与历史设计
