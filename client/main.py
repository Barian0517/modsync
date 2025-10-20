import os
import hashlib
import json
import requests
from urllib.parse import quote
import time
import concurrent.futures
import shutil
import zipfile
import sys

# --- 客戶端設定檔 ---
config_file = "client_config.txt"
folders = {}

server_url = "http://localhost:8000"

# 從伺服器獲取資料夾名稱列表
try:
    print(f"正在連線伺服器以獲取設定檔: {server_url}/config_names?json=1")
    resp = requests.get(f"{server_url}/config_names?json=1", timeout=10)
    if resp.status_code == 200:
        folder_names = resp.json()
    else:
        print(f"伺服器回傳錯誤代碼: {resp.status_code}")
        sys.exit(1)
except Exception as e:
    print(f"❌ 無法連線伺服器，請確認伺服器是否啟動。\n錯誤: {e}")
    sys.exit(1)

# 生成最新設定檔
with open(config_file, "w", encoding="utf-8") as f:
    for name in folder_names:
        f.write(f'{name}:""\n')
print(f"✅ {config_file} 已建立")

# 讀取設定檔，解析 force=true
for line in open(config_file, "r", encoding="utf-8"):
    line = line.strip()
    if not line or ":" not in line:
        continue
    key, path = line.split(":", 1)
    key = key.strip()
    path = path.strip().strip('"')
    force = False
    if "force=true" in key:
        key = key.replace(" force=true", "")
        force = True
    if not path:
        path = os.path.join(os.getcwd(), key)
    if not os.path.exists(path):
        os.makedirs(path)
    folders[key] = {"path": path, "force": force}


# --- MD5 ---
def get_md5(file_path):
    hash_md5 = hashlib.md5()
    try:
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_md5.update(chunk)
        return hash_md5.hexdigest()
    except:
        return None


# --- 遞歸掃描 ---
def scan_folder(folder_path):
    files_md5 = {}
    for root, dirs, files in os.walk(folder_path):
        for file in files:
            full_path = os.path.join(root, file)
            rel_path = os.path.relpath(full_path, folder_path).replace("\\", "/")
            files_md5[rel_path] = get_md5(full_path)
    return files_md5


# --- 統計檔案數量 ---
def count_server_files(server_dict):
    total = 0
    for v in server_dict.values():
        if isinstance(v, dict):
            total += count_server_files(v)
        else:
            total += 1
    return total


# --- 下載 ZIP 並解壓 ---
def download_and_extract_zip(zip_url, extract_to):
    zip_local = os.path.join(os.getcwd(), "temp.zip")
    try:
        print(f"📦 下載 ZIP: {zip_url}")
        r = requests.get(zip_url, stream=True, timeout=30)
        with open(zip_local, "wb") as f:
            for chunk in r.iter_content(65536):
                if chunk:
                    f.write(chunk)
        print("🧩 下載完成，開始解壓縮 ...")
        with zipfile.ZipFile(zip_local, 'r') as zip_ref:
            zip_ref.extractall(extract_to)
        print("✅ 解壓完成。")
    except Exception as e:
        print(f"❌ 下載或解壓失敗: {e}")
    finally:
        if os.path.exists(zip_local):
            os.remove(zip_local)


# --- 單檔下載 ---
def download_file(base_url, folder_key, file_path, save_dir, max_retries=3):
    file_url = f"{base_url}/{folder_key}/{quote(file_path)}?download=1"
    local_path = os.path.join(save_dir, file_path.replace("/", os.sep))
    os.makedirs(os.path.dirname(local_path), exist_ok=True)
    for attempt in range(max_retries):
        try:
            headers = {}
            existing_size = 0
            if os.path.exists(local_path):
                existing_size = os.path.getsize(local_path)
                headers['Range'] = f'bytes={existing_size}-'
            with requests.get(file_url, stream=True, headers=headers, timeout=15) as r:
                if r.status_code in (200, 206):
                    mode = 'ab' if existing_size > 0 else 'wb'
                    with open(local_path, mode) as f:
                        for chunk in r.iter_content(65536):
                            if chunk:
                                f.write(chunk)
                    print(f"[下載完成] {folder_key}/{file_path}")
                    return True
                else:
                    print(f"[下載失敗] {folder_key}/{file_path}: HTTP {r.status_code}")
        except Exception as e:
            print(f"[下載錯誤] {folder_key}/{file_path}: {e}")
        time.sleep(1)
    return False


# --- 收集下載任務 ---
def collect_download_tasks(server_dict, local_base, rel_path=""):
    tasks = []
    for name, value in server_dict.items():
        local_rel = f"{rel_path}/{name}" if rel_path else name
        local_abs = os.path.join(local_base, local_rel.replace("/", os.sep))
        if isinstance(value, dict):
            os.makedirs(local_abs, exist_ok=True)
            tasks.extend(collect_download_tasks(value, local_base, local_rel))
        else:
            local_md5 = get_md5(local_abs) if os.path.exists(local_abs) else None
            if not local_md5 or local_md5 != value:
                if local_md5:
                    print(f"[校驗碼不同] {local_rel}")
                    os.remove(local_abs)
                else:
                    print(f"[檔案缺失] {local_rel}")
                tasks.append(local_rel)
            else:
                print(f"[正確] {local_rel}")
    return tasks


# --- 主程式 ---
if __name__ == "__main__":
    max_workers = 8

    for key, info in folders.items():
        folder_path = info["path"]
        force = info["force"]
        print(f"\n🔍 檢查資料夾 {key} ...")

        if force:
            if os.path.exists(folder_path):
                print(f"[force] 刪除 {folder_path}")
                shutil.rmtree(folder_path)
            os.makedirs(folder_path)
            download_and_extract_zip(f"{server_url}/{key}?download=1", folder_path)
            continue

        try:
            resp = requests.get(f"{server_url}/{key}/?json=1", timeout=10)
            if resp.status_code != 200:
                print(f"無法取得服務器檔案列表: {key}")
                continue
            server_files = resp.json()
        except Exception as e:
            print(f"取得檔案列表失敗: {e}")
            continue

        tasks = collect_download_tasks(server_files, folder_path)
        total_files = count_server_files(server_files)

        if total_files == 0:
            print(f"{key}: 伺服器資料夾為空，略過。")
            continue

        ratio = len(tasks) / total_files
        print(f"{key}: 缺失/不同檔案比例 {ratio:.0%}")

        if ratio > 0.5:
            print(f"[超過一半檔案需要更新] 下載整個資料夾 ZIP")
            shutil.rmtree(folder_path)
            os.makedirs(folder_path)
            download_and_extract_zip(f"{server_url}/{key}?download=1", folder_path)
        elif tasks:
            print(f"開始下載 {len(tasks)} 個檔案 ...")
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = [executor.submit(download_file, server_url, key, t, folder_path) for t in tasks]
                concurrent.futures.wait(futures)
        else:
            print("所有檔案均正確，無需下載。")
