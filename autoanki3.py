import os
import re
import time
import urllib.parse
import urllib.request
import urllib.error
import json
import requests
import base64

from dotenv import load_dotenv
load_dotenv()

# =========================
# 設定
# =========================
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
MEDIA_DIR = "./media"

SLEEP_SEC = 2.0
MAX_RETRIES = 5
BACKOFF_BASE_SEC = 2
THUMB_SIZE = 400
USER_AGENT = "anki-auto-generator/4.0"

os.makedirs(MEDIA_DIR, exist_ok=True)

# =========================
# 入力（CLI）
# =========================
user_input = input("単語をカンマ区切りで入力: ")

INPUT_WORDS = re.split(r"[、,]", user_input)
INPUT_WORDS = [w.strip() for w in INPUT_WORDS if w.strip()]

# =========================
# ChatGPT生成
# =========================
def generate_word_data(word: str) -> dict:
    prompt = f"""
次の日本語単語について情報を出してください：

単語: {word}

JSONのみで出力：
{{
 "meaning": "",
 "reading": "",
 "example1": "",
 "example2": "",
 "image_query": ""
}}
"""

    url = "https://api.openai.com/v1/responses"

    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json; charset=utf-8"
    }

    payload = {
        "model": "gpt-5-mini",
        "input": prompt
    }

    res = requests.post(
        url,
        headers=headers,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8")
    )

    res.raise_for_status()

    data = res.json()

    try:
        text = data["output"][1]["content"][0]["text"]
    except Exception:
        print("[DEBUG]", data)
        raise Exception("レスポンス形式が不明")

    return json.loads(text)

# =========================
# ユーティリティ
# =========================
def slugify(text: str) -> str:
    text = re.sub(r"[\\/:*?\"<>|]+", "_", text)
    text = re.sub(r"\s+", "_", text)
    return text[:80]

def request_bytes_with_retry(url: str) -> bytes:
    backoff = BACKOFF_BASE_SEC
    for i in range(MAX_RETRIES):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp.read()
        except Exception:
            if i == MAX_RETRIES - 1:
                raise
            time.sleep(backoff)
            backoff *= 2

def get_json_with_retry(url: str) -> dict:
    return json.loads(request_bytes_with_retry(url).decode("utf-8"))

def ext_from_url(url: str) -> str:
    path = urllib.parse.urlparse(url).path
    _, ext = os.path.splitext(path)
    return ext if ext else ".jpg"

# =========================
# Wikipedia画像取得
# =========================
def commons_pick_thumb(term: str) -> str | None:
    search_url = "https://ja.wikipedia.org/w/api.php?" + urllib.parse.urlencode({
        "action": "query",
        "format": "json",
        "list": "search",
        "srsearch": term,
        "srlimit": 1
    })

    data = get_json_with_retry(search_url)
    results = data.get("query", {}).get("search", [])
    if not results:
        return None

    pageid = results[0]["pageid"]

    image_url = "https://ja.wikipedia.org/w/api.php?" + urllib.parse.urlencode({
        "action": "query",
        "format": "json",
        "pageids": pageid,
        "prop": "pageimages",
        "piprop": "thumbnail",
        "pithumbsize": THUMB_SIZE
    })

    data2 = get_json_with_retry(image_url)
    page = data2.get("query", {}).get("pages", {}).get(str(pageid), {})

    return page.get("thumbnail", {}).get("source")

# =========================
# Anki送信
# =========================
def add_to_anki(word, meaning, reading, ex1, ex2, image_path=None):
    image_html = ""

    try:
        # ① 画像送信
        if image_path and os.path.exists(image_path):
            with open(image_path, "rb") as f:
                img_data = base64.b64encode(f.read()).decode()

            filename = os.path.basename(image_path)

            requests.post("http://localhost:8765", json={
                "action": "storeMediaFile",
                "version": 6,
                "params": {
                    "filename": filename,
                    "data": img_data
                }
            })

            image_html = f'<img src="{filename}">'

        # ② カード作成
        res = requests.post(
            "http://localhost:8765", 
            json={
                "action": "addNote",
                "version": 6,
                "params": {
                    "note": {
                        "deckName": "vocab",
                        "modelName": "現在写真あり読書",
                        "fields": {
                            "Expression": word,
                            "定義": meaning,
                            "Reading": reading,
                            "例文": ex1,
                            "例文２": ex2,
                            "写真": image_html,
                            "単語発��": "",
                            "発音": "",
                            "書き方": "",
                            "他": "",
                        },
                        "tags": ["auto"]
                    }
                }
            }
        )

        print("[ANKI]", res.json())

    except Exception as e:
        print("[ANKI ERROR]", e)

# =========================
# メイン処理
# =========================
words = {}

print("\n=== AI生成中 ===")
for w in INPUT_WORDS:
    try:
        data = generate_word_data(w)
        words[w] = data
        print(f"[OK] {w}")
    except Exception as e:
        print(f"[ERR] {w}: {e}")

print("\n=== 実行 ===")

for word, d in words.items():
    meaning = d.get("meaning", "")
    reading = d.get("reading", "")
    ex1 = d.get("example1", "")
    ex2 = d.get("example2", "")
    image_query = d.get("image_query", word)

    image_html = ""
    path = None

    try:
        search_terms = [
            image_query,
            word,
            word + " 写真",
            word + " 画像",
            word + " object"
        ]

        thumb_url = None

        for term in search_terms:
            thumb_url = commons_pick_thumb(term)
            if thumb_url:
                break

        if thumb_url:
            filename = slugify(word) + ext_from_url(thumb_url)
            path = os.path.join(MEDIA_DIR, filename)

            if not os.path.exists(path):
                data = request_bytes_with_retry(thumb_url)
                with open(path, "wb") as img:
                    img.write(data)

            image_html = f'<img src="{filename}">'
            print(f"[IMG] {word}")
        else:
            print(f"[NO IMG] {word}")

    except Exception as e:
        print(f"[IMG ERR] {word}: {e}")

    # Anki直接追加（CSV削除）
    add_to_anki(word, meaning, reading, ex1, ex2, path)

    time.sleep(SLEEP_SEC)

print("\n✅ 完了")
print("👉 Ankiに自動追加されているはず")
