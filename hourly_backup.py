#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
hourly_backup.py —— 每小时备份 commits.json（零依赖，仅 Python 3 标准库）

工作方式：
  * 默认把 commits.json 复制到 hourly-backup/commits-latest.json
  * 每次运行【覆盖】上一次的备份（只保留最近一份，符合"覆盖式备份"需求）
  * 如需保留历史份数，加参数 --keep N（例如 --keep 24 保留最近 24 份）

安全设计（很重要，避免"备份把好数据覆盖成坏数据"）：
  1. 写入前先校验源文件是合法 JSON 且结构正确；不合规则【拒绝覆盖】，备份保持上一次的良好副本
  2. 原子写入：先写 .tmp 再 fsync + os.replace，断电/崩溃不会留下半个文件
  3. 写后回读校验 SHA256，确保备份与源一致
  4. 文件锁（fcntl.flock）防止两次运行重叠
  5. 源文件未变化时跳过写入（no-op），避免无意义的磁盘写入

用法：
  python3 hourly_backup.py            # 覆盖式备份（每小时由定时任务调用）
  python3 hourly_backup.py --keep 24  # 额外保留 24 份带时间戳的历史副本
  python3 hourly_backup.py --status   # 查看当前备份状态

部署：由 study-tracker-backup.timer 每小时触发（见 install-backup.sh）
"""

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(BASE_DIR, "commits.json")
BACKUP_DIR = os.path.join(BASE_DIR, "hourly-backup")
LATEST_FILE = os.path.join(BACKUP_DIR, "commits-latest.json")
LOCK_FILE = os.path.join(BACKUP_DIR, ".backup.lock")
LOG_FILE = os.path.join(BASE_DIR, "logs", "hourly-backup.log")
LOG_MAX_BYTES = 512 * 1024     # 日志超过 512KB 自动轮转

# 控制台统一 UTF-8（兼容 Windows GBK 终端，避免 emoji 导致崩溃）
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

# fcntl 仅 Linux/macOS 可用；Windows 上降级为无锁（便于本地测试）
try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log(msg):
    """同时打印到 stdout（systemd journal 可见）和日志文件。"""
    line = "[%s] %s" % (now_str(), msg)
    try:
        print(line, flush=True)
    except (UnicodeEncodeError, OSError):
        pass  # 终端编码不支持 emoji 时不影响备份流程
    try:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > LOG_MAX_BYTES:
            os.replace(LOG_FILE, LOG_FILE + ".1")
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass  # 日志写失败不影响备份本身


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def load_and_validate(path):
    """读取并校验数据结构。返回 (data, 记录数)。数据损坏时抛 ValueError。"""
    if not os.path.exists(path):
        raise ValueError("数据文件不存在: %s" % path)
    with open(path, "r", encoding="utf-8-sig") as f:
        raw = f.read()
    if not raw.strip():
        raise ValueError("数据文件为空")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError("数据文件不是合法 JSON: %s" % e)
    if not isinstance(data, dict) or not isinstance(data.get("commits"), list):
        raise ValueError('数据文件结构异常（应为 {"commits": [...]}）')
    return data, len(data["commits"])


def atomic_copy(src, dest):
    """原子复制：临时文件 + fsync + os.replace。"""
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = dest + ".tmp"
    with open(src, "rb") as fin, open(tmp, "wb") as fout:
        shutil.copyfileobj(fin, fout)
        fout.flush()
        os.fsync(fout.fileno())
    os.replace(tmp, dest)


def rotate_history(keep):
    """可选的带时间戳历史副本，只保留最近 keep 份。"""
    if keep <= 0:
        return
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    dest = os.path.join(BACKUP_DIR, "commits-%s.json" % stamp)
    n = 1
    while os.path.exists(dest):
        dest = os.path.join(BACKUP_DIR, "commits-%s-%d.json" % (stamp, n))
        n += 1
    atomic_copy(LATEST_FILE, dest)
    history = sorted(f for f in os.listdir(BACKUP_DIR)
                     if f.startswith("commits-2") and f.endswith(".json"))
    for old in history[:-keep]:
        try:
            os.remove(os.path.join(BACKUP_DIR, old))
        except OSError:
            pass


def show_status():
    print("数据文件 : %s" % DATA_FILE)
    exists = os.path.exists(DATA_FILE)
    print("           %s" % ("存在, %d 字节, 修改于 %s" % (
        os.path.getsize(DATA_FILE),
        datetime.fromtimestamp(os.path.getmtime(DATA_FILE)).strftime("%Y-%m-%d %H:%M:%S")) if exists else "不存在"))
    print("备份文件 : %s" % LATEST_FILE)
    if os.path.exists(LATEST_FILE):
        print("           存在, %d 字节, 备份于 %s" % (
            os.path.getsize(LATEST_FILE),
            datetime.fromtimestamp(os.path.getmtime(LATEST_FILE)).strftime("%Y-%m-%d %H:%M:%S")))
        try:
            _, n = load_and_validate(LATEST_FILE)
            print("           备份内记录数: %d" % n)
        except ValueError as e:
            print("           备份内容异常: %s" % e)
    else:
        print("           尚不存在（首次运行后生成）")
    hist = [f for f in os.listdir(BACKUP_DIR) if f.startswith("commits-2")] if os.path.isdir(BACKUP_DIR) else []
    print("历史副本 : %d 份" % len(hist))
    return 0


def main():
    ap = argparse.ArgumentParser(description="每小时覆盖式备份 commits.json")
    ap.add_argument("--keep", type=int, default=0,
                    help="额外保留 N 份带时间戳的历史副本（默认 0 = 只保留覆盖式最新一份）")
    ap.add_argument("--status", action="store_true", help="查看备份状态后退出")
    args = ap.parse_args()

    if args.status:
        return show_status()

    os.makedirs(BACKUP_DIR, exist_ok=True)

    lock_handle = None
    if fcntl is not None:
        lock_handle = open(LOCK_FILE, "w")
        try:
            fcntl.flock(lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            log("已有备份进程在运行，本次跳过")
            lock_handle.close()
            return 0

    try:
        # 1. 校验源数据（关键：坏数据绝不覆盖好备份）
        try:
            data, count = load_and_validate(DATA_FILE)
        except ValueError as e:
            log("❌ 跳过备份：源数据不合格 -> %s（已保留上一次的良好备份）" % e)
            return 2

        src_hash = sha256_of(DATA_FILE)

        # 2. 源未变化则跳过
        if os.path.exists(LATEST_FILE) and sha256_of(LATEST_FILE) == src_hash:
            log("数据无变化，跳过（%d 条记录）" % count)
            return 0

        # 3. 原子覆盖写入
        atomic_copy(DATA_FILE, LATEST_FILE)

        # 4. 回读校验
        if sha256_of(LATEST_FILE) != src_hash:
            log("❌ 备份校验失败：写入后哈希不一致，请检查磁盘")
            return 3
        try:
            _, backup_count = load_and_validate(LATEST_FILE)
        except ValueError as e:
            log("❌ 备份文件校验失败：%s" % e)
            return 3

        # 5. 可选历史副本
        rotate_history(args.keep)

        size = os.path.getsize(LATEST_FILE)
        log("✅ 备份完成：%d 条记录, %d 字节 -> %s%s" % (
            backup_count, size, os.path.relpath(LATEST_FILE, BASE_DIR),
            ("（另保留 %d 份历史）" % args.keep) if args.keep > 0 else "（已覆盖上一份）"))
        return 0
    finally:
        if lock_handle is not None:
            try:
                fcntl.flock(lock_handle, fcntl.LOCK_UN)
            finally:
                lock_handle.close()


if __name__ == "__main__":
    sys.exit(main())
