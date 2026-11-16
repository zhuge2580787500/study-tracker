#!/usr/bin/env bash
# ============================================================
# install.sh —— 推荐部署方式：安装 systemd 服务
#   * 开机自启 + 崩溃自动重启（比 nohup 更可靠）
#   * 自动生成 .env 密码文件（权限 600，仅本人可读）
#   * 若 .env 已存在则保留其中的密码（方便迁移服务器）
# 用法: ./install.sh
# 卸载: sudo systemctl disable --now study-tracker && sudo rm /etc/systemd/system/study-tracker.service
# ============================================================
set -euo pipefail
cd "$(dirname "$0")"
APP_DIR="$(pwd)"
PORT="${TRACKER_PORT:-8000}"

echo "==> 应用目录: $APP_DIR"

# 1. 密码配置文件（已有则保留，绝不覆盖）
if [ ! -f .env ]; then
  GEN_PWD="$(python3 -c 'import secrets; print(secrets.token_urlsafe(18))')"
  printf 'TRACKER_PASSWORD=%s\nTRACKER_PORT=%s\n' "$GEN_PWD" "$PORT" > .env
  echo "==> 已生成随机管理密码，保存在 .env（权限 600）"
else
  echo "==> 检测到已有 .env，保留现有密码配置"
fi
chmod 600 .env

# 2. 初始化数据文件
if [ ! -f commits.json ]; then
  printf '{\n  "commits": []\n}\n' > commits.json
  echo "==> 已初始化 commits.json"
fi
mkdir -p backups

# 3. 写入 systemd 服务单元
sudo tee /etc/systemd/system/study-tracker.service > /dev/null <<EOF
[Unit]
Description=Study Contribution Wall (zero-dependency Python)
After=network.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$APP_DIR
EnvironmentFile=$APP_DIR/.env
ExecStart=/usr/bin/python3 $APP_DIR/server.py
Restart=always
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ReadWritePaths=$APP_DIR

[Install]
WantedBy=multi-user.target
EOF

# 4. 启动并设置开机自启
sudo systemctl daemon-reload
sudo systemctl enable --now study-tracker
sleep 1
sudo systemctl status study-tracker --no-pager | head -n 12 || true

# 5. 系统防火墙（若启用 ufw 则放行端口；云厂商安全组需在控制台另行放行）
if sudo ufw status 2>/dev/null | grep -q "Status: active"; then
  sudo ufw allow "$PORT"/tcp
  echo "==> ufw 已放行 $PORT/tcp"
fi

echo ""
echo "✅ 部署完成！"
echo "   访问地址: http://<服务器公网IP>:$PORT"
echo "   管理密码: 见 $APP_DIR/.env（修改密码: ./change-password.sh '新密码'）"
echo "   查看日志: sudo journalctl -u study-tracker -f"
echo "   ⚠️ 若是腾讯/阿里云服务器，请到控制台「安全组/防火墙」放行 TCP $PORT 端口"
