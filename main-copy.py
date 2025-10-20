import os
import hashlib
import io
import zipfile
import json
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import unquote, quote, urlparse, parse_qs

# 讀取 config.txt
config_file = "config.txt"
folders = {}

with open(config_file, "r", encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if not line or ":" not in line:
            continue
        key, path = line.split(":", 1)
        folders[key.strip()] = path.strip().strip('"')


def get_md5(file_path):
    """計算檔案 MD5 校驗碼"""
    hash_md5 = hashlib.md5()
    try:
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_md5.update(chunk)
        return hash_md5.hexdigest()
    except:
        return "error"


def scan_folder_dict(folder_path):
    """遞歸掃描資料夾，返回嵌套 JSON 結構"""
    result = {}
    try:
        for entry in os.listdir(folder_path):
            full_path = os.path.join(folder_path, entry)
            if os.path.isfile(full_path):
                result[entry] = get_md5(full_path)
            elif os.path.isdir(full_path):
                result[entry] = scan_folder_dict(full_path)
    except Exception as e:
        print(f"掃描資料夾失敗: {folder_path}, {e}")
    return result


def list_dir_html(folder_path, base_url):
    """生成 HTML 列表"""
    if not os.path.exists(folder_path):
        return f"<p>Folder '{folder_path}' not found</p>"

    entries = sorted(os.listdir(folder_path))
    html = "<ul>"
    for entry in entries:
        full_path = os.path.join(folder_path, entry)
        entry_url = base_url + "/" + quote(entry)
        if os.path.isdir(full_path):
            html += f"<li>[DIR] <a href='{entry_url}'>{entry}</a> " \
                    f"<a href='{entry_url}?download=1' style='margin-left:10px;'>下載</a></li>"
        else:
            md5 = get_md5(full_path)
            html += f"<li>{entry} [{md5}] " \
                    f"<a href='{entry_url}?download=1'>下載</a></li>"
    html += "</ul>"
    return html


def zip_folder(folder_path):
    """將資料夾打包成 zip，返回 BytesIO"""
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zipf:
        for root, dirs, files in os.walk(folder_path):
            for file in files:
                abs_path = os.path.join(root, file)
                rel_path = os.path.relpath(abs_path, folder_path)
                zipf.write(abs_path, rel_path)
    zip_buffer.seek(0)
    return zip_buffer


class FileBrowserHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        url_parts = urlparse(self.path)
        path = unquote(url_parts.path)
        query = parse_qs(url_parts.query)
        download_mode = "download" in query
        json_mode = "json" in query

        # 返回設定檔名稱列表
        if path.lstrip("/") == "config_names" and json_mode:
            config_keys = list(folders.keys())
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.end_headers()
            self.wfile.write(json.dumps(config_keys, ensure_ascii=False, indent=2).encode('utf-8'))
            return

        parts = path.lstrip("/").split("/", 1)
        if parts[0] in folders:
            folder_base = folders[parts[0]]
            sub_path = parts[1] if len(parts) > 1 else ""
            real_path = os.path.join(folder_base, sub_path.replace("/", os.sep))

            # 單檔案
            if os.path.isfile(real_path):
                if download_mode:
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/octet-stream')

                    file_name = os.path.basename(real_path)
                    file_name_encoded = urllib.parse.quote(file_name)
                    self.send_header('Content-Disposition',
                    f"attachment; filename*=UTF-8''{file_name_encoded}; filename=\"{file_name.encode('ascii', 'ignore').decode('ascii')}\"")
                    self.send_header('Content-Length', str(os.path.getsize(real_path)))
                    self.end_headers()
                    # 分塊傳輸
                    with open(real_path, 'rb') as f:
                        while True:
                            chunk = f.read(8192)
                            if not chunk:
                                break
                            self.wfile.write(chunk)
                    return
                else:
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/octet-stream')
                    self.end_headers()
                    with open(real_path, 'rb') as f:
                        while True:
                            chunk = f.read(8192)
                            if not chunk:
                                break
                            self.wfile.write(chunk)
                    return

            # 資料夾
            elif os.path.isdir(real_path):
                if download_mode:
                    zip_bytes = zip_folder(real_path)
                    zip_name = os.path.basename(real_path.rstrip(os.sep)) + ".zip"
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/zip')
                    zip_name = os.path.basename(real_path.rstrip(os.sep)) + ".zip"
                    zip_name_encoded = urllib.parse.quote(zip_name)
                    self.send_header('Content-Disposition',
                        f"attachment; filename*=UTF-8''{zip_name_encoded}; filename=\"{zip_name.encode('ascii', 'ignore').decode('ascii')}\"")
                    self.send_header('Content-Length', str(len(zip_bytes.getvalue())))
                    self.end_headers()
                    while True:
                        chunk = zip_bytes.read(8192)
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                    return
                elif json_mode:
                    files_md5 = scan_folder_dict(real_path)
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json; charset=utf-8')
                    self.end_headers()
                    self.wfile.write(json.dumps(files_md5, ensure_ascii=False, indent=2).encode('utf-8'))
                    return
                else:
                    html = f"<h2>Folder: /{parts[0]}"
                    if sub_path:
                        html += f"/{sub_path}"
                    html += "</h2>"
                    html += list_dir_html(real_path, "/" + parts[0] + ("/" + sub_path if sub_path else ""))
                    html += "<hr><a href='/'>返回首頁</a>"
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/html; charset=utf-8')
                    self.end_headers()
                    self.wfile.write(html.encode('utf-8'))
                    return

        # 首頁
        html = "<h2>根目錄</h2><ul>"
        for key in folders:
            html += f"<li>[DIR] <a href='/{key}'>{key}</a> " \
                    f"<a href='/{key}?download=1' style='margin-left:10px;'>下載</a></li>"
        html += "</ul>"
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(html.encode('utf-8'))


# 列印檔案列表
for key, path in folders.items():
    print(f"{key}:")
    print(json.dumps(scan_folder_dict(path), indent=2, ensure_ascii=False))
    print()

# 啟動 HTTP 服務
print("Starting HTTP server on http://localhost:8000")
server_address = ('', 8000)
httpd = HTTPServer(server_address, FileBrowserHandler)
httpd.serve_forever()
