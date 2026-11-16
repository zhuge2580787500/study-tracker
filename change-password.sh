#!/usr/bin/env bash
# ============================================================
# change-password.sh —— 修改网站管理密码（随时可改）
# 用法: ./change-password.sh '新密码'
#       ./change-password.sh          （交互式隐藏输入）
# 改完自动重启服务生效；浏览器端旧密码会自动失效，需重新解锁一次。
# ============================================================
set -euo pipefail
cd "$(dirname "$0")"

NEW="${1:-}"
if [ -z "$NEW" ]; then
  read -r -s -p "请输入新密码: " NEW
  echo ""
fi
if [ -z "$NEW" ]; then
  echo "❌ 密码不能为空"
  exit 1
fi

# 用 Python 安全改写 .env（避免 sed 特殊字符问题）
python3 - "$NEW" <<'PY'
import sys, pathlib
new = sys.argv[1]
p = pathlib.Path('.env')
lines = p.read_text(encoding='utf-8').splitlines() if p.exists() else []
out, found = [], False
for ln in lines:
    if ln.startswith('TRACKER_PASSWORD='):
        out.append('TRACKER_PASSWORD=' + new)
        found = True
    else:
        out.append(ln)
if not found:
    out.append('TRACKER_PASSWORD=' + new)
p.write_text('\n'.join(out) + '\n', encoding='utf-8')
PY
chmod 600 .env

# 重启服务使新密码生效
if [ -f /etc/systemd/system/study-tracker.service ]; then
  sudo systemctl restart study-tracker
  echo "✅ 密码已修改，systemd 服务已重启"
else
  ./start.sh stop >/dev/null 2>&1 || true
  ./start.sh
  echo "✅ 密码已修改，服务已重启（nohup 模式）"
fi
