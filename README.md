# 🚀 Study Contribution Wall —— 云端个人学习打卡墙

GitHub 提交热力图风格的个人学习记录网站。**零第三方依赖**：后端仅用 Python 3 标准库，前端为单文件原生 HTML/CSS/JS，数据保存在本地 JSON 文件，一台最便宜的云服务器即可运行。

界面采用现代双主题设计：☀️ 亮色 / 🌙 暗色一键切换（右上角太阳/月亮按钮），自动跟随系统偏好并记住你的选择；配有氛围渐变背景、入场动画、数字滚动、提交脉冲反馈等细节，电脑 / 平板 / 手机三端精细适配。热力图按**自然年**展示（1 月~12 月），卡片右上角的年份选择器支持箭头翻年与下拉菜单切换，自动列出所有有记录的年份并显示各年提交次数，选择会被记住；当前年自动定位到今天，补录其他年份的记录时会自动跳转到该年。

## 目录结构

```text
study-tracker/
├── server.py             # 后端服务（鉴权 / 限流 / 原子写入 / 自动备份）
├── index.html            # 前端单页应用（热力图 / 统计 / 提交 / 导入导出）
├── commits.example.json  # 示例数据（可导入体验；真实数据文件自动生成）
├── start.sh              # nohup 一键启动（简单场景）
├── install.sh            # ★推荐：systemd 安装（开机自启 + 崩溃自动重启）
├── change-password.sh    # 修改管理密码
├── backup.sh             # 手动全量打包备份
├── restore.sh            # 从备份包一键恢复数据
├── .gitignore            # 隐私与数据文件绝不进 git
├── .gitattributes        # 强制 LF 换行，跨平台克隆即可运行
└── README.md
# 运行时生成（已被 git 忽略）：commits.json 数据 / backups/ 快照 / .env 密码 / logs/
```

## 快速部署（Linux 云服务器）

**方式一：从 GitHub 克隆（推荐）**

```bash
ssh ubuntu@<服务器IP>
git clone https://github.com/<你的用户名>/study-tracker.git
cd study-tracker
chmod +x *.sh
./install.sh          # 自动生成强随机密码 + systemd 开机自启
cat .env              # 查看自动生成的密码
```

**方式二：本地上传**

```bash
# 在你自己电脑上执行
scp -r study-tracker ubuntu@<服务器IP>:~/
ssh ubuntu@<服务器IP> "cd ~/study-tracker && chmod +x *.sh && ./install.sh"
```

最后一步：云厂商控制台（腾讯云/阿里云）→ 安全组/防火墙 → 放行 TCP 8000 端口，然后浏览器打开 `http://<服务器公网IP>:8000`。

> 若已有自己的密码（比如迁移服务器），先创建 `.env` 再运行 install.sh：
> ```bash
> printf 'TRACKER_PASSWORD=你的密码\nTRACKER_PORT=8000\n' > .env
> chmod 600 .env
> ./install.sh
> ```

## 常用操作

| 操作 | 命令 |
|---|---|
| 修改管理密码（网页） | 解锁后点「🔑 修改密码」，立即生效，无需重启 |
| 修改管理密码（命令行） | `./change-password.sh '新密码'`（自动重启生效） |
| 查看运行状态 | `sudo systemctl status study-tracker` |
| 查看实时日志 | `sudo journalctl -u study-tracker -f` |
| 重启服务 | `sudo systemctl restart study-tracker` |
| 手动全量备份 | `./backup.sh`（生成 tar.gz，可 scp 下载到本地） |
| 从备份包恢复 | `./restore.sh <备份文件.tar.gz>`（自动先备份现状再恢复） |
| 改用其他端口 | 编辑 `.env` 的 `TRACKER_PORT`，重启服务，并在安全组放行新端口 |
| 无 sudo 时启动 | `./start.sh` / `./start.sh stop` / `./start.sh status` |

## 安全设计

- **读写分离**：浏览热力图完全公开；提交 / 删除 / 导入 / 导出必须携带 `X-Auth-Password` 请求头，后端真实校验（前端无法绕过）。
- **防暴力破解**：同一 IP 5 分钟内密码错误超过 8 次即拒绝（HTTP 429）。
- **常量时间比较**：`hmac.compare_digest` 防时序攻击。
- **密码不进代码**：密码只存在于服务器 `.env`（权限 600，仅本人可读），代码里只有默认占位值。网页端解锁后可直接改密码，新密码经过校验（6~128 位、禁止空白与 shell 危险字符）后原子写入 `.env`，立即生效且重启不丢。
- **防跨站伪造**：不返回任何 CORS 头，自定义鉴权头使浏览器跨站请求无法通过预检。
- **输入校验**：日期 / 时间 / 时长 / 标签 / 内容全部严格校验，请求体上限 64KB。
- **进程加固**：systemd 单元启用 `NoNewPrivileges / PrivateTmp / ProtectSystem`。
- 建议：密码尽量 ≥ 12 位；如有域名，可后续加 Nginx + Let's Encrypt 升级 HTTPS。

## 数据保存与可移植性

- **原子写入**：先写临时文件并 `fsync`，再 `os.replace` 覆盖，断电 / 崩溃不会写坏数据。
- **自动快照**：每次提交 / 删除 / 导入前，自动把当前 `commits.json` 复制到 `backups/commits-时间戳.json`，保留最近 60 份。误删可随时找回。
- **网页端导出 / 导入**：解锁后点「⬇️ 导出全部数据」下载 JSON；换服务器后用「⬆️ 导入数据」一键恢复（导入前服务器也会自动备份）。
- **本周 Markdown 总结**：右上角「📋 导出本周总结」，访客也能用。

### 迁移到新服务器（推荐：GitHub + 备份包）

项目代码在 GitHub 上，数据在你手里，两者分离，随时可重建：

```bash
# 1. 旧服务器：打包数据并下载到本地
ssh ubuntu@<旧服务器IP> "cd ~/study-tracker && ./backup.sh"
scp ubuntu@<旧服务器IP>:~/study-tracker/backups/study-tracker-data-*.tar.gz .

# 2. 新服务器：克隆项目 + 安装（密码会重新生成，也可沿用旧 .env）
ssh ubuntu@<新服务器IP>
git clone https://github.com/<你的用户名>/study-tracker.git
cd study-tracker && chmod +x *.sh && ./install.sh

# 3. 上传备份包并一键恢复
scp study-tracker-data-*.tar.gz ubuntu@<新服务器IP>:~/study-tracker/
ssh ubuntu@<新服务器IP> "cd ~/study-tracker && ./restore.sh study-tracker-data-*.tar.gz"
```

也可以不用命令行：新服务器装好后，网页解锁 →「⬆️ 导入数据」选择之前「⬇️ 导出全部数据」下载的 JSON 即可。

<details>
<summary>备选：整目录拷贝（含密码配置，一条命令迁移）</summary>

```bash
scp -r ubuntu@<旧服务器IP>:~/study-tracker ./study-tracker-old
scp -r ./study-tracker-old ubuntu@<新服务器IP>:~/study-tracker
ssh ubuntu@<新服务器IP> "cd ~/study-tracker && chmod +x *.sh && ./install.sh"
```
</details>

### GitHub 仓库与隐私边界

仓库只包含**代码与脚本**，以下内容被 `.gitignore` 永久排除，不会上传：

| 文件 | 内容 | 保存位置 |
|---|---|---|
| `.env` | 管理密码 | 仅服务器（权限 600） |
| `commits.json` | 你的学习记录 | 服务器 + 你的备份包 |
| `backups/` | 自动快照 / 手动备份 | 服务器 + 你的备份包 |
| `logs/` | 运行日志 | 仅服务器 |

建议定期（如每周）执行 `./backup.sh` 并把 tar.gz 下载到自己的电脑或网盘；数据完全由你掌握，与代码仓库解耦。

### 从自动快照恢复误删数据

```bash
cd ~/study-tracker/backups
ls -t | head                        # 找到想要的时间点
cp commits-20261116-120000.json ../commits.json
sudo systemctl restart study-tracker
```

## API 一览

| 方法 | 路径 | 鉴权 | 说明 |
|---|---|---|---|
| GET | `/` | 无 | 前端页面 |
| GET | `/api/commits` | 无 | 全部记录 |
| GET | `/healthz` | 无 | 健康检查 |
| POST | `/api/auth` | 需要 | 校验密码 |
| POST | `/api/commits` | 需要 | 新增记录 |
| DELETE | `/api/commits?id=xxx` | 需要 | 删除记录 |
| GET | `/api/export` | 需要 | 下载全量 JSON |
| POST | `/api/import` | 需要 | 覆盖导入（自动先备份） |
| POST | `/api/password` | 需要 | 修改密码（body: `{"new_password":"..."}`，立即生效并写入 .env） |

鉴权方式：请求头 `X-Auth-Password: <密码>`。失败返回 `401 {"error":"密码错误，无权操作"}`，限流返回 `429`。

## 常见问题

- **浏览器打不开页面？** 90% 是云控制台安全组没放行 8000 端口；其次检查 `sudo systemctl status study-tracker` 与服务器防火墙（`sudo ufw status`）。
- **手机打开会自动放大输入框？** 已处理：所有输入框字号 ≥ 16px。
- **换手机 / 换浏览器要重新输密码？** 是。密码只存在当前浏览器的 localStorage，这是有意设计；输一次后该浏览器永久免密。
- **忘记密码？** 服务器上 `cat ~/study-tracker/.env` 查看，或 `./change-password.sh '新密码'` 重置。
