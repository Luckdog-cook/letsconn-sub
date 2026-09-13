# netsub 条目分发服务

在服务器上常驻运行，定时自动提取 **PROXY** 条目，对外提供**固定分发地址**。
手机端（客户端A / 客户端B / 客户端C / 客户端D）只需配置源一次，之后自动更新，**永久不用改地址**。

> 零第三方依赖，纯标准库，Python 3.6+。

---

## 快速开始

```bash
# 直接跑（默认：每 4 分钟刷新一次，监听 8787）
python3 netsub_server.py

# 自定义
python3 netsub_server.py --interval 240 --port 8080
```

启动后终端会打印你的固定分发地址：

```
http://<服务器IP>:8787/sub64      ← 添加这个源（base64 格式）
http://<服务器IP>:8787/sub        ← 明文 proxy 链接
http://<服务器IP>:8787/status     ← 状态 JSON
http://<服务器IP>:8787/health     ← 健康检查
```

**手机端**：客户端A → 配置源 → 地址填 `http://<服务器IP>:8787/sub64` → 更新。

---

## 部署方式

### 方式 A：systemd（推荐）

```bash
sudo mkdir -p /opt/netsub
sudo cp netsub_server.py netsub_client.py /opt/netsub/
sudo cp deploy/netsub.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now netsub
sudo systemctl status netsub

# 看日志
journalctl -u netsub -f
```

### 方式 B：Docker

```bash
docker build -t netsub .
docker run -d --name netsub \
  --restart unless-stopped \
  -p 8787:8787 \
  -v $(pwd)/out:/app/out \
  netsub
```

或 docker compose：

```bash
docker compose up -d
```

### 方式 C：cron（只定时生成文件，不起 HTTP 服务）

如果你已有 nginx/caddy 托管静态文件：

```bash
# 每 4 分钟跑一次，把配置源写到 web 目录
*/4 * * * * cd /opt/netsub && /usr/bin/python3 netsub_server.py --once --out /var/www/html/sub >> /var/log/netsub.log 2>&1
```

然后分发地址就是 `http://你的域名/sub/sub_base64.txt`。

---

## 参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `--interval, -i` | `240` | 刷新间隔（秒），最小 30 |
| `--port, -p` | `8787` | HTTP 监听端口 |
| `--exe, -e` | `1` | 每个地区抽几个条目（>1 会增加风控风险） |
| `--areas, -a` | 全部 | 只抽指定地区码，逗号分隔，如 `85,86,87` |
| `--mirror, -m` | 自动 | 手动指定 API 镜像域名 |
| `--out, -o` | `./out` | 产物输出目录 |
| `--once` | — | 只跑一次就退出（用于 cron / 调试） |

---

## 重要说明

### 关于刷新间隔

试用账号有效期约 **299 秒**，所以：

- **间隔设为 240 秒**（默认）留了 60 秒缓冲，比较稳妥
- 设太短（如 60 秒）会疯狂注册新账号，**服务端有 IP 维度风控**，容易被封
- 服务端**永不返回空配置源**：刷新失败时保留上一次的条目，客户端不会突然断线

### 关于条目可用性

服务端下发的条目域名（`lczx-*.example.com`）**在公网 DNS 中无记录**，需要靠 App 内部的解析机制。
本仓库**只负责提取和分发条目配置**，不保证条目在任意网络环境下都能连通 —— 这部分取决于你的网络环境。

### 关于安全

- `/sub64` 是**无鉴权**的公开地址，任何人知道 IP:端口 就能拿到配置源
- **强烈建议**：用 nginx/caddy 反代 + 加个路径前缀或 basic auth，别直接暴露端口
- 别把端口直接暴露到公网而无任何保护

示例 nginx 反代：

```nginx
location /aBcD1234/ {
    proxy_pass http://127.0.0.1:8787/;
}
```

这样分发地址变成 `http://your.domain/aBcD1234/sub64`，加了层隐蔽性。

---

## 文件说明

| 文件 | 说明 |
|---|---|
| `netsub_client.py` | 协议库（加密/签名/RPC），零依赖 |
| `netsub_server.py` | 分发服务端（本仓库主程序） |
| `deploy/netsub.service` | systemd unit 文件 |
| `Dockerfile` | Docker 镜像 |
| `docker-compose.yml` | Docker Compose 配置 |

---

## 免责声明

本项目仅供**网络协议研究与技术学习**使用。使用者应自行遵守所在地区法律法规。
作者不对任何使用后果负责。
