import os
import hashlib
import io
import zipfile
import json
import urllib.parse
import threading
import concurrent.futures
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import unquote, quote, urlparse, parse_qs
import sys
import time
import shutil

# -------------------- 配置檔與資料夾 --------------------
CONFIG_FILE = "config.txt"
CLIENT_UPDATE_DIR = os.path.join(os.getcwd(), "clientupdate")
CLIENT_VERSION_FILE = os.path.join(CLIENT_UPDATE_DIR, "version.txt")

CACHE_DIR = os.path.join(os.getcwd(), "cache_zip")
HASH_RECORD_FILE = os.path.join(CACHE_DIR, "hash_record.json")
os.makedirs(CACHE_DIR, exist_ok=True)

folders = {}          # key -> folder_path
cache_files = {}      # key -> zip_path

# -------------------- 忽略規則 --------------------
IGNORE_PREFIXES = ["serveronly_"]
IGNORE_NAMES = ["ignore_me.txt"]  # 可自行擴展

# -------------------- 載入 config.txt --------------------
with open(CONFIG_FILE, "r", encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if not line or ":" not in line:
            continue
        key, path = line.split(":", 1)
        folders[key.strip()] = path.strip().strip('"')

# -------------------- 工具函數 --------------------
def should_ignore(file_name):
    """判斷檔案是否應該被忽略"""
    return any(file_name.startswith(p) for p in IGNORE_PREFIXES) or file_name in IGNORE_NAMES

def get_md5(file_path):
    """取得檔案 MD5"""
    hash_md5 = hashlib.md5()
    try:
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                hash_md5.update(chunk)
        return hash_md5.hexdigest()
    except Exception:
        return "error"

def scan_folder_dict(folder_path):
    """遞迴掃描資料夾，返回 {相對路徑: md5}"""
    result = {}
    for root, dirs, files in os.walk(folder_path):
        for file in files:
            if should_ignore(file):
                continue
            abs_path = os.path.join(root, file)
            rel_path = os.path.relpath(abs_path, folder_path)
            result[rel_path] = get_md5(abs_path)
    return result

def zip_folder(folder_path, zip_path):
    """壓縮資料夾（忽略指定檔案），顯示進度"""
    files_to_zip = [
        (os.path.join(root, file), os.path.relpath(os.path.join(root, file), folder_path))
        for root, dirs, files in os.walk(folder_path)
        for file in files if not should_ignore(file)
    ]
    total_files = len(files_to_zip)
    done = 0
    start_time = time.time()

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
        for abs_path, rel_path in files_to_zip:
            zipf.write(abs_path, rel_path)
            done += 1
            if done % max(1, total_files // 50) == 0 or done == total_files:
                percent = (done / total_files) * 100
                elapsed = time.time() - start_time
                sys.stdout.write(f"\r[{os.path.basename(zip_path)}] 快取進度: {done}/{total_files} ({percent:.1f}%) 用時: {elapsed:.1f}s")
                sys.stdout.flush()
    sys.stdout.write("\n")

# -------------------- 快取管理 --------------------
def load_old_hash():
    if os.path.exists(HASH_RECORD_FILE):
        try:
            with open(HASH_RECORD_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_hash_record(record):
    with open(HASH_RECORD_FILE, "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=2)

def create_zip_cache():
    """比對檔案變動，僅在有變動時重建快取"""
    print("🗜️ 正在檢查 ZIP 快取...")
    old_hash = load_old_hash()
    new_hash = {}
    changed_keys = []

    # 計算每個資料夾的 hash
    for key, folder_path in folders.items():
        new_hash[key] = scan_folder_dict(folder_path)
        if key not in old_hash or new_hash[key] != old_hash[key]:
            changed_keys.append(key)

    if not changed_keys:
        print("✅ 所有資料夾與快照相同，使用現有快取。")
    else:
        print(f"♻️ 偵測到變動的資料夾: {', '.join(changed_keys)}")
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(4, len(changed_keys))) as executor:
            futures = {executor.submit(zip_folder, folders[key], os.path.join(CACHE_DIR, f"{key}.zip")): key for key in changed_keys}
            for future in concurrent.futures.as_completed(futures):
                k = futures[future]
                try:
                    future.result()
                    print(f"[快取更新完成] {k}")
                except Exception as e:
                    print(f"[快取失敗] {k}: {e}")

    # 更新 cache_files 映射
    for key in folders:
        cache_files[key] = os.path.join(CACHE_DIR, f"{key}.zip")

    save_hash_record(new_hash)
    print("📦 快取初始化完成！")

# -------------------- HTTP 處理 --------------------
class FileBrowserHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        url_parts = urlparse(self.path)
        path = unquote(url_parts.path)
        query = parse_qs(url_parts.query)
        download_mode = "download" in query
        json_mode = "json" in query

        # ------------------- clientupdate -------------------
        if path.startswith("/clientupdate"):
            parts = path[len("/clientupdate/"):].lstrip("/")  # 取得檔名部分
            if not parts:
                # 只有 /clientupdate → 顯示 HTML
                self.send_clientupdate_page()
                return
            else:
                # 有檔名 → 下載
                file_name = parts
                update_file_path = os.path.join(CLIENT_UPDATE_DIR, file_name)
                if os.path.exists(update_file_path):
                    self._send_file(update_file_path, download=True)
                    return
                else:
                    self.send_error(404, f"{file_name} not found")
                    return

        # ------------------- config_names -------------------
        if path.lstrip("/") == "config_names" and json_mode:
            self._send_json(list(folders.keys()))
            return

        # ------------------- 其他資料夾 / 檔案 -------------------
        parts = path.lstrip("/").split("/", 1)
        if parts[0] in folders:
            folder_base = folders[parts[0]]
            sub_path = parts[1] if len(parts) > 1 else ""
            real_path = os.path.join(folder_base, sub_path.replace("/", os.sep))

            if should_ignore(os.path.basename(real_path)):
                self.send_error(404, "File is ignored")
                return

            if os.path.isfile(real_path):
                self._send_file(real_path, True)
                return
            elif os.path.isdir(real_path):
                if download_mode:
                    zip_path = cache_files.get(parts[0])
                    if zip_path and os.path.exists(zip_path):
                        self._send_file(zip_path, True)
                        return
                elif json_mode:
                    # 嘗試從快取讀取 MD5
                    try:
                        with open(HASH_RECORD_FILE, "r", encoding="utf-8") as f:
                            hash_record = json.load(f)
                        files_md5 = hash_record.get(parts[0], scan_folder_dict(real_path))
                    except Exception:
                        files_md5 = scan_folder_dict(real_path)
                    self._send_json(files_md5)
                    return
                else:
                    self.send_folder_listing(parts[0], real_path, sub_path)
                    return

        # ------------------- 首頁 -------------------
        self.send_homepage()

    # ------------------- 封裝方法 -------------------
    def send_clientupdate_page(self):
        latest_version = "未知"
        note = ""
        file_to_download = "client.zip"
        if os.path.exists(CLIENT_VERSION_FILE):
            try:
                with open(CLIENT_VERSION_FILE, "r", encoding="utf-8") as f:
                    version_info = json.load(f)
                    latest_version = version_info.get("version", "未知")
                    file_to_download = version_info.get("file", "client.zip")
                    note = version_info.get("note", "")
            except Exception as e:
                print(f"解析 version.txt 失敗: {e}")
        html = f"""
        <h2>最新版本: {latest_version}</h2>
        <pre>{note}</pre>
        <div style="margin-top:20px;">
            <a href='/clientupdate/{file_to_download}?download=1'>
                <button style="background:#7c4ed8;color:white;border:none;padding:10px 20px;border-radius:8px;cursor:pointer;">下載更新</button>
            </a>
            <a href='http://modpack.barian.moe' target="_blank" style="margin-left:10px;">
                <button style="background:#4caf50;color:white;border:none;padding:10px 20px;border-radius:8px;cursor:pointer;">前往 Modpack</button>
            </a>
        </div>
        """
        self._send_html(html)

    def send_folder_listing(self, key, folder_path, sub_path):
        html = f"<h2 style='color:#4a2a8a;'>📁 Folder: /{key}"
        if sub_path:
            html += f"/{sub_path}"
        html += "</h2><ul>"
        for entry in os.listdir(folder_path):
            if should_ignore(entry):
                continue
            entry_path = os.path.join(folder_path, entry)
            entry_url = '/' + key + '/' + quote(sub_path + '/' + entry if sub_path else entry)
            icon = "📂" if os.path.isdir(entry_path) else "📄"
            html += f"<li>{icon} <a href='{entry_url}{'?download=1' if os.path.isfile(entry_path) else ''}'>{entry}</a></li>"
        html += "</ul><hr><a href='/'>返回首頁</a> | "
        html += f"<a href='/{key}?download=1'>下載整包 ZIP</a>"
        self._send_html(html)

    def send_homepage(self):
        html = "<h2 style='color:#4a2a8a;'>📦 資料夾清單</h2><ul>"
        for key in folders:
            html += f"<li>[DIR] <a href='/{key}'>{key}</a> " \
                    f"<a href='/{key}?download=1' style='margin-left:10px;'>📥 下載整包</a></li>"
        html += "</ul>"
        self._send_html(html)

    def _send_html(self, html):
        full_html = f"<html><head><meta charset='utf-8'><title>Server</title></head>" \
                    f"<body style='background-color:#f3ecfc;color:#2b1d40;font-family:sans-serif;padding:20px;'>{html}</body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(full_html.encode("utf-8"))

    def _send_json(self, data):
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"))

    def _send_file(self, file_path, download=False):
        if not os.path.exists(file_path):
            self.send_error(404, f"{file_path} not found")
            return
        file_name = os.path.basename(file_path)
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        if download:
            encoded = urllib.parse.quote(file_name)
            self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{encoded}")
        self.send_header("Content-Length", str(os.path.getsize(file_path)))
        self.end_headers()
        try:
            with open(file_path, "rb") as f:
                shutil.copyfileobj(f, self.wfile, 8192)
        except ConnectionResetError:
            print(f"[警告] 客戶端中斷下載: {file_name}")

# -------------------- 啟動 HTTP 服務 --------------------
if __name__ == "__main__":
    print("\n🚀 伺服器啟動中: http://localhost:8000")
    create_zip_cache()
    server_address = ("", 8000)
    httpd = HTTPServer(server_address, FileBrowserHandler)
    httpd.serve_forever()
