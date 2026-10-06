#!/usr/bin/env python3
"""
HyperAI 免费档实例保活 + 节点自愈。

免费档硬上限 = 单次最长连续运行 2 小时（官方 pricing: "2 vCPU · 4 GB RAM",
Pro 才 8 小时），到点必停，SSH/流量活跃都拦不住。所以保活 = 死了/快到点就拉起来。

流程:
  1. 查实例 status + startedAt
  2. 已停 或 运行 ≥ RESTART_AFTER_MIN 分钟 -> restartWorkspace
  3. 等 RUNNING -> SSH -> 健康检查(端口8001 + nezha进程) -> 必要时重装 sb.sh
  4. 拉 /root/.tmp/sub.txt -> 解出节点链接 -> 写 current.txt
  5. 链接变化时(可选)推 Telegram

环境变量:
  HYPERAI_TOKEN   必需。hyperai 登录态 JWT（约 30 天有效，过期要换）
  TG_BOT_TOKEN    可选。Telegram bot token，用于把新链接推给你
  TG_CHAT_ID      可选。目标 chat id
"""
import base64, json, os, re, socket, ssl, sys, time, urllib.request
from datetime import datetime, timezone

USER_ID = "tx6om1itepfa"
JOB_ID = "rsgfv78615wt"
GQL = "https://app.hyper.ai/gateway/graphql"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

# --- 部署参数（sb.sh 一条龙：nezha agent + sing-box + argo 隧道）---
NEZHA_SERVER = "nezhak2.btpp.ggff.net"
NEZHA_PORT = "443"
NEZHA_KEY = "HRnfDTioTnjzNRRI5w"
SB_CMD = (f"NEZHA_SERVER={NEZHA_SERVER} NEZHA_PORT={NEZHA_PORT} "
          f"NEZHA_KEY={NEZHA_KEY} bash <(curl -Ls https://main.ssss.nyc.mn/sb.sh)")

RESTART_AFTER_MIN = 100        # 距 2h 硬上限留 20 分钟余量，主动重启
TOKEN = os.environ.get("HYPERAI_TOKEN", "").strip()


def log(*a):
    print(f"[{time.strftime('%H:%M:%S')}]", *a, flush=True)


def gql(query, variables=None):
    body = json.dumps({"query": query, "variables": variables or {}}).encode()
    req = urllib.request.Request(GQL, data=body, method="POST", headers={
        "User-Agent": UA, "Content-Type": "application/json",
        "Authorization": f"Bearer {TOKEN}", "Origin": "https://app.hyper.ai",
        "Referer": "https://app.hyper.ai/console", "X-Requested-With": "XMLHttpRequest"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def job_info():
    q = ("query J($u:String!,$j:String!){ job(userId:$u,jobId:$j)"
         "{ id status startedAt password links{ name value } } }")
    d = gql(q, {"u": USER_ID, "j": JOB_ID})
    if "errors" in d:
        raise RuntimeError("graphql: " + json.dumps(d["errors"])[:300])
    return d["data"]["job"]


def restart():
    m = ("mutation R($username:String!,$jobId:String!,$input:RestartWorkspaceInput!)"
         "{ restartWorkspace(userId:$username,jobId:$jobId,input:$input){ id } }")
    inp = {"resource": "free-cpu", "runtime": "pytorch-2.8-2204-cpu",
           "dataBindings": [], "useRDMADevices": False, "ports": []}
    d = gql(m, {"username": USER_ID, "jobId": JOB_ID, "input": inp})
    if "errors" in d:
        raise RuntimeError("restart: " + json.dumps(d["errors"])[:300])


def uptime_min(job):
    s = job.get("startedAt")
    if not s:
        return None
    t = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return (datetime.now(timezone.utc) - t).total_seconds() / 60


def ssh_parts(job):
    pwd = job["password"]
    port = int(re.search(r"-p(\d+)",
               next(l["value"] for l in job["links"] if l["name"] == "ssh")).group(1))
    return port, pwd


def ssh_run(port, pwd, cmd, timeout=300):
    import paramiko
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect("ssh.hyper.ai", port=port, username="root", password=pwd, timeout=25,
              banner_timeout=40, auth_timeout=40, look_for_keys=False, allow_agent=False)
    try:
        b64 = base64.b64encode(cmd.encode()).decode()
        _, out, err = c.exec_command(f"echo {b64} | base64 -d | bash", timeout=timeout)
        o = out.read().decode("utf-8", "replace")
        e = err.read().decode("utf-8", "replace")
        return o, e
    finally:
        c.close()


def healthy(port, pwd):
    """sing-box 在 8001 监听 + nezha agent 进程在 = 活着"""
    o, _ = ssh_run(port, pwd,
                   "ss -tlnp 2>/dev/null | grep -c ':8001'; "
                   "pgrep -fc 'nezhak2.btpp.ggff.net'", timeout=60)
    nums = [int(x) for x in re.findall(r"\d+", o)]
    return len(nums) >= 2 and nums[0] >= 1 and nums[1] >= 1


def fetch_link(port, pwd):
    o, _ = ssh_run(port, pwd, "cat /root/.tmp/sub.txt 2>/dev/null", timeout=60)
    raw = o.strip()
    if not raw:
        return None
    return base64.b64decode(raw + "=" * (-len(raw) % 4)).decode()


def ws_probe(link):
    b64p = link.split("vmess://")[1].strip()
    conf = json.loads(base64.b64decode(b64p + "=" * (-len(b64p) % 4)).decode())
    dom, uuid_, path = conf["host"], conf["id"], conf["path"]
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    s = socket.create_connection((dom, 443), timeout=15)
    s = ctx.wrap_socket(s, server_hostname=dom)
    s.sendall((f"GET {path} HTTP/1.1\r\nHost: {dom}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
               f"Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\nSec-WebSocket-Version: 13\r\n"
               f"Sec-WebSocket-Protocol: {uuid_}\r\n\r\n").encode())
    time.sleep(1)
    line = s.recv(200).decode("utf-8", "replace").split("\r\n")[0]
    s.close()
    return line


def tg_notify(text):
    tok = os.environ.get("TG_BOT_TOKEN", "").strip()
    chat = os.environ.get("TG_CHAT_ID", "").strip()
    if not tok or not chat:
        return
    data = json.dumps({"chat_id": chat, "text": text,
                       "disable_web_page_preview": True}).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{tok}/sendMessage", data=data,
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        urllib.request.urlopen(req, timeout=20)
        log("telegram notified")
    except Exception as e:
        log("telegram notify failed:", e)


def main():
    if not TOKEN:
        print("FATAL: 未配置 HYPERAI_TOKEN secret", file=sys.stderr)
        return 2
    try:
        job = job_info()
    except Exception as e:
        print(f"FATAL: 查询失败(多半是 JWT 过期，重新登录换 HYPERAI_TOKEN): {e}", file=sys.stderr)
        return 3

    st, up = job["status"], uptime_min(job)
    log(f"status={st} uptime={None if up is None else round(up, 1)}min")

    force = os.environ.get("FORCE_FETCH", "").strip().lower() in ("1", "true", "yes")
    need_restart = st != "RUNNING" or (up is not None and up >= RESTART_AFTER_MIN)
    if not need_restart and not force:
        log("实例健康，无需操作")
        return 0

    did_restart = False
    if need_restart:
        if st == "RUNNING":
            log(f"距 2h 硬上限已不足 {120 - RESTART_AFTER_MIN} 分钟，主动重启")
        else:
            log("实例已停，重启中")
        try:
            restart()
        except Exception as e:
            if st == "RUNNING":
                log("运行中不接受重启，跳过本次:", str(e)[:200])
                return 0
            print(f"FATAL: 重启失败 {e}", file=sys.stderr)
            return 4
        for i in range(24):
            time.sleep(15)
            try:
                job = job_info()
            except Exception:
                continue
            log("poll:", job["status"])
            if job["status"] == "RUNNING":
                break
        if job["status"] != "RUNNING":
            print("FATAL: 实例未进入 RUNNING", file=sys.stderr)
            return 5
        did_restart = True

    port, pwd = ssh_parts(job)
    log(f"ssh port={port}")
    ok = False
    for i in range(20):
        try:
            o, _ = ssh_run(port, pwd, "echo SSH_OK", timeout=60)
            if "SSH_OK" in o:
                ok = True
                break
        except Exception:
            pass
        log(f"ssh 未就绪 ({i+1}/20)")
        time.sleep(20)
    if not ok:
        print("FATAL: SSH 连不上", file=sys.stderr)
        return 6

    if healthy(port, pwd):
        log("sing-box + nezha 已在，跳过重装")
    else:
        log("重装 sb.sh ...")
        o, e = ssh_run(port, pwd, SB_CMD, timeout=300)
        log("sb.sh tail:", " | ".join((o or e).strip().splitlines()[-3:]))

    link = None
    for i in range(10):
        link = fetch_link(port, pwd)
        if link:
            break
        time.sleep(10)
    if not link:
        print("FATAL: 拿不到节点链接", file=sys.stderr)
        return 7

    try:
        ws = ws_probe(link)
    except Exception as ex:
        ws = f"probe failed: {ex}"
    log("WS probe:", ws)

    print("\n=== RESULT ===")
    print("restarted:", did_restart)
    print("ws:", ws)
    print("link:", link.strip())
    if did_restart or force:
        tg_notify(f"🔄 hyperai 节点已恢复 ({time.strftime('%m-%d %H:%M UTC', time.gmtime())})\n\n{link.strip()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
