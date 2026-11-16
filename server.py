#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Study Contribution Wall —— 零依赖后端服务（仅 Python 3 标准库）

特性：
  * 公开只读展示（GET / 与 GET /api/commits 无需密码）
  * 写入 / 删除 / 导入 / 导出 / 鉴权接口强制校验 X-Auth-Password 请求头
  * threading.Lock 保证并发读写安全；临时文件 + os.replace 原子落盘，断电不丢数据
  * 每次修改前自动快照到 backups/（保留最近 60 份），误删可随时恢复
  * 鉴权失败按 IP 限流（5 分钟内最多 8 次失败），防暴力破解
  * hmac.compare_digest 常量时间比较密码，防时序攻击
  * 请求体大小限制 + 严格字段校验，防恶意载荷

密码配置：
  优先读取环境变量 TRACKER_PASSWORD。
  ⚠️ 公网部署请务必修改默认密码！推荐用 install.sh 生成的 .env 文件管理，
  修改密码执行 ./change-password.sh '新密码' 即可。
"""

import hmac
import json
import os
import re
import secrets
import shutil
import signal
import sys
import threading
import time
from datetime import datetime, date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

# 控制台输出统一 UTF-8（兼容 Windows GBK 终端，避免 emoji 导致崩溃）
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

# ============================ 配置区 ============================

# ⚠️⚠️ 默认密码仅供本地试用，公网部署前必须修改！⚠️⚠️
# 推荐方式：在同目录 .env 文件中写 TRACKER_PASSWORD=你的密码（install.sh 会自动创建）
ADMIN_PASSWORD = os.environ.get("TRACKER_PASSWORD", "change-me")

HOST = os.environ.get("TRACKER_HOST", "0.0.0.0")
PORT = int(os.environ.get("TRACKER_PORT", "8000"))

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(BASE_DIR, "commits.json")
INDEX_FILE = os.path.join(BASE_DIR, "index.html")
BACKUP_DIR = os.path.join(BASE_DIR, "backups")
ENV_FILE = os.path.join(BASE_DIR, ".env")

MAX_BODY_BYTES = 64 * 1024      # 请求体上限 64KB
MAX_BACKUPS = 60                # 自动快照保留份数
MAX_RECORDS = 100000            # 记录总数上限（防滥用）
CONTENT_MAX_LEN = 2000          # 单条内容最大字符数
TAG_MAX_LEN = 30                # 标签最大字符数
DURATION_MAX = 1440             # 单次专注时长上限（分钟）

AUTH_WINDOW_SEC = 300           # 鉴权限流窗口：5 分钟
AUTH_MAX_FAILURES = 8           # 窗口内同一 IP 最多失败次数

# ============================ 数据层 ============================

_data_lock = threading.Lock()   # commits.json 读写全局锁
_auth_lock = threading.Lock()   # 限流计数器锁
_config_lock = threading.Lock() # 密码修改 / .env 写入锁
_auth_failures = {}             # ip -> [失败时间戳, ...]

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")

# 密码中禁止的字符：空白 + shell/systemd 环境文件中的危险字符
# （.env 会被 start.sh 以 shell 方式加载，必须杜绝命令注入）
PWD_FORBIDDEN = set(' \t\r\n`\\$&;|<>()"\'')


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


def _load_unlocked():
    """读取数据文件（调用方必须已持有 _data_lock）。"""
    if not os.path.exists(DATA_FILE):
        return {"commits": []}
    # utf-8-sig 兼容 Windows 记事本等工具写入的 BOM 头
    with open(DATA_FILE, "r", encoding="utf-8-sig") as f:
        data = json.load(f)
    if not isinstance(data, dict) or not isinstance(data.get("commits"), list):
        raise ApiError(500, "数据文件格式损坏，请从 backups/ 恢复")
    return data


def _save_unlocked(data):
    """原子写入：先写临时文件并 fsync，再 os.replace 覆盖（调用方必须已持锁）。"""
    tmp = DATA_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, DATA_FILE)


def _backup_unlocked():
    """修改前快照当前数据文件，并清理过期备份（调用方必须已持锁）。"""
    if not os.path.exists(DATA_FILE):
        return
    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = os.path.join(BACKUP_DIR, "commits-%s.json" % stamp)
    n = 1
    while os.path.exists(dest):
        dest = os.path.join(BACKUP_DIR, "commits-%s-%d.json" % (stamp, n))
        n += 1
    shutil.copy2(DATA_FILE, dest)
    # 只清理自动快照（commits-*.json），不动手动打包的 tar.gz
    snaps = sorted(
        f for f in os.listdir(BACKUP_DIR)
        if f.startswith("commits-") and f.endswith(".json")
    )
    for old in snaps[:-MAX_BACKUPS]:
        try:
            os.remove(os.path.join(BACKUP_DIR, old))
        except OSError:
            pass


def _new_id():
    """毫秒时间戳 + 随机后缀，避免同毫秒碰撞。"""
    return str(int(time.time() * 1000)) + secrets.token_hex(2)


def _validate_record(raw):
    """校验并规范化一条记录，返回可入库的 dict。"""
    if not isinstance(raw, dict):
        raise ApiError(400, "记录必须是 JSON 对象")

    d = str(raw.get("date", "")).strip()
    if not DATE_RE.match(d):
        raise ApiError(400, "date 格式必须为 YYYY-MM-DD")
    try:
        parsed = datetime.strptime(d, "%Y-%m-%d").date()
    except ValueError:
        raise ApiError(400, "date 不是合法日期")
    if parsed > date.today():
        raise ApiError(400, "不能提交未来日期")

    t = str(raw.get("time", "")).strip()
    if not t:
        t = datetime.now().strftime("%H:%M")
    if not TIME_RE.match(t):
        raise ApiError(400, "time 格式必须为 HH:MM（24 小时制）")

    try:
        duration = int(raw.get("duration"))
    except (TypeError, ValueError):
        raise ApiError(400, "duration 必须是整数分钟数")
    if not 1 <= duration <= DURATION_MAX:
        raise ApiError(400, "duration 必须在 1~%d 分钟之间" % DURATION_MAX)

    tag = str(raw.get("tag", "")).strip()
    if not tag:
        raise ApiError(400, "tag 不能为空")
    if len(tag) > TAG_MAX_LEN:
        raise ApiError(400, "tag 不能超过 %d 个字符" % TAG_MAX_LEN)

    content = str(raw.get("content", "")).strip()
    if not content:
        raise ApiError(400, "content 不能为空")
    if len(content) > CONTENT_MAX_LEN:
        raise ApiError(400, "content 不能超过 %d 个字符" % CONTENT_MAX_LEN)

    rid = str(raw.get("id", "")).strip()
    if not rid or len(rid) > 40:
        rid = _new_id()

    return {"id": rid, "date": d, "time": t, "duration": duration,
            "tag": tag, "content": content}


# ============================ 鉴权限流 ============================

def _rate_limited(ip):
    now = time.time()
    with _auth_lock:
        hits = [t for t in _auth_failures.get(ip, []) if now - t < AUTH_WINDOW_SEC]
        if hits:
            _auth_failures[ip] = hits
        else:
            _auth_failures.pop(ip, None)
        if len(_auth_failures) > 10000:
            _auth_failures.clear()
        return len(hits) >= AUTH_MAX_FAILURES


def _record_failure(ip):
    with _auth_lock:
        _auth_failures.setdefault(ip, []).append(time.time())


# ============================ 密码修改 ============================

def _validate_new_password(raw):
    if not isinstance(raw, str):
        raise ApiError(400, "new_password 必须是字符串")
    if len(raw) < 6 or len(raw) > 128:
        raise ApiError(400, "新密码长度需为 6~128 位")
    for ch in raw:
        if not ch.isprintable() or ch in PWD_FORBIDDEN:
            raise ApiError(400, "新密码不能包含空格或 $ ` \\ \" ' & ; | < > ( ) 等特殊字符")
    return raw


def _persist_password(new_pwd):
    """把新密码原子写入 .env（保留 TRACKER_PORT 等其他配置，权限 600）。"""
    lines = []
    if os.path.exists(ENV_FILE):
        with open(ENV_FILE, "r", encoding="utf-8-sig") as f:
            lines = f.read().splitlines()
    out, found = [], False
    for ln in lines:
        if ln.startswith("TRACKER_PASSWORD="):
            out.append("TRACKER_PASSWORD=" + new_pwd)
            found = True
        else:
            out.append(ln)
    if not found:
        out.append("TRACKER_PASSWORD=" + new_pwd)
    tmp = ENV_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
        f.flush()
        os.fsync(f.fileno())
    os.chmod(tmp, 0o600)
    os.replace(tmp, ENV_FILE)


# ============================ HTTP 处理 ============================

class Handler(BaseHTTPRequestHandler):
    server_version = "StudyTracker"
    sys_version = ""
    protocol_version = "HTTP/1.1"
    timeout = 30  # 防慢速连接占满线程

    # ---------- 基础工具 ----------

    def _send(self, status, body, ctype="application/json; charset=utf-8", extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        if extra:
            for k, v in extra.items():
                self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status, obj, extra=None):
        self._send(status, json.dumps(obj, ensure_ascii=False), extra=extra)

    def _read_body(self):
        raw = getattr(self, "_body", b"")
        if not raw:
            raise ApiError(400, "请求体为空")
        try:
            return json.loads(raw.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ApiError(400, "请求体不是合法 JSON")

    def _drain_body(self):
        """无论鉴权成败都先读完请求体，避免 keep-alive 连接被残留字节污染。"""
        self._body = b""
        if self.command not in ("POST", "PUT", "DELETE", "PATCH"):
            return
        if self.headers.get("Transfer-Encoding"):
            self.close_connection = True
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.close_connection = True
            return
        if length <= 0:
            return
        if length > MAX_BODY_BYTES:
            self.close_connection = True
            raise ApiError(413, "请求体过大（上限 64KB）")
        self._body = self.rfile.read(length)

    def _check_auth(self):
        ip = self.client_address[0]
        if _rate_limited(ip):
            self._json(429, {"error": "密码尝试过于频繁，请 5 分钟后再试"})
            return False
        supplied = self.headers.get("X-Auth-Password", "")
        ok = hmac.compare_digest(supplied.encode("utf-8"),
                                 ADMIN_PASSWORD.encode("utf-8"))
        if not ok:
            _record_failure(ip)
            print("[AUTH] 密码错误 ip=%s path=%s" % (ip, self.path), flush=True)
            self._json(401, {"error": "密码错误，无权操作"})
            return False
        return True

    # ---------- 路由 ----------

    def _route(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        method = self.command

        if method in ("GET", "HEAD"):
            if path in ("/", "/index.html"):
                return self._serve_index()
            if path == "/api/commits":
                return self._get_commits()
            if path == "/api/export":
                return self._export()
            if path == "/healthz":
                return self._json(200, {"ok": True})
            return self._json(404, {"error": "页面不存在"})

        if method == "POST":
            if path == "/api/auth":
                if self._check_auth():
                    return self._json(200, {"ok": True})
                return
            if path == "/api/commits":
                return self._post_commit()
            if path == "/api/import":
                return self._import()
            if path == "/api/password":
                return self._change_password()
            return self._json(404, {"error": "接口不存在"})

        if method == "DELETE":
            if path == "/api/commits":
                return self._delete_commit(parse_qs(parsed.query))
            return self._json(404, {"error": "接口不存在"})

        # OPTIONS 等一律拒绝，且不返回任何 CORS 头（天然阻断跨站伪造请求）
        return self._json(405, {"error": "不支持的请求方法"})

    def do_GET(self):
        self._safe_route()

    def do_HEAD(self):
        self._safe_route()

    def do_POST(self):
        self._safe_route()

    def do_DELETE(self):
        self._safe_route()

    def do_OPTIONS(self):
        self._safe_route()

    def do_PUT(self):
        self._safe_route()

    def _safe_route(self):
        try:
            self._drain_body()
            self._route()
        except ApiError as e:
            try:
                self._json(e.status, {"error": e.message})
            except (BrokenPipeError, ConnectionResetError):
                pass
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:  # 兜底：不向客户端泄露堆栈
            print("[ERROR] %s %s -> %r" % (self.command, self.path, e), flush=True)
            try:
                self._json(500, {"error": "服务器内部错误"})
            except (BrokenPipeError, ConnectionResetError):
                pass

    # ---------- 具体接口 ----------

    def _serve_index(self):
        try:
            with open(INDEX_FILE, "r", encoding="utf-8") as f:
                html = f.read()
        except OSError:
            return self._json(500, {"error": "index.html 缺失"})
        self._send(200, html, ctype="text/html; charset=utf-8",
                   extra={"Content-Security-Policy":
                          "default-src 'self'; script-src 'unsafe-inline'; "
                          "style-src 'unsafe-inline'; img-src 'self' data:; "
                          "connect-src 'self'"})

    def _get_commits(self):
        with _data_lock:
            data = _load_unlocked()
        self._json(200, data)

    def _post_commit(self):
        if not self._check_auth():
            return
        raw = self._read_body()
        record = _validate_record(raw)
        with _data_lock:
            data = _load_unlocked()
            if len(data["commits"]) >= MAX_RECORDS:
                raise ApiError(400, "记录数已达上限")
            if any(c.get("id") == record["id"] for c in data["commits"]):
                record["id"] = _new_id()
            _backup_unlocked()
            data["commits"].append(record)
            _save_unlocked(data)
        self._json(200, {"ok": True, "commit": record})

    def _delete_commit(self, query):
        if not self._check_auth():
            return
        rid = (query.get("id") or [""])[0].strip()
        if not rid:
            raise ApiError(400, "缺少 id 参数")
        with _data_lock:
            data = _load_unlocked()
            remaining = [c for c in data["commits"] if c.get("id") != rid]
            if len(remaining) == len(data["commits"]):
                raise ApiError(404, "未找到该记录")
            _backup_unlocked()
            data["commits"] = remaining
            _save_unlocked(data)
        self._json(200, {"ok": True})

    def _export(self):
        if not self._check_auth():
            return
        with _data_lock:
            data = _load_unlocked()
        body = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        fname = "study-commits-%s.json" % datetime.now().strftime("%Y%m%d")
        self._send(200, body, extra={
            "Content-Disposition": 'attachment; filename="%s"' % fname})

    def _import(self):
        if not self._check_auth():
            return
        payload = self._read_body()
        if isinstance(payload, dict):
            records = payload.get("commits")
        else:
            records = payload
        if not isinstance(records, list):
            raise ApiError(400, "导入格式应为 {\"commits\": [...]}")
        if len(records) > MAX_RECORDS:
            raise ApiError(400, "记录数超过上限")
        validated = [_validate_record(r) for r in records]
        seen, deduped = set(), []
        for r in validated:
            if r["id"] in seen:
                r["id"] = _new_id()
            seen.add(r["id"])
            deduped.append(r)
        with _data_lock:
            _backup_unlocked()  # 覆盖前先备份旧数据
            _save_unlocked({"commits": deduped})
        self._json(200, {"ok": True, "count": len(deduped)})

    def _change_password(self):
        global ADMIN_PASSWORD
        if not self._check_auth():
            return
        payload = self._read_body()
        if not isinstance(payload, dict):
            raise ApiError(400, "请求体应为 {\"new_password\": \"...\"}")
        new_pwd = _validate_new_password(payload.get("new_password", ""))
        with _config_lock:
            if new_pwd == ADMIN_PASSWORD:
                return self._json(200, {"ok": True, "unchanged": True})
            _persist_password(new_pwd)   # 先持久化，失败则内存密码不变
            ADMIN_PASSWORD = new_pwd     # 立即生效，无需重启
        print("[ADMIN] 管理密码已修改 ip=%s" % self.client_address[0], flush=True)
        self._json(200, {"ok": True})

    def log_message(self, fmt, *args):
        sys.stdout.write("[%s] %s %s\n" % (
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            self.client_address[0], fmt % args))
        sys.stdout.flush()


# ============================ 启动 ============================

def main():
    if ADMIN_PASSWORD == "change-me":
        print("=" * 60, flush=True)
        print("⚠️  警告：正在使用默认密码 change-me！", flush=True)
        print("⚠️  公网部署请设置环境变量 TRACKER_PASSWORD 或运行 install.sh", flush=True)
        print("=" * 60, flush=True)
    elif len(ADMIN_PASSWORD) < 8:
        print("⚠️  提示：当前密码较短，公网环境建议使用更长的密码", flush=True)

    os.makedirs(BACKUP_DIR, exist_ok=True)
    with _data_lock:
        if not os.path.exists(DATA_FILE):
            _save_unlocked({"commits": []})
            print("已初始化数据文件: %s" % DATA_FILE, flush=True)

    server = ThreadingHTTPServer((HOST, PORT), Handler)
    server.daemon_threads = True

    def _shutdown(signum, frame):
        print("收到信号 %s，正在退出..." % signum, flush=True)
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    print("🚀 Study Contribution Wall 已启动", flush=True)
    print("   监听: http://%s:%d" % (HOST, PORT), flush=True)
    print("   数据: %s" % DATA_FILE, flush=True)
    print("   备份: %s（每次修改前自动快照，保留 %d 份）" % (BACKUP_DIR, MAX_BACKUPS), flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        print("服务已停止", flush=True)


if __name__ == "__main__":
    main()
