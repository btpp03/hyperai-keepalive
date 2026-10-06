# hyperai-keepalive

给 HyperAI 免费档实例（`free-cpu`，2 vCPU / 4 GB）做**保活 + 节点自愈**。

> 仓库里**不含**任何节点链接 —— 新链接只在恢复时直接推到你 Telegram。

## 为什么要重启而不是"保活"

HyperAI 免费档的硬性规则是**单次最长连续运行 2 小时**（官方 pricing 页：免费档 `2 vCPU · 4 GB RAM`，Pro 才把 "maximum continuous Free CPU runtime" 从 2 小时提到 8 小时）。

- 跑满 2 小时必停，**跟你 SSH 不 SSH、有没有流量完全无关**
- 实例里跑着的 nezha agent / cloudflared / sing-box 再活跃也拦不住
- 唯一的"保活" = 死了或快到点就**重新拉起来**，然后重装节点

## 它每小时做什么

1. GraphQL 查实例 `status` + `startedAt`
2. 判定：
   - `status != RUNNING` → 重启
   - 运行 ≥ `RESTART_AFTER_MIN`（默认 100 分钟，留 20 分钟余量）→ 主动重启
   - 否则 → 健康，直接退出（几乎不耗时）
3. 重启后等 `RUNNING` → SSH 进去 → 检查 sing-box(8001) + nezha 进程，缺了就重跑 `sb.sh`
4. 拉 `/root/.tmp/sub.txt` → 解出节点链接 → **推 Telegram**（仓库里不留）

因为重启总发生在整点检查时，下次被砍也正好落在 2 小时后的那次检查上 —— 每次只断几分钟。

## 装法

在仓库 **Settings → Secrets and variables → Actions** 里加：

| Secret | 必填 | 说明 |
|---|---|---|
| `HYPERAI_TOKEN` | ✅ | hyperai 登录态 JWT（`/root/hyperai/state.json` 的 `token` 字段），约 **30 天**有效 |
| `TG_BOT_TOKEN` | ✅ | Telegram bot token，用来把新链接推给你 |
| `TG_CHAT_ID` | ✅ | 目标 chat id |

配好去 **Actions → hyperai-keepalive → Run workflow**（可勾 `force_fetch` 立刻拉一条链接验证）。

## 拿节点链接

恢复完成后（重启 / 手动强制拉取）**直接推到你 Telegram**。仓库、日志里都不会留链接。

> 手动强制拉取时也会推一条，方便验证通道是否正常。

## 坑

- **JWT 30 天过期**（约 2026-11-05）。过期后 workflow 会报 `FATAL: 查询失败(多半是 JWT 过期...)` 变红，重新登录 hyperai 拿新 token 换掉 secret 即可。
- **argo 域名每次重启都变**（quick tunnel 的临时域名），所以链接会换 —— 这正是必须自动推送的原因。
- **GitHub Actions 额度**：本仓是 public，公开仓的 Actions **不限额**，跟私库那 2000 分钟/月各算各的。
- `keepalive.py` 里的 `NEZHA_KEY` 是公开可见的（仓库公开），知道就行。
- sb.sh 来自第三方（`main.ssss.nyc.mn`），装的东西以你的判断为准。

## 想调

- `keepalive.py` 顶部 `RESTART_AFTER_MIN`：主动重启阈值（分钟）
- 顶部 `USER_ID` / `JOB_ID`：换实例时改这里（实例 ID 在控制台 URL 里）
- 顶部 `NEZHA_*`：哪吒面板参数
- `.github/workflows/keepalive.yml` 的 cron：检查频率
