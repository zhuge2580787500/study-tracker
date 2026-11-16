#!/usr/bin/env bash
# ============================================================
# install-backup.sh —— 安装"每小时自动备份 commits.json"定时任务
#
# 使用 systemd timer（比 cron 更可靠）：
#   * 开机自动生效、无需登录
#   * Persistent=true：服务器关机/重启错过的任务，开机后补跑一次
#   * 日志进 journald，可用 journalctl 查看
#
# 用法:  ./install-backup.sh
# 卸载:  sudo systemctl disable --now study-tracker-backup.timer
#        sudo rm /etc/systemd/system/study-tracker-backup.{service,timer}
#        sudo systemctl daemon-reload
# ============================================================
set -euo pipefail
cd "$(dirname "$0")"
APP_DIR="$(pwd)"

if [ ! -f hourly_backup.py ]; then
  echo "❌ 未找到 hourly_backup.py，请先上传脚本"
  exit 1
fi

echo "==> 应用目录: $APP_DIR"

# 1. 先手动跑一次，确认脚本可用
echo "==> 试跑一次备份脚本..."
/usr/bin/python3 "$APP_DIR/hourly_backup.py" || echo "   （首次运行提示可忽略，见上方原因）"

# 2. 备份服务单元（oneshot：跑完即退出）
sudo tee /etc/systemd/system/study-tracker-backup.service > /dev/null <<EOF
[Unit]
Description=Study Tracker hourly data backup (commits.json)
After=network.target

[Service]
Type=oneshot
User=$USER
WorkingDirectory=$APP_DIR
ExecStart=/usr/bin/python3 $APP_DIR/hourly_backup.py
Nice=10
IOSchedulingClass=idle
EOF

# 3. 定时器：每小时整点触发，错过的开机补跑
sudo tee /etc/systemd/system/study-tracker-backup.timer > /dev/null <<EOF
[Unit]
Description=Run Study Tracker backup every hour

[Timer]
OnCalendar=hourly
Persistent=true
RandomizedDelaySec=30

[Install]
WantedBy=timers.target
EOF

# 4. 启用
sudo systemctl daemon-reload
sudo systemctl enable --now study-tracker-backup.timer

echo ""
echo "✅ 每小时自动备份已启用"
sudo systemctl list-timers study-tracker-backup.timer --no-pager || true
echo ""
echo "备份文件: $APP_DIR/hourly-backup/commits-latest.json（每次覆盖上一份）"
echo "运行日志: journalctl -u study-tracker-backup.service -n 20"
echo "日志文件: $APP_DIR/logs/hourly-backup.log"
echo "立即备份: sudo systemctl start study-tracker-backup.service"
