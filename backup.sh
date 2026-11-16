#!/usr/bin/env bash
# ============================================================
# backup.sh —— 手动全量备份（打包下载后可彻底离线保存）
# 说明: 服务每次修改数据前都会自动快照到 backups/commits-*.json，
#       本脚本把 数据文件 + 全部快照 打包成一个 tar.gz，便于下载到本地。
# 用法: ./backup.sh
# 恢复: 解压后把 commits.json 放回应用目录，重启服务即可；
#       或直接在网页管理员模式用「导入数据」恢复。
# ============================================================
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p backups

STAMP="$(date +%Y%m%d-%H%M%S)"
OUT="backups/study-tracker-data-$STAMP.tar.gz"

if ls backups/commits-*.json >/dev/null 2>&1; then
  tar --exclude='backups/study-tracker-data-*' -czf "$OUT" commits.json backups
else
  tar -czf "$OUT" commits.json
fi

echo "✅ 备份完成: $(pwd)/$OUT"
echo "   下载到本地电脑（在你自己电脑上执行）:"
echo "   scp ubuntu@<服务器IP>:$(pwd)/$OUT ."
