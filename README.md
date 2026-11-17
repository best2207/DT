# 外贸风控情报监控平台

GitHub Actions 驱动的轻量版：定时采集公开政策/新闻源，按企业别名匹配，标记涉华风险，向飞书推送新告警，并生成 GitHub Pages 看板。

## GitHub 部署

1. 将本目录内容推送到一个 GitHub 仓库。
2. 仓库 Settings → Actions → General，启用 Actions。
3. Settings → Secrets and variables → Actions，添加 `FEISHU_WEBHOOK_URL`；如飞书机器人启用了签名，再添加 `FEISHU_SIGNING_SECRET`。
4. Actions 页面运行 `Trade risk monitor`，选择 `Run workflow` 做首次手动运行。
5. Settings → Pages → Build and deployment，选择 `Deploy from a branch`，分支选 `main`，目录选 `/ (root)`。
6. 在 `config/companies.json` 写入需要监控的企业名称和别名；在 `config/settings.json` 调整关键词、周期和告警规则。

GitHub Actions 默认每 30 分钟运行一次，也支持手动运行。首轮成功后会生成 `data/events.json` 和 `data/state.json`，并提交回仓库；GitHub Pages 会显示看板。

## 本地验证

Python 3.11+，安装依赖：

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python main.py --once --offline
```

`--offline` 使用内置样例事件验证匹配、涉华标记、持久化和静态看板数据生成，不访问外网。正常手动采集：

```bash
python main.py --once
```

## 配置

- `config/companies.json`：企业中文名、英文名、别名、行业、产品、HS 编码、出口市场。
- `config/sources.json`：数据源及采集开关。
- `config/settings.json`：关键词、告警阈值、采集窗口、去重与记录上限。
- `FEISHU_WEBHOOK_URL` 和 `FEISHU_SIGNING_SECRET`：只放 GitHub Secrets，不写入仓库。

首版包含 Federal Register JSON、GDELT DOC API、MOFCOM/CACS 公共列表页和 RSS/Atom 通用适配器。数据源访问状态会出现在 `data/state.json`；单个源失败不会中断其他源。

## GitHub Actions 权限

workflow 使用 `contents: write` 将运行状态和事件索引提交回当前分支。并发运行已串行化，避免状态文件冲突。若仓库规则不允许机器人推送，需允许 GitHub Actions 使用 `GITHUB_TOKEN` 写入。
