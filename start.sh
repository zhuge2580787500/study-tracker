#!/usr/bin/env bash
# ============================================================
# start.sh —— nohup 一键后台启动（简单场景 / 无 sudo 权限时用）
# 推荐生产环境使用 ./install.sh 安装 systemd 服务（开机自启 + 崩溃自动重启）
# 用法: ./start.sh [start|stop|status]
# ============================================================
set -e
cd "$(dirname "$0")"

# 加载 .env 中的密码等配置（若存在）
if [ -f .env ]; then
  set -a
  . ./.env
  set +a
fi

ACTION="${1:-start}"
PID_FILE="tracker.pid"
mkdir -p logs

is_running() {
  [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null
}

case "$ACTION" in
  start)
    if is_running; then
      echo "已在运行中（PID $(cat "$PID_FILE")），无需重复启动"
      exit 0
    fi
    nohup python3 server.py >> logs/server.log 2>&1 &
    echo $! > "$PID_FILE"
    sleep 1
    if is_running; then
      echo "✅ 已启动：PID $(cat "$PID_FILE")，日志: logs/server.log"
      echo "   访问: http://$(hostname -I 2>/dev/null | awk '{print $1}'):${TRACKER_PORT:-8000}"
    else
      echo "❌ 启动失败，请查看 logs/server.log"
      exit 1
    fi
    ;;
  stop)
    if is_running; then
      kill "$(cat "$PID_FILE")"
      rm -f "$PID_FILE"
      echo "已停止"
    else
      echo "未在运行"
      rm -f "$PID_FILE"
    fi
    ;;
  status)
    if is_running; then
      echo "运行中（PID $(cat "$PID_FILE")）"
    else
      echo "未运行"
    fi
    ;;
  *)
    echo "用法: $0 [start|stop|status]"
    exit 1
    ;;
esac
