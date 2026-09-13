# 📱 在手机上拿到这些条目 —— 三种方案

> 脚本 `netsub_client.py` 是**单文件、零第三方依赖**（只用 Python 标准库），
> Windows / Linux / macOS / Termux(Android) 都能直接跑。

---

## 方案 A：电脑跑脚本 → 手机配置源 ⭐ 最推荐（30 秒搞定）

**你的情况（Windows + 手机同 WiFi）最适合这个。**

### A1. 电脑上启动分发服务器

```powershell
cd C:\GZQ\netsub
python netsub_client.py --serve
```

跑完会打印：

```
══════════════════════════════════════════════════════════
  分发服务器已启动
══════════════════════════════════════════════════════════
  手机/同 WiFi 设备分发地址：
    http://10.0.0.101:8787/sub        (明文链接)
    http://10.0.0.101:8787/sub64      (base64 配置源)
══════════════════════════════════════════════════════════
```

> `10.0.0.101` 是自动探测的本机局域网 IP（你当前就是它）。
> 手机必须和电脑连**同一个 WiFi**。若连不上，检查 Windows 防火墙：
> ```powershell
> New-NetFirewallRule -DisplayName "netsub sub" -Direction Inbound -LocalPort 8787 -Protocol TCP -Action Allow
> ```

### A2. 手机上配置源

| 客户端 | 操作 |
|---|---|
| **客户端A** | 左上角 ☰ → 配置源 → ➕ → 地址填 `http://10.0.0.101:8787/sub64` → 保存 → 返回主页 → 右上角 ⋮ → **更新配置源** |
| **客户端B / sing-box** | 新建配置 → 类型「配置源」→ URL 填 `http://10.0.0.101:8787/sub64` |
| **客户端C** | 首页右上角 ➕ → 类型「Subscribe」→ URL 同上 |
| **Karing**（你电脑上装了）| 配置 → 添加 → 分发地址 → 同上 |

**优点**：电脑上跑一次，手机上「更新配置源」随时刷新，还会自动换新 UUID。

### A3. 不想开服务器？直接传文件

```powershell
# 推到手机下载目录，手机文件管理器点一下就能导入
adb push out\nodes.txt /sdcard/Download/netsub_nodes.txt
adb push out\sub_base64.txt /sdcard/Download/netsub_sub.txt
```

手机上：客户端A → ☰ → **从剪贴板导入**（先打开 `sub_base64.txt` 全选复制）或
文件管理器长按 `nodes.txt` → 打开方式 → 客户端A。

---

## 方案 B：手机本地跑 Python（Termux）

**适合：手机常出门、想脱离电脑自己跑。**

### B1. 装 Termux

从 **F-Droid** 下载（不要用 Google Play 版，那个已停更且路径不同）：
<https://f-droid.org/packages/com.termux/>

### B2. 一键安装脚本

把 `netsub_client.py` 传到手机（adb push 或微信/QQ 传文件），然后在 Termux 里：

```bash
# 授予存储权限（第一次）
termux-setup-storage

# 把脚本放到 Termux 家目录
cp /sdcard/Download/netsub_client.py ~/
cd ~

# 无需 pip install！脚本只用标准库
python netsub_client.py
```

**Termux 自带 Python**，如果提示 `python: command not found`：

```bash
pkg update && pkg install python -y
```

### B3. 运行结果

```
[*] 输出目录: /sdcard/Download/netsub
[+] init OK  mirror=apis.letsapis.com  40 个地区
[+] 注册成功 x12345678 / Ab3xY9kLm2    level=Free timeLong=299s
    [ 1/40] 85        vless://...@lczx-hk01.example.com:58001?...
    ...
  ✅ 完成：38 个条目
  /sdcard/Download/netsub/nodes.txt
  /sdcard/Download/netsub/sub_base64.txt
  /sdcard/Download/netsub/nodes.json
```

脚本会自动识别手机并把结果写到 `/sdcard/Download/netsub/`。

### B4. 手机上直接配置源自己

```bash
# Termux 里启动本地分发服务器
python netsub_client.py --serve --port 8787
# 然后 客户端A 分发地址填 http://127.0.0.1:8787/sub64
```

### B5. 做成快捷命令（可选）

```bash
echo 'alias vpn="python ~/netsub_client.py"' >> ~/.bashrc
source ~/.bashrc
# 以后终端里敲 vpn 就行
```

---

## 方案 C：完全不要 Python —— 用电脑生成、手机永久配置源

**适合：只想偶尔更新一次。**

1. 电脑跑 `python netsub_client.py`，得到 `out\sub_base64.txt`
2. 把 `sub_base64.txt` 的内容**做成一个静态网页**（GitHub Pages / Gist / 任意空间）
3. 手机添加这个源固定 URL

或者用**已有的在线配置源转换**：把 `nodes.txt` 里的 vless 链接粘进
<https://acl4ssr-sub.github.io/> 之类的转换器，生成一个分发链接。

---

## ⚠️ 已知限制（务必知道）

| 限制 | 说明 |
|---|---|
| **试用期 299 秒** | 新号只有约 5 分钟。条目 UUID **不会因此失效**（UUID 是服务端签发的，条目本身能用），但**账号到期后可能需要重新注册**。想长期用就定期重跑脚本换新 UUID。 |
| **每设备注册限额** | 同一 `deviceId` 只能注册有限次，脚本已自动换 ID 绕过（557 → 换 ID）。 |
| **域名可能被墙** | 若 `init` 失败，试 `--mirror webcn.letsa1b2.com` 换镜像。 |
| **条目域名 DNS** | `lczx-*.example.com` 在某些 DNS 上解析失败，手机 DNS 建议设为 `223.5.5.5`（阿里）或 `1.1.1.1`。 |
| **不要用这个做违法的事** | 仅供协议研究与个人测试。 |

---

## 📋 参数速查

```bash
python netsub_client.py [账号] [密码] [选项]

选项：
  --out DIR        输出目录（默认：手机→/sdcard/Download/netsub，电脑→out/）
  --exe N          每个地区抽 N 个 UUID（默认 1；抽 10 个能得到 400 个条目）
  --areas HK,TW-01 只抽指定地区（逗号分隔的 areaCode）
  --serve          跑完后启动分发服务器
  --port 8787      分发服务器端口
  --mirror DOMAIN  强制指定镜像域名
  --device UUID    指定 deviceId（不指定则随机，用于绕注册限额）
  --quiet          安静模式
  -h, --help       帮助

示例：
  python netsub_client.py                            # 全自动，拉全部 40 地区
  python netsub_client.py --exe 5 --serve            # 每区 5 个 UUID + 开配置源
  python netsub_client.py --areas HK,TW-01,JP-01     # 只拉港台日
  python netsub_client.py abc12345 MyPass88          # 用已有账号
```

---

## 🔧 排错

| 现象 | 原因 / 解法 |
|---|---|
| `init 失败: all mirrors failed` | 网络不通或全被墙 → 换 `--mirror`，或挂梯子跑脚本 |
| `undecryptable` | 服务端换了响应密钥 → 脚本会自动全扫 0-255，若全失败说明协议改了 |
| `SSLError` | 老 Android Python 证书问题 → 脚本已内置 `CERT_NONE`，仍失败则 `pkg install ca-certificates` |
| `Permission denied` 写文件 | `termux-setup-storage` 授权；或 `--out ~/netsub` |
| 手机连不上电脑的分发地址 | Windows 防火墙没放行 8787（见 A1 的命令） |
| 配置源更新后条目没变 | 客户端A 里手动「更新配置源」，或分发地址加 `/sub64` 后缀 |
