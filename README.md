# hyperai-keepalive

给 HyperAI 免费档实例（`free-cpu`，2 vCPU / 4 GB）做**保活 + 节点自愈**。

## 为什么要重启而不是"保活"

HyperAI 免费档的硬性规则是**单次最长连续运行 2 小时**（官方 pricing 页：免费档 `2 vCPU · 4 GB RAM`，Pro 才把 "maximum continuous Free CPU runtime" 从 2 小时提到 8 小时）。

所以：

- 跑满 2 小时必停，**跟你 SSH 不 SSH、有没有流量完全无关**
- 实例里跑着的 nezha agent / cloudflared / sing-box 再活跃也拦不住
- 唯一的"保活" = 死了或快到点就**重新拉起来**，然后重装节点

## 它每小时做什么

1. GraphQL 查实例 `status` + `startedAt`
2. 判定：
   - `status != RUNNING` → 重启
   - 运行 ≥ `RESTART_AFTER_MIN`（默认 100 分钟，留 20 分钟余量）→ 主动重启，免得被平台砍
   - 否则 → 健康，直接退出（只花 1 分钟额度）
3. 重启后等 `RUNNING` → SSH 进去 → 检查 sing-box(8001) + nezha 进程，缺了就重跑 `sb.sh`
4. 拉 `/root/.tmp/sub.txt` → 解出节点链接 → 写回本仓库 `current.txt`
5. 链接变了（说明发生了重启）→ 可选推 Telegram

## 装法

在仓库 **Settings → Secrets and variables → Actions** 里加：

| Secret | 必填 | 说明 |
|---|---|---|
| `HYPERAI_TOKEN` | ✅ | hyperai 登录态 JWT（`/root/hyperai/state.json` 的 `token` 字段），约 **30 天**有效 |
| `TG_BOT_TOKEN` | 可选 | Telegram bot token，加了就把新链接推给你 |
| `TG_CHAT_ID` | 可选 | 目标 chat id |

加完 secret，去 **Actions → hyperai-keepalive → Run workflow** 手动跑一次验证。

## 拿节点链接

- 仓库里的 **`current.txt`** 永远是最新的那条 `vmess://`（每次重启由 bot 自动提交）
- 配了 TG secret 的话，重启后会自动把新链接推给你

## 坑

- **JWT 30 天过期**（约 2026-11-05）。过期后 workflow 会报 `FATAL: 查询失败(多半是 JWT 过期...)` 变红，重新登录 hyperai 拿新 token 换掉 secret 即可。
- **argo 域名每次重启都变**（quick tunnel 的临时域名），所以链接会换。想要固定域名得换 CF named tunnel。
- **GitHub Actions 额度**：本仓是 public，公开仓的 Actions **不限额**，所以每小时一跑随便造。想把检查搞得更密（比如每 20 分钟）直接改 cron 即可。
- **本仓是公开的**：`current.txt` 里的 `vmess://` 链接、以及 `keepalive.py` 里的 `NEZHA_KEY` 都是公开可见的 —— 等于半个公开代理节点，谁刷到这个仓都能用。不想这样就把链接改成只推 Telegram、仓库里不留（加 `TG_BOT_TOKEN`/`TG_CHAT_ID`，再删掉写 `current.txt` 那几行）。
- sb.sh 来自第三方（`main.ssss.nyc.mn`），装的东西以你的判断为准。

## 想调

- `keepalive.py` 顶部 `RESTART_AFTER_MIN`：主动重启阈值（分钟）
- 顶部 `USER_ID` / `JOB_ID`：换实例时改这里（实例 ID 在控制台 URL 里）
- 顶部 `NEZHA_*`：哪吒面板参数
