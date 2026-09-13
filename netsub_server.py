# -*- coding: utf-8 -*-
"""
netsub 分发服务端 (Server Edition)
=====================================

在服务器上常驻运行，定时自动提取 proxy 条目，对外暴露**固定分发地址**。

用法
----
    python netsub_server.py                  # 默认：每 4 分钟刷新，端口 8787
    python netsub_server.py --interval 240   # 自定义间隔（秒）
    python netsub_server.py --port 8080
    python netsub_server.py --once           # 只跑一次不常驻

固定分发地址
------------
    http://<服务器IP>:8787/sub        明文 proxy 链接
    http://<服务器IP>:8787/sub64      base64 配置源（客户端A/客户端B/客户端D 通用）
    http://<服务器IP>:8787/health     健康检查
    http://<服务器IP>:8787/status     状态 JSON

设计要点
--------
* **永不返回空配置源**：刷新失败时保留上一次的条目，客户端不会掉线
* 单线程刷新 + 线程锁，避免并发重复注册触发风控
* 刷新间隔默认 240s（试用期 299s，留缓冲）
* 零第三方依赖，Python 3.6+
"""

import base64
import json
import os
import random
import socket
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from netsub_client import NetSub, b64sub, log, VERBOSE  # noqa: E402

# ══════════════════════════════════════════════════════════════
#  全局状态（被 HTTP 线程与刷新线程共享）
# ══════════════════════════════════════════════════════════════
STATE = {
    "nodes_txt": "",      # 当前有效的条目明文
    "count": 0,
    "last_ok": 0,         # 上次成功刷新时间戳
    "last_try": 0,        # 上次尝试时间戳
    "last_err": "",
    "runs": 0,            # 成功刷新次数
    "fails": 0,           # 失败次数
    "account": "",
}
LOCK = threading.Lock()


def new_device_id():
    h = uuid.uuid4().hex.upper()
    return "%s-%s-%s-%s-%s" % (h[0:8], h[8:12], h[12:16], h[16:20], h[20:32])


def rand_account():
    return random.choice("abcdefghijklmnopqrstuvwxyz") + str(random.randint(10000000, 99999999))


def rand_pwd(n=10):
    s = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ23456789"
    return "".join(random.choice(s) for _ in range(n))


# ══════════════════════════════════════════════════════════════
#  核心：抓一次条目
# ══════════════════════════════════════════════════════════════
def fetch_nodes(exe=1, areas=None, mirror=None, timeout_retry=3):
    """
    跑完整流程拿条目。返回 (txt, count, err)
    失败时 txt 为空字符串。
    """
    for attempt in range(timeout_retry):
        try:
            c = NetSub(mirror=mirror, device_id=new_device_id())

            # init
            r = c.init()
            if not (isinstance(r, dict) and r.get("result") == 200):
                err = "init 失败: %s" % json.dumps(r, ensure_ascii=False)[:160]
                log("[!] %s" % err)
                time.sleep(3)
                continue

            # 注册（自动绕 557）
            ok = False
            for _ in range(8):
                acc, pwd = rand_account(), rand_pwd()
                rr, _ = c.rpc("regist", {"account": acc, "pwd": pwd})
                res = rr.get("result") if isinstance(rr, dict) else "?"
                if res == 200:
                    c.account, c.pwd = acc, pwd
                    c.token = rr.get("data", {}).get("userInfo", {}).get("token", "")
                    ok = True
                    break
                if res == 557:
                    c.device_id = new_device_id()
                    log("[~] 557 → 换设备ID")
                time.sleep(0.8)
            if not ok:
                log("[!] 注册失败")
                time.sleep(5)
                continue

            # 带 token 再 init 刷 secret
            r2 = c.init()
            if isinstance(r2, dict) and r2.get("result") == 200:
                c.secret = r2.get("clientSecret") or c.secret

            # 地区列表
            al = c.areas()
            if not isinstance(al, list):
                al = r.get("areaList", []) or []
            if areas:
                want = set(str(x) for x in areas)
                al = [a for a in al if str(a.get("areaCode")) in want]
            if not al:
                log("[!] 地区列表为空")
                time.sleep(3)
                continue

            # allocate
            nodes, seen = [], set()
            for a in al:
                code = a.get("areaCode")
                nm = a.get("name") or str(code)
                for k in range(exe):
                    rr, _ = c.rpc("allocate", {"area": str(code)})
                    if isinstance(rr, dict) and rr.get("result") == 200:
                        n = rr["data"]
                        u = c.proxy(n, name="%s#%d" % (nm, k + 1) if exe > 1 else nm)
                        if u in seen:
                            continue
                        seen.add(u)
                        nodes.append({"area": code, "name": nm, "node": n, "proxy": u})
                    else:
                        break
                    if exe > 1:
                        time.sleep(0.25)
                time.sleep(0.25)

            if not nodes:
                log("[!] 未抽到条目")
                time.sleep(3)
                continue

            txt = "\n".join(n["proxy"] for n in nodes) + "\n"
            log("[+] 抓取成功: %d 个条目 (账号 %s)" % (len(nodes), c.account))
            return txt, len(nodes), "", c.account

        except Exception as e:
            log("[!] 异常: %r" % e)
            time.sleep(4)

    return "", 0, "全部重试失败", ""


# ══════════════════════════════════════════════════════════════
#  刷新线程
# ══════════════════════════════════════════════════════════════
def refresher(interval, exe, areas, mirror, outdir):
    """常驻循环：定时刷新配置源内容"""
    while True:
        with LOCK:
            STATE["last_try"] = int(time.time())

        txt, cnt, err, acct = fetch_nodes(exe=exe, areas=areas, mirror=mirror)

        with LOCK:
            if txt:
                STATE["nodes_txt"] = txt
                STATE["count"] = cnt
                STATE["last_ok"] = int(time.time())
                STATE["last_err"] = ""
                STATE["runs"] += 1
                STATE["account"] = acct
                # 落盘留档
                try:
                    with open(os.path.join(outdir, "nodes.txt"), "w", encoding="utf-8") as f:
                        f.write(txt)
                    with open(os.path.join(outdir, "sub_base64.txt"), "w", encoding="utf-8") as f:
                        f.write(b64sub(txt))
                except Exception:
                    pass
            else:
                STATE["fails"] += 1
                STATE["last_err"] = err
                if STATE["nodes_txt"]:
                    log("[~] 本次失败，继续沿用旧条目（%d 个）" % STATE["count"])
                else:
                    log("[!] 本次失败且无可用旧条目")

        # 等到下一个周期
        time.sleep(interval)


# ══════════════════════════════════════════════════════════════
#  HTTP 分发服务
# ══════════════════════════════════════════════════════════════
class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, body, ctype, extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        # 配置源客户端据此自动重拉
        self.send_header("Profile-Update-Interval", "6")
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        p = self.path.split("?")[0].rstrip("/") or "/"

        with LOCK:
            txt = STATE["nodes_txt"]
            cnt = STATE["count"]
            last_ok = STATE["last_ok"]
            err = STATE["last_err"]
            runs = STATE["runs"]
            fails = STATE["fails"]

        if p in ("/", "/sub", "/sub.txt"):
            if not txt:
                self.send_error(503, "Node list warming up, retry in ~30s")
                return
            self._send(txt, "text/plain; charset=utf-8",
                       {"Subscription-Userinfo":
                        "upload=0; download=0; total=0; expire=%d" % (last_ok + 300)})

        elif p == "/sub64":
            if not txt:
                self.send_error(503, "Node list warming up, retry in ~30s")
                return
            self._send(b64sub(txt), "text/plain; charset=utf-8",
                       {"Subscription-Userinfo":
                        "upload=0; download=0; total=0; expire=%d" % (last_ok + 300)})

        elif p == "/nodes.json":
            self._send(json.dumps(
                {"count": cnt, "last_ok": last_ok, "age": int(time.time()) - last_ok,
                 "runs": runs, "fails": fails,
                 "nodes": [l for l in txt.splitlines() if l.strip()]},
                ensure_ascii=False, indent=2),
                "application/json; charset=utf-8")

        elif p == "/health":
            self._send("ok\n", "text/plain; charset=utf-8")

        elif p == "/status":
            self._send(json.dumps(
                {"ok": bool(txt), "count": cnt, "runs": runs, "fails": fails,
                 "last_ok": last_ok, "age_sec": int(time.time()) - last_ok if last_ok else None,
                 "last_err": err, "account": STATE["account"]},
                ensure_ascii=False, indent=2),
                "application/json; charset=utf-8")

        else:
            self.send_error(404)


def local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


# ══════════════════════════════════════════════════════════════
#  入口
# ══════════════════════════════════════════════════════════════
def parse(argv):
    o = {"interval": 240, "port": 8787, "exe": 1, "areas": None,
         "mirror": None, "once": False, "outdir": None}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("--interval", "-i"):   i += 1; o["interval"] = max(30, int(argv[i]))
        elif a in ("--port", "-p"):     i += 1; o["port"] = int(argv[i])
        elif a in ("--exe", "-e"):      i += 1; o["exe"] = max(1, int(argv[i]))
        elif a in ("--areas", "-a"):    i += 1; o["areas"] = [x.strip() for x in argv[i].split(",") if x.strip()]
        elif a in ("--mirror", "-m"):   i += 1; o["mirror"] = argv[i]
        elif a in ("--out", "-o"):      i += 1; o["outdir"] = argv[i]
        elif a == "--once":             o["once"] = True
        elif a in ("--help", "-h"):
            print(__doc__); sys.exit(0)
        i += 1
    return o


def main():
    o = parse(sys.argv[1:])
    outdir = o["outdir"] or os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
    try:
        os.makedirs(outdir)
    except OSError:
        pass

    log("")
    log("=" * 62)
    log("  netsub 分发服务端")
    log("=" * 62)
    log("  刷新间隔 : %d 秒" % o["interval"])
    log("  监听端口 : %d" % o["port"])
    log("  输出目录 : %s" % outdir)
    log("=" * 62)
    log("")

    # --once：跑一次就退出（用于 cron / 调试）
    if o["once"]:
        txt, cnt, err, acct = fetch_nodes(exe=o["exe"], areas=o["areas"], mirror=o["mirror"])
        if not txt:
            log("[!] 失败: %s" % err)
            return 1
        with open(os.path.join(outdir, "nodes.txt"), "w", encoding="utf-8") as f:
            f.write(txt)
        with open(os.path.join(outdir, "sub_base64.txt"), "w", encoding="utf-8") as f:
            f.write(b64sub(txt))
        log("[+] 完成 %d 个条目 → %s" % (cnt, outdir))
        return 0

    # 首次同步抓一次（避免客户端一开始拿到 503）
    log("[*] 首次抓取（约 30-60 秒）...")
    txt, cnt, err, acct = fetch_nodes(exe=o["exe"], areas=o["areas"], mirror=o["mirror"])
    if txt:
        with LOCK:
            STATE["nodes_txt"] = txt
            STATE["count"] = cnt
            STATE["last_ok"] = int(time.time())
            STATE["runs"] = 1
            STATE["account"] = acct
        log("[+] 首次成功: %d 个条目" % cnt)
    else:
        log("[!] 首次失败 (%s)，服务仍会启动并自动重试" % err)

    # 启动刷新线程
    t = threading.Thread(target=refresher,
                         args=(o["interval"], o["exe"], o["areas"], o["mirror"], outdir),
                         daemon=True)
    t.start()

    ip = local_ip()
    log("")
    log("=" * 62)
    log("  ✅ 分发服务已启动 —— 固定分发地址")
    log("=" * 62)
    log("    http://%s:%d/sub64      ← 客户端A / 客户端B / 客户端D" % (ip, o["port"]))
    log("    http://%s:%d/sub        ← 明文链接" % (ip, o["port"]))
    log("    http://%s:%d/status     ← 状态 JSON" % (ip, o["port"]))
    log("")
    log("  手机端只需配置源一次，之后自动更新，永久不用改。")
    log("  按 Ctrl+C 停止。")
    log("=" * 62)

    try:
        HTTPServer(("0.0.0.0", o["port"]), Handler).serve_forever()
    except KeyboardInterrupt:
        log("\n[+] 已停止")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
