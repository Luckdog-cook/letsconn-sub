#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
netsub 条目提取器 —— 单文件 / 零第三方依赖 / 手机可直接跑
=========================================================
支持：Windows / Linux / Termux(Android) / macOS，Python 3.6+

用法：
  python netsub_client.py                      # 自动注册 + 拉全部条目 + 输出配置源
  python netsub_client.py 账号 密码             # 用已有账号
  python netsub_client.py --exe 10             # 每个地区多抽 10 个 UUID（默认 1）
  python netsub_client.py --areas HK,TW-01     # 只拉指定地区
  python netsub_client.py --serve              # 启动本地分发服务器（手机同 WiFi 直接配置源）
  python netsub_client.py --out /sdcard/Download   # 指定输出目录

输出：
  nodes.txt        换行分隔的 vless:// 链接（可直接粘贴导入）
  sub_base64.txt   base64 配置源内容（客户端「从剪贴板导入」/「分发地址」用）
  sub.html         配置源网页（--serve 时提供）
  nodes.json       完整数据（账号/条目/原始字段）
"""
import os, sys, json, time, random, uuid, base64, hashlib, socket, ssl, gzip, io
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

try:
    from urllib.request import Request, urlopen, build_opener, HTTPSHandler
    from urllib.error import URLError, HTTPError
    from urllib.parse import urlencode
except ImportError:                                  # pragma: no cover
    from urllib2 import Request, urlopen, build_opener, HTTPSHandler, URLError, HTTPError
    from urllib import urlencode

# ══════════════════════════════════════════════════════════════
#  常量
# ══════════════════════════════════════════════════════════════
MIRRORS = ["apis.letsapis.com", "apis.netsub.com", "letswebcn.x1y2.xyz",
           "webcn.letsa1b2.com", "apis.letsapi.xyz", "letswebcn.a1b2.pro"]
VER         = 103          # 请求侧密钥 & 明文头标签
RESP_K      = 178          # 响应侧密钥
PKG         = "com.netsub.android.guanwang"
UA          = "okhttp/4.9.3"
DEF_DEVICE  = "3690A9BC-392E-478D-93F8-6CA575B71BCA"   # 默认 deviceId（改成自己的会更好）

VERBOSE = True
def log(*a):
    if VERBOSE:
        sys.stderr.write(" ".join(str(x) for x in a) + "\n")
        sys.stderr.flush()

# ══════════════════════════════════════════════════════════════
#  加解密（纯 Python，无依赖）
#  加密: c[i] = p[i] ^ c[i-1] ^ K   (prev = previous CIPHERTEXT / 输出)
#  解密: p[i] = c[i] ^ c[i-1] ^ K   (prev = previous CIPHERTEXT / 输入)
# ══════════════════════════════════════════════════════════════
def encrypt_string(s, ver=VER):
    plain = ("%d%s%03d" % (ver, s, random.randint(0, 999))).encode("utf-8")
    out = bytearray(len(plain))
    prev = 0
    for i in range(len(plain)):
        prev = out[i] = plain[i] ^ prev ^ ver
    return base64.b64encode(bytes(out)).decode("ascii")

def _try_decrypt(raw, k):
    out = bytearray(len(raw))
    prev = 0
    for i in range(len(raw)):
        c = raw[i]
        out[i] = c ^ prev ^ k
        prev = c                       # ← 解密：prev 取【输入密文】字节
    try:
        t = out.decode("utf-8")
    except Exception:
        return None
    i, j = t.find("{"), t.rfind("}")
    if 0 <= i < j:
        try:
            return json.loads(t[i:j + 1])
        except Exception:
            return None
    return None

def decrypt_string(b64, k=None):
    """自适应密钥：先试 178，再全扫 0-255。返回 (json, k)"""
    s = b64.strip().replace("\n", "").replace("\r", "")
    try:
        raw = base64.b64decode(s)          # 宽容模式：与原版一致，勿手工补 padding
    except Exception:
        try:
            raw = base64.b64decode(s + "=" * (-len(s) % 4))
        except Exception:
            return None, None
    order = [k] if k else ([RESP_K] + [x for x in range(256) if x != RESP_K])
    for kk in order:
        r = _try_decrypt(raw, kk)
        if r is not None:
            return r, kk
    return None, None

def sign_string(enc, service, t, secret):
    return hashlib.md5(("%s|%s|%s|%s" % (enc, service, t, secret)).encode("utf-8")).hexdigest()

# ══════════════════════════════════════════════════════════════
#  HTTP（只用标准库；自带证书宽松校验，兼容 Android 老 Python）
# ══════════════════════════════════════════════════════════════
class _NoVerify(HTTPSHandler):
    """忽略证书校验（App 本身也是宽松的；兼容老 Android Python）"""
    def __init__(self):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        try:
            ctx.set_ciphers("DEFAULT@SECLEVEL=1")
        except Exception:
            pass
        HTTPSHandler.__init__(self, context=ctx)

try:
    _OPENER = build_opener(_NoVerify)
except TypeError:                                    # 老 Python 不支持 context 参数
    _ctx = ssl._create_unverified_context()
    _OPENER = build_opener(HTTPSHandler(context=_ctx))
_OPENER.addheaders = [("User-Agent", UA)]

def http_post(url, form, timeout=20):
    body = urlencode(form).encode("utf-8")
    req = Request(url, data=body, headers={
        "User-Agent": UA,
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept-Encoding": "gzip",
    })
    try:
        r = _OPENER.open(req, timeout=timeout)
        return r.getcode(), r.read()
    except HTTPError as e:
        return e.code, e.read()

def _maybe_gunzip(raw, enc):
    if (enc or "").lower() == "gzip" or raw[:2] == b"\x1f\x8b":
        try:
            return gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
        except Exception:
            return raw
    return raw

# ══════════════════════════════════════════════════════════════
#  客户端
# ══════════════════════════════════════════════════════════════
class NetSub(object):
    def __init__(self, mirror=None, device_id=None, timeout=20):
        self.mirrors    = ([mirror] if mirror else []) + [m for m in MIRRORS if m != mirror]
        self.base       = None
        self.secret     = None
        self.token      = ""
        self.account    = None
        self.pwd        = None
        self.device_id  = device_id or DEF_DEVICE
        self.timeout    = timeout

    def header(self):
        return {
            "a-app-mac": "",
            "x-app-accessToken": self.token,
            "x-app-channelId": "rocket",
            "x-app-deviceId": self.device_id,
            "x-app-deviceModel": "XIAOMI 2509FPN0BC",
            "x-app-devicename": "2509FPN0BC",
            "x-app-iv": 211221,
            "x-app-language": "zh-Hans",
            "x-app-os": "android",
            "x-app-osversion": "14",
            "x-app-package": PKG,
            "x-app-packageVersion": "1.0.15",
        }

    def rpc(self, service, data=None, retry=2):
        data = data or {}
        errs = []
        for attempt in range(retry + 1):
            t = str(int(time.time() * 1000))
            body = {"data": data, "header": self.header(),
                    "service": service, "time": int(t)}
            js  = json.dumps(body, ensure_ascii=False,
                             separators=(",", ":"), sort_keys=True)
            enc = encrypt_string(js, VER)
            secret = self.device_id if service == "init" else (self.secret or self.device_id)
            sign = sign_string(enc, service, t, secret)
            reqid = str(VER) + str(uuid.uuid4())          # ← 关键：103 + UUID
            path = "/client/api/v1/%s/%s" % (service, reqid)
            for m in self.mirrors:
                url = "https://%s%s" % (m, path)
                try:
                    code, raw = http_post(url, {"enc": enc, "sign": sign}, self.timeout)
                    if code != 200:
                        errs.append("%s:HTTP%s" % (m, code)); continue
                    txt = _maybe_gunzip(raw, "gzip").decode("utf-8", "replace").strip()
                    if not txt:
                        errs.append("%s:empty" % m); continue
                    try:
                        obj = json.loads(txt)
                    except Exception:
                        errs.append("%s:%s" % (m, txt[:50])); continue
                    if isinstance(obj, dict) and "enc" in obj:
                        dec, k = decrypt_string(obj["enc"])
                        if dec is None:
                            errs.append("%s:undecryptable" % m); continue
                        return dec, m
                    return obj, m
                except (URLError, HTTPError, socket.timeout, ssl.SSLError) as e:
                    errs.append("%s:%s" % (m, type(e).__name__))
                except Exception as e:
                    errs.append("%s:%s" % (m, type(e).__name__))
            if attempt < retry:
                time.sleep(1.0 + attempt)
        return {"error": "all mirrors failed", "detail": errs}, None

    # ── 高层 ──
    def init(self):
        r, m = self.rpc("init", {})
        if isinstance(r, dict) and r.get("result") == 200:
            self.base   = m
            self.secret = r["data"].get("clientSecret")
            out = dict(r["data"]); out["result"] = 200
            return out
        return r

    def _after_auth(self, r):
        if isinstance(r, dict) and r.get("result") == 200:
            ui = r.get("data", {}).get("userInfo", {})
            self.token = ui.get("token", "")
            return ui
        return r

    def regist(self, account, pwd):
        r, _ = self.rpc("regist", {"account": account, "pwd": pwd})
        if isinstance(r, dict) and r.get("result") == 200:
            self.account, self.pwd = account, pwd
        return self._after_auth(r)

    def login(self, account, pwd):
        r, _ = self.rpc("login", {"account": account, "pwd": pwd})
        if isinstance(r, dict) and r.get("result") == 200:
            self.account, self.pwd = account, pwd
        return self._after_auth(r)

    def areas(self):
        r, _ = self.rpc("userarealist", {})
        if isinstance(r, dict) and r.get("result") == 200:
            return r.get("data", {}).get("areaList", [])
        return r

    def allocate(self, area_code):
        r, _ = self.rpc("allocate", {"area": str(area_code)})
        return r

    def proxy(self, node_obj, name=None):
        pc = node_obj.get("protocolConfig", {}) or {}
        q = {}
        ws = pc.get("ws") or pc.get("wsPath") or pc.get("ws-path")
        q["type"] = "ws" if ws else "tcp"
        if ws:
            q["path"] = pc.get("ws-path") or pc.get("wsPath") or "/"
            host = pc.get("ws-host") or pc.get("wsHost")
            if host:
                q["host"] = host
        if pc.get("tls"):
            q["security"] = "tls"
            sni = pc.get("sni") or pc.get("ws-host") or pc.get("server")
            if sni:
                q["sni"] = sni
        if pc.get("mode"):
            q["mode"] = pc["mode"]
        frag = name or node_obj.get("name") or pc.get("server", "")
        return "vless://%s@%s:%s?%s#%s" % (
            pc.get("uuid"), pc.get("server"), pc.get("port"),
            urlencode(q), frag)

# ══════════════════════════════════════════════════════════════
#  辅助
# ══════════════════════════════════════════════════════════════
def new_device_id():
    h = uuid.uuid4().hex.upper()
    return "%s-%s-%s-%s-%s" % (h[0:8], h[8:12], h[12:16], h[16:20], h[20:32])

def rand_account():
    return random.choice("abcdefghijklmnopqrstuvwxyz") + str(random.randint(10000000, 99999999))

def rand_pwd(n=10):
    s = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ23456789"
    return "".join(random.choice(s) for _ in range(n))

def default_outdir():
    """手机优先 /sdcard/Download，其次脚本所在目录"""
    for p in ("/sdcard/Download", "/storage/emulated/0/Download"):
        if os.path.isdir(p):
            return os.path.join(p, "netsub")
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")

def local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80)); ip = s.getsockname()[0]; s.close()
        return ip
    except Exception:
        return "127.0.0.1"

def b64sub(text):
    """标准 VPN 配置源 base64（无换行）"""
    return base64.b64encode(text.encode("utf-8")).decode("ascii")

# ══════════════════════════════════════════════════════════════
#  分发服务器
# ══════════════════════════════════════════════════════════════
class _SubHandler(BaseHTTPRequestHandler):
    nodes_txt = b""

    def log_message(self, *a):
        pass

    def do_GET(self):
        p = self.path.split("?")[0].rstrip("/") or "/"
        if p in ("/", "/sub", "/sub.txt", "/plain"):
            body = self.nodes_txt
            ctype = "text/plain; charset=utf-8"
        elif p == "/sub64":
            body = b64sub(self.nodes_txt.decode("utf-8")).encode()
            ctype = "text/plain; charset=utf-8"
        elif p == "/nodes.json":
            body = self.nodes_txt
            ctype = "application/json; charset=utf-8"
        else:
            self.send_error(404); return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Profile-Update-Interval", "6")
        self.send_header("Subscription-Userinfo", "upload=0; download=0; total=0; expire=0")
        self.end_headers()
        self.wfile.write(body)

def serve(port=8787, blob=""):
    _SubHandler.nodes_txt = blob.encode("utf-8")
    ip = local_ip()
    srv = HTTPServer(("0.0.0.0", port), _SubHandler)
    log("")
    log("═" * 58)
    log("  分发服务器已启动")
    log("═" * 58)
    log("  手机/同 WiFi 设备分发地址：")
    log("    http://%s:%d/sub        (明文链接)" % (ip, port))
    log("    http://%s:%d/sub64      (base64 配置源)" % (ip, port))
    log("")
    log("  客户端A / 客户端B: 配置源 → 地址填上面的 /sub64")
    log("  客户端C: 同上，或直接粘贴 nodes.txt")
    log("  按 Ctrl+C 停止")
    log("═" * 58)
    log("")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        log("\n[+] 已停止")

# ══════════════════════════════════════════════════════════════
#  主流程
# ══════════════════════════════════════════════════════════════
def parse_args(argv):
    o = {"account": None, "pwd": None, "out": None, "exe": 1,
         "areas": None, "serve": False, "port": 8787, "quiet": False,
         "device": None, "mirror": None}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("--out", "-o"):        i += 1; o["out"] = argv[i]
        elif a in ("--exe", "-e"):      i += 1; o["exe"] = max(1, int(argv[i]))
        elif a in ("--areas", "-a"):    i += 1; o["areas"] = [x.strip() for x in argv[i].split(",") if x.strip()]
        elif a in ("--port", "-p"):     i += 1; o["port"] = int(argv[i])
        elif a in ("--device", "-d"):   i += 1; o["device"] = argv[i]
        elif a in ("--mirror", "-m"):   i += 1; o["mirror"] = argv[i]
        elif a == "--serve":            o["serve"] = True
        elif a in ("--quiet", "-q"):    o["quiet"] = True
        elif a in ("--help", "-h"):
            print(__doc__); sys.exit(0)
        elif not a.startswith("-"):
            if o["account"] is None:    o["account"] = a
            elif o["pwd"] is None:      o["pwd"] = a
        i += 1
    return o

def main():
    global VERBOSE
    o = parse_args(sys.argv[1:])
    if o["quiet"]:
        VERBOSE = False
    outdir = o["out"] or default_outdir()
    try:
        os.makedirs(outdir)
    except OSError:
        pass

    log("[*] 输出目录: %s" % outdir)
    c = NetSub(mirror=o["mirror"], device_id=o["device"] or new_device_id())
    log("[*] deviceId: %s" % c.device_id)

    # ── 1. init ──
    r = c.init()
    if not (isinstance(r, dict) and r.get("result") == 200):
        log("[!] init 失败: %s" % json.dumps(r, ensure_ascii=False)[:300])
        log("    → 检查网络 / 换镜像域名（--mirror）")
        return 1
    areas = r.get("areaList", []) or []
    log("[+] init OK  mirror=%s  clientSecret=%s  %d 个地区"
        % (c.base, c.secret, len(areas)))

    # ── 2. 注册 / 登录（自动绕 557）──
    if o["account"]:
        ui = c.login(o["account"], o["pwd"] or "")
        res = ui.get("result") if isinstance(ui, dict) else "?"
        if res == 200:
            log("[+] 登录成功  timeLong=%ss" % ui.get("timeLong"))
        else:
            log("[!] 登录失败: %s" % json.dumps(ui, ensure_ascii=False)[:200]); return 1
    else:
        ok = False
        for attempt in range(8):
            acc, pwd = rand_account(), rand_pwd()
            rr, _ = c.rpc("regist", {"account": acc, "pwd": pwd})
            res = rr.get("result") if isinstance(rr, dict) else "?"
            msg = rr.get("msg", "") if isinstance(rr, dict) else ""
            if res == 200:
                c.account, c.pwd = acc, pwd
                c.token = rr.get("data", {}).get("userInfo", {}).get("token", "")
                ui = rr["data"]["userInfo"]
                log("[+] 注册成功 %s / %s    level=%s timeLong=%ss"
                    % (acc, pwd, ui.get("level"), ui.get("timeLong")))
                ok = True
                break
            elif res == 557:                                    # 设备注册太频繁
                c.device_id = new_device_id()
                log("[~] 557 换设备ID → %s" % c.device_id)
            elif res == 541:
                log("[~] 541 换账号格式重试")
            else:
                log("[~] regist %s %s" % (res, msg))
            time.sleep(0.8)
        if not ok:
            log("[!] 注册失败（8 次重试）"); return 1

    # ── 3. 刷新 secret（带 token）──
    r2 = c.init()
    if isinstance(r2, dict) and r2.get("result") == 200:
        c.secret = r2.get("clientSecret")
        areas = r2.get("areaList", areas)

    # ── 4. 地区列表 ──
    al = c.areas()
    if not isinstance(al, list):
        log("[~] userarealist 失败(%s)，用 init 的列表"
            % (al.get("result") if isinstance(al, dict) else "?"))
        al = areas
    if o["areas"]:
        want = set(o["areas"])
        al = [a for a in al if str(a.get("areaCode")) in want]
    log("[+] %d 个地区待抽取" % len(al))

    # ── 5. allocate ──
    nodes, seen = [], set()
    for idx, a in enumerate(al, 1):
        code = a.get("areaCode"); nm = a.get("name") or str(code)
        got = 0
        for k in range(o["exe"]):
            rr, _ = c.rpc("allocate", {"area": str(code)})
            if isinstance(rr, dict) and rr.get("result") == 200:
                n = rr["data"]; n["name"] = nm
                u = c.proxy(n, name="%s#%d" % (nm, k + 1) if o["exe"] > 1 else nm)
                if u in seen:
                    continue
                seen.add(u)
                nodes.append({"area": code, "name": nm, "node": n, "proxy": u})
                got += 1
                log("    [%2d/%2d] %-9s %s" % (idx, len(al), code, u))
            else:
                log("    [%2d/%2d] %-9s FAIL %s" % (idx, len(al), code,
                    rr.get("result") if isinstance(rr, dict) else rr))
                break
            if o["exe"] > 1:
                time.sleep(0.25)
        time.sleep(0.3)

    if not nodes:
        log("[!] 没抽到任何条目"); return 1

    # ── 6. 输出 ──
    txt = "\n".join(n["proxy"] for n in nodes) + "\n"
    p_txt = os.path.join(outdir, "nodes.txt")
    p_b64 = os.path.join(outdir, "sub_base64.txt")
    p_json = os.path.join(outdir, "nodes.json")
    with open(p_txt, "w", encoding="utf-8") as f:  f.write(txt)
    with open(p_b64, "w", encoding="utf-8") as f:  f.write(b64sub(txt))
    with open(p_json, "w", encoding="utf-8") as f:
        json.dump({"account": c.account, "pwd": c.pwd, "token": c.token,
                   "clientSecret": c.secret, "mirror": c.base,
                   "deviceId": c.device_id, "ts": int(time.time()),
                   "count": len(nodes), "nodes": nodes},
                  f, ensure_ascii=False, indent=2)

    log("")
    log("═" * 58)
    log("  ✅ 完成：%d 个条目" % len(nodes))
    log("═" * 58)
    log("  %s" % p_txt)
    log("  %s" % p_b64)
    log("  %s" % p_json)
    log("")
    log("  账号: %s   密码: %s" % (c.account, c.pwd))
    log("  试用: timeLong 约 299 秒，过期后重新运行本脚本换新号")
    log("═" * 58)

    if o["serve"]:
        serve(o["port"], txt)
    else:
        log("")
        log("  ▸ 手机同 WiFi 想直接配置源？加 --serve 参数重跑，")
        log("    然后在 客户端A「配置源」里填提示的地址。")
        log("")
    return 0

if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        log("\n[!] 已中断")
        sys.exit(130)
