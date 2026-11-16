#!/usr/bin/env bash
# ============================================================
# restore.sh —— 从备份包一键恢复数据
# 用法: ./restore.sh <备份文件.tar.gz>
#   备份文件由 ./backup.sh 生成（含 commits.json + backups/ 快照）
#   恢复前会先自动备份当前数据，可随时回退
# ============================================================
set -euo pipefail
cd "$(dirname "$0")"

FILE="${1:-}"
if [ -z "$FILE" ] || [ ! -f "$FILE" ]; then
  echo "用法: $0 <备份文件.tar.gz>"
  echo "示例: $0 backups/study-tracker-data-20261116-120000.tar.gz"
  exit 1
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
tar -xzf "$FILE" -C "$TMP"

if [ ! -f "$TMP/commits.json" ]; then
  echo "❌ 备份包中缺少 commits.json，放弃恢复"
  exit 1
fi

# 校验 JSON 合法性与结构
python3 -c "import json,sys; d=json.load(open(sys.argv[1],encoding='utf-8-sig')); assert isinstance(d.get('commits'),list)" "$TMP/commits.json"

# 恢复前先备份当前数据
if [ -f commits.json ]; then
  echo "==> 先备份当前数据..."
  ./backup.sh
fi

cp "$TMP/commits.json" commits.json
if [ -d "$TMP/backups" ]; then
  mkdir -p backups
  cp -n "$TMP/backups/"commits-*.json backups/ 2>/dev/null || true
fi

# 重启服务生效
if [ -f /etc/systemd/system/study-tracker.service ]; then
  sudo systemctl restart study-tracker
  echo "✅ 恢复完成，systemd 服务已重启"
else
  ./start.sh stop >/dev/null 2>&1 || true
  ./start.sh
  echo "✅ 恢复完成，服务已重启（nohup 模式）"
fi
echo "   打开网页即可看到恢复后的数据"
