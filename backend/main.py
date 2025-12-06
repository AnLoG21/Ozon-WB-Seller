from fastapi import FastAPI, HTTPException, Depends, status, UploadFile, File
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
import os
from pathlib import Path
from typing import Optional, List, Dict
import httpx
import sqlite3
import hashlib
import json
from datetime import datetime, timedelta
import jwt
import requests
import tempfile
import subprocess
from moviepy import ImageSequenceClip
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import os
from typing import List, Dict

app = FastAPI()

# Хранилище для временных файлов
temp_files = {}

# ======================== CONFIG ========================
SECRET_KEY = os.getenv("SECRET_KEY", "your-super-secret-key-change-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 24

# OpenRouter API Config
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = "tngtech/deepseek-r1t2-chimera:free"

# Catbox.moe API Config
CATBOX_USERHASH = "7efdb06212c7f378d525201d8"

# ======================== DATABASE ========================
DB_PATH = Path(__file__).parent.parent / "app.db"

def init_db():
    """Инициализация базы данных"""
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            email TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS api_keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            marketplace TEXT NOT NULL,
            key_name TEXT,
            key_value TEXT NOT NULL,
            encrypted BOOLEAN DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS product_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            marketplace TEXT NOT NULL,
            offer_id TEXT NOT NULL,
            product_data JSON NOT NULL,
            status TEXT DEFAULT 'pending',
            response JSON,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)
    conn.commit()
    conn.close()
    print(f"✅ Database initialized at {DB_PATH}")

init_db()

# ======================== MODELS ========================
from pydantic import BaseModel

class UserRegister(BaseModel):
    username: str
    password: str
    email: Optional[str] = None

class UserLogin(BaseModel):
    username: str
    password: str

class ProductCreate(BaseModel):
    offer_id: str
    name: str
    brand: Optional[str] = None
    price: float
    stock: int
    description: Optional[str] = None
    images: Optional[List[str]] = []
    barcode: Optional[str] = None
    primary_image: Optional[int] = None
    video_url: Optional[str] = None
    wb_sku: Optional[str] = None
    wb_images: Optional[List[str]] = []
    wb_video: Optional[str] = None

class BatchProducts(BaseModel):
    products: List[ProductCreate]

class GenerateDescriptionRequest(BaseModel):
    product_name: str
    brand: Optional[str] = None
    category: Optional[str] = None
    key_features: Optional[List[str]] = []
    marketplace: str = "ozon"

class GenerateCoverVideoRequest(BaseModel):
    images: List[str]
    duration: float = 5.0

# ======================== FASTAPI APP ========================
app = FastAPI(
    title="Ozon & Wildberries Product Manager v3.5",
    description="API с OpenRouter AI и категориями товаров",
    version="3.5.0",
    debug=True
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Paths
BASE_DIR = Path(__file__).parent.parent
FRONTEND_PATH = BASE_DIR / "frontend"

# ======================== AUTH HELPERS ========================
def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

def create_access_token(user_id: int, username: str) -> str:
    payload = {
        "user_id": user_id,
        "username": username,
        "exp": datetime.utcnow() + timedelta(hours=24)
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

def verify_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")

security = HTTPBearer()

async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    print(f"Received token: {credentials.credentials}")
    try:
        payload = jwt.decode(credentials.credentials, SECRET_KEY, algorithms=[ALGORITHM])
        print(f"Decoded payload: {payload}")
        return payload
    except jwt.InvalidTokenError:
        print("Invalid token error")
        raise HTTPException(status_code=401, detail="Invalid token")

# ======================== DATABASE HELPERS ========================
def upload_to_catbox(file_content, filename, content_type):
    session = requests.Session()
    retry_strategy = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    files = {"fileToUpload": (filename, file_content, content_type)}
    data = {
        "reqtype": "fileupload",
        "userhash": CATBOX_USERHASH
    }
    try:
        response = session.post("https://catbox.moe/user/api.php", data=data, files=files, timeout=30)
        if response.status_code == 200:
            return response.text.strip()
        else:
            raise Exception(f"Upload failed: {response.status_code} - {response.text}")
    except Exception as e:
        raise Exception(f"Error uploading to catbox.moe: {str(e)}")

def get_user_by_username(username: str) -> Optional[dict]:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE username = ?", (username,))
    user = cursor.fetchone()
    conn.close()
    return dict(user) if user else None

def save_user(username: str, password_hash: str, email: Optional[str] = None) -> int:
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO users (username, password_hash, email) VALUES (?, ?, ?)",
            (username, password_hash, email)
        )
        conn.commit()
        user_id = cursor.lastrowid
        conn.close()
        return user_id
    except sqlite3.IntegrityError:
        conn.close()
        raise HTTPException(status_code=400, detail="Username already exists")

def save_api_keys(user_id: int, marketplace: str, keys: dict) -> None:
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute("DELETE FROM api_keys WHERE user_id = ? AND marketplace = ?", (user_id, marketplace))
    for key_name, key_value in keys.items():
        cursor.execute(
            "INSERT INTO api_keys (user_id, marketplace, key_name, key_value) VALUES (?, ?, ?, ?)",
            (user_id, marketplace, key_name, key_value)
        )
    conn.commit()
    conn.close()

def get_api_keys(user_id: int, marketplace: str) -> dict:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(
        "SELECT key_name, key_value FROM api_keys WHERE user_id = ? AND marketplace = ?",
        (user_id, marketplace)
    )
    keys = {row["key_name"]: row["key_value"] for row in cursor.fetchall()}
    conn.close()
    return keys

def save_product_history(user_id: int, marketplace: str, offer_id: str, product_data: dict, status: str, response: dict = None):
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO product_history (user_id, marketplace, offer_id, product_data, status, response) VALUES (?, ?, ?, ?, ?, ?)",
        (user_id, marketplace, offer_id, json.dumps(product_data), status, json.dumps(response) if response else None)
    )
    conn.commit()
    conn.close()

# ======================== AUTH ENDPOINTS ========================
@app.post("/api/auth/register")
async def register(user: UserRegister):
    if len(user.password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")
    password_hash = hash_password(user.password)
    user_id = save_user(user.username, password_hash, user.email)
    token = create_access_token(user_id, user.username)
    print(f"✅ User registered: {user.username}")
    return {
        "user_id": user_id,
        "username": user.username,
        "access_token": token,
        "token_type": "bearer"
    }

@app.post("/api/auth/login")
async def login(user: UserLogin):
    db_user = get_user_by_username(user.username)
    if not db_user:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    password_hash = hash_password(user.password)
    if password_hash != db_user["password_hash"]:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = create_access_token(db_user["id"], user.username)
    print(f"✅ User logged in: {user.username}")
    return {
        "user_id": db_user["id"],
        "username": user.username,
        "access_token": token,
        "token_type": "bearer"
    }

# ======================== API KEYS ENDPOINTS ========================
@app.post("/api/keys/save")
async def save_keys(
    marketplace: str,
    keys: dict,
    current_user: dict = Depends(get_current_user)
):
    save_api_keys(current_user["user_id"], marketplace, keys)
    print(f"✅ API keys saved for {marketplace}")
    return {"status": "ok", "message": "API keys saved"}

@app.get("/api/keys/{marketplace}")
async def get_keys(marketplace: str, current_user: dict = Depends(get_current_user)):
    keys = get_api_keys(current_user["user_id"], marketplace)
    safe_keys = {}
    for k, v in keys.items():
        if v:
            safe_keys[k] = v[:4] + "***" + v[-4:] if len(v) > 8 else "***"
    return safe_keys

# ======================== CATEGORIES ENDPOINTS ========================

@app.post("/api/categories/ozon/tree")
async def get_ozon_categories(
    current_user: dict = Depends(get_current_user)
):
    """Получить дерево категорий Ozon"""
    keys = get_api_keys(current_user["user_id"], "ozon")
    if not keys or "client_id" not in keys or "api_key" not in keys:
        raise HTTPException(status_code=400, detail="Ozon API keys not configured")
    
    headers = {
        "Client-Id": keys["client_id"],
        "Api-Key": keys["api_key"],
        "Content-Type": "application/json"
    }
    
    payload = {
        "language": "DEFAULT"
    }
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                "https://api-seller.ozon.ru/v1/description-category/tree",
                json=payload,
                headers=headers
            )
            
            if response.status_code == 200:
                data = response.json()
                print(f"✅ Ozon categories loaded")
                return data
            else:
                error_detail = response.text
                print(f"❌ Ozon API error: {response.status_code} - {error_detail}")
                raise HTTPException(status_code=response.status_code, detail=f"Ozon error: {error_detail}")
    except Exception as e:
        print(f"❌ Error loading Ozon categories: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")

@app.get("/api/categories/wildberries/tree")
async def get_wildberries_categories(
    current_user: dict = Depends(get_current_user)
):
    """Получить дерево категорий Wildberries"""
    keys = get_api_keys(current_user["user_id"], "wildberries")
    if not keys or "api_key" not in keys:
        raise HTTPException(status_code=400, detail="Wildberries API key not configured")
    headers = {"X-API-Key": keys["api_key"]}
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                "https://content-api.wildberries.ru/content/v2/object/parent/all",
                headers=headers
            )
            if response.status_code == 200:
                data = response.json()
                print(f"✅ Wildberries categories loaded")
                return data
            else:
                error_detail = response.text
                print(f"❌ WB API error: {response.status_code} - {error_detail}")
                raise HTTPException(status_code=response.status_code, detail=f"WB error: {error_detail}")
    except Exception as e:
        print(f"❌ Error loading WB categories: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")

@app.get("/api/categories/wildberries/subjects")
async def get_wildberries_subjects(
    parent_id: Optional[int] = None,
    name: Optional[str] = None,
    limit: int = 30,
    offset: int = 0,
    current_user: dict = Depends(get_current_user)
):
    """Получить предметы (подкатегории) Wildberries"""
    keys = get_api_keys(current_user["user_id"], "wildberries")
    if not keys or "api_key" not in keys:
        raise HTTPException(status_code=400, detail="Wildberries API key not configured")
    headers = {"X-API-Key": keys["api_key"]}
    params = {"limit": min(limit, 1000), "offset": offset}
    if parent_id:
        params["parentID"] = parent_id
    if name:
        params["name"] = name
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                "https://content-api.wildberries.ru/content/v2/object/all",
                headers=headers,
                params=params
            )
            if response.status_code == 200:
                data = response.json()
                print(f"✅ WB subjects loaded")
                return data
            else:
                error_detail = response.text
                print(f"❌ WB API error: {response.status_code} - {error_detail}")
                raise HTTPException(status_code=response.status_code, detail=f"WB error: {error_detail}")
    except Exception as e:
        print(f"❌ Error loading WB subjects: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")

@app.get("/api/categories/wildberries/characteristics/{subject_id}")
async def get_wildberries_characteristics(
    subject_id: int,
    current_user: dict = Depends(get_current_user)
):
    """Получить характеристики предмета Wildberries"""
    keys = get_api_keys(current_user["user_id"], "wildberries")
    if not keys or "api_key" not in keys:
        raise HTTPException(status_code=400, detail="Wildberries API key not configured")
    headers = {"X-API-Key": keys["api_key"]}
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"https://content-api.wildberries.ru/content/v2/object/charcs/{subject_id}",
                headers=headers
            )
            if response.status_code == 200:
                data = response.json()
                print(f"✅ WB characteristics loaded for subject {subject_id}")
                return data
            else:
                error_detail = response.text
                print(f"❌ WB API error: {response.status_code} - {error_detail}")
                raise HTTPException(status_code=response.status_code, detail=f"WB error: {error_detail}")
    except Exception as e:
        print(f"❌ Error loading WB characteristics: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")

# ======================== BARCODE ENDPOINTS ========================
@app.post("/api/ozon/barcode/add")
async def add_ozon_barcode(
    request_data: dict,
    current_user: dict = Depends(get_current_user)
):
    """Привязать баркод к товару в Ozon"""
    keys = get_api_keys(current_user["user_id"], "ozon")
    if not keys or "api_key" not in keys or "client_id" not in keys:
        raise HTTPException(status_code=400, detail="Ozon API keys not configured")
    headers = {
        "Client-Id": keys["client_id"],
        "Api-Key": keys["api_key"],
        "Content-Type": "application/json"
    }
    payload = {"barcodes": request_data.get("barcodes", [])}
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                "https://api-seller.ozon.ru/v1/barcode/add",
                headers=headers,
                json=payload
            )
            if response.status_code == 200:
                data = response.json()
                print(f"✅ Ozon barcodes added: {len(payload['barcodes'])} items")
                return data
            else:
                error_detail = response.text
                print(f"❌ Ozon API error: {response.status_code} - {error_detail}")
                raise HTTPException(status_code=response.status_code, detail=f"Ozon error: {error_detail}")
    except Exception as e:
        print(f"❌ Error adding Ozon barcodes: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")

@app.post("/api/ozon/barcode/generate")
async def generate_ozon_barcodes(
    request_data: dict,
    current_user: dict = Depends(get_current_user)
):
    """Сгенерировать баркоды для товаров в Ozon"""
    keys = get_api_keys(current_user["user_id"], "ozon")
    if not keys or "api_key" not in keys or "client_id" not in keys:
        raise HTTPException(status_code=400, detail="Ozon API keys not configured")
    headers = {
        "Client-Id": keys["client_id"],
        "Api-Key": keys["api_key"],
        "Content-Type": "application/json"
    }
    payload = {"product_ids": [str(pid) for pid in request_data.get("product_ids", [])]}
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                "https://api-seller.ozon.ru/v1/barcode/generate",
                headers=headers,
                json=payload
            )
            if response.status_code == 200:
                data = response.json()
                print(f"✅ Ozon barcodes generated for {len(payload['product_ids'])} products")
                return data
            else:
                error_detail = response.text
                print(f"❌ Ozon API error: {response.status_code} - {error_detail}")
                raise HTTPException(status_code=response.status_code, detail=f"Ozon error: {error_detail}")
    except Exception as e:
        print(f"❌ Error generating Ozon barcodes: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")

@app.post("/api/wildberries/barcode/generate")
async def generate_wildberries_barcodes(
    request_data: dict,
    current_user: dict = Depends(get_current_user)
):
    """Сгенерировать баркоды в Wildberries"""
    keys = get_api_keys(current_user["user_id"], "wildberries")
    if not keys or "api_key" not in keys:
        raise HTTPException(status_code=400, detail="Wildberries API key not configured")
    headers = {"Authorization": keys["api_key"], "Content-Type": "application/json"}
    count = request_data.get("count", 1)
    if count < 1 or count > 5000:
        raise HTTPException(status_code=400, detail="Count must be between 1 and 5000")
    payload = {"count": count}
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                "https://content-api.wildberries.ru/content/v2/barcodes",
                headers=headers,
                json=payload
            )
            if response.status_code == 200:
                data = response.json()
                print(f"✅ WB barcodes generated: {count} items")
                return data
            else:
                error_detail = response.text
                print(f"❌ WB API error: {response.status_code} - {error_detail}")
                raise HTTPException(status_code=response.status_code, detail=f"WB error: {error_detail}")
    except Exception as e:
        print(f"❌ Error generating WB barcodes: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")

# ======================== OPENROUTER AI ENDPOINTS ========================
async def generate_description_openrouter(
    product_name: str,
    brand: Optional[str] = None,
    category: Optional[str] = None,
    key_features: Optional[List[str]] = None,
    marketplace: str = "ozon"
) -> str:
    """Генерация описания товара с помощью OpenRouter AI"""
    if not OPENROUTER_API_KEY:
        raise HTTPException(
            status_code=400,
            detail="OpenRouter API key not configured. Set OPENROUTER_API_KEY environment variable."
        )
    features_text = ", ".join(key_features) if key_features else "не указаны"
    marketplace_name = "Ozon" if marketplace == "ozon" else "Wildberries"
    prompt = f"""Напиши краткое и привлекательное описание товара для маркетплейса {marketplace_name}.
Название товара: {product_name}
Бренд: {brand or 'не указан'}
Категория: {category or 'не указана'}
Ключевые характеристики: {features_text}
Требования:
- Описание должно быть кратким (100-150 слов)
- Описание должно быть привлекательным и информативным
- Описание должно подчеркивать преимущества товара
- Избегай чрезмерно использовать восклицательные знаки
- Используй профессиональный тон
- Написано для {marketplace_name}
Напиши только описание без дополнительных комментариев."""
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "http://localhost:8000",
        "X-Title": "Marketplace Manager"
    }
    payload = {
        "model": OPENROUTER_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.7,
        "max_tokens": 1024,
        "top_p": 0.95
    }
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                json=payload,
                headers=headers
            )
            if response.status_code == 200:
                data = response.json()
                if "choices" in data and len(data["choices"]) > 0:
                    choice = data["choices"][0]
                    if "message" in choice and "content" in choice["message"]:
                        description = choice["message"]["content"].strip()
                        if description and description != "":
                            print(f"✅ Description generated for: {product_name}")
                            return description
                        else:
                            raise HTTPException(status_code=500, detail="Empty response from OpenRouter (empty content)")
                    else:
                        raise HTTPException(status_code=500, detail="No choices in response from OpenRouter")
                else:
                    raise HTTPException(status_code=500, detail="No choices in response from OpenRouter")
            else:
                error_detail = response.text
                print(f"❌ OpenRouter error: {response.status_code} - {error_detail}")
                raise HTTPException(status_code=response.status_code, detail=f"OpenRouter error: {error_detail}")
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="OpenRouter request timeout")
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ OpenRouter error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")

@app.post("/api/generate-description")
async def generate_description(
    request: GenerateDescriptionRequest,
    current_user: dict = Depends(get_current_user)
):
    """Endpoint для генерации описания товара с помощью OpenRouter AI"""
    try:
        description = await generate_description_openrouter(
            product_name=request.product_name,
            brand=request.brand,
            category=request.category,
            key_features=request.key_features,
            marketplace=request.marketplace
        )
        return {"success": True, "description": description}
    except HTTPException as e:
        raise e
    except Exception as e:
        print(f"❌ Error generating description: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")

# ======================== VIDEO COVER GENERATION ========================
from PIL import Image

@app.post("/api/generate-video-cover")
async def generate_video_cover(file_ids: List[str]):
    """Генерация видеообложки из временных файлов"""
    if len(file_ids) == 0:
        raise HTTPException(status_code=400, detail="No images provided")
    
    # Получаем пути к временным файлам
    image_files = []
    for file_id in file_ids:
        if file_id in temp_files:
            image_files.append(temp_files[file_id])
        else:
            raise HTTPException(status_code=400, detail=f"File not found: {file_id}")
    
    if len(image_files) == 0:
        raise HTTPException(status_code=400, detail="Failed to load any images")
    
    # Проверка размеров изображений
    sizes = []
    for image_path in image_files:
        with Image.open(image_path) as img:
            sizes.append(img.size)
    if len(set(sizes)) > 1:
        raise HTTPException(status_code=400, detail="All images must be the same size")
    
    # Создаём видеообложку
    clip = ImageSequenceClip(image_files, fps=1.0)
    clip = clip.with_duration(5.0)
    video_path = tempfile.mktemp(suffix=".mp4")
    clip.write_videofile(video_path, codec="libx264", fps=1.0)
    
    # Загружаем видео на catbox.moe
    with open(video_path, "rb") as f:
        file_content = f.read()
    files = {"fileToUpload": ("cover_video.mp4", file_content, "video/mp4")}
    data = {
        "reqtype": "fileupload",
        "userhash": "7efdb06212c7f378d525201d8"
    }
    response = requests.post("https://catbox.moe/user/api.php", data=data, files=files)
    if response.status_code != 200:
        raise HTTPException(status_code=500, detail="Failed to upload video")
    
    # Удаляем временные файлы
    for file_id in file_ids:
        if file_id in temp_files:
            os.remove(temp_files[file_id])
            del temp_files[file_id]
    os.remove(video_path)
    
    return {"video_url": response.text.strip()}

# ======================== PRODUCT ENDPOINTS ========================
def build_ozon_product(product: ProductCreate) -> dict:
    """Построение продукта для Ozon API"""
    ozon_product = {
        "offer_id": product.offer_id,
        "name": product.name,
        "brand": product.brand or "",
        "price": str(int(product.price * 100)),
        "description": product.description or "",
    }
    if product.images:
        ozon_product["images"] = [{"file_name": url} for url in product.images if url]
        if product.primary_image and product.primary_image > 0:
            ozon_product["primary_image"] = {"file_name": product.images[product.primary_image - 1]}
    if product.video_url:
        ozon_product["complex_attributes"] = [
            {
                "complex_id": 100002,
                "id": 21845,
                "values": [{"value": product.video_url}]
            }
        ]
    if product.barcode:
        ozon_product["barcode"] = product.barcode
    return ozon_product

async def ozon_create_product(product: ProductCreate, client_id: str, api_key: str) -> dict:
    ozon_product = build_ozon_product(product)
    payload = {"items": [ozon_product]}
    headers = {
        "Client-Id": client_id,
        "Api-Key": api_key,
        "Content-Type": "application/json"
    }
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                "https://api-seller.ozon.ru/v2/product/import",
                json=payload,
                headers=headers
            )
            return {
                "status": response.status_code,
                "body": response.json() if response.text else {},
                "success": response.status_code == 200
            }
    except Exception as e:
        print(f"❌ Ozon API error: {str(e)}")
        return {"status": 500, "error": str(e), "success": False}

async def wb_create_product(product: ProductCreate, api_key: str) -> dict:
    wb_product = {
        "vendorCode": product.wb_sku or product.offer_id,
        "brand": product.brand or "",
        "title": product.name,
        "description": product.description or "",
        "sizes": [
            {
                "skus": [product.offer_id],
                "price": int(product.price * 100),
                "stocks": [{"warehouseId": 0, "quantity": product.stock}]
            }
        ]
    }
    if product.wb_images:
        wb_product["mediaFiles"] = [url for url in product.wb_images if url]
    payload = [wb_product]
    headers = {
        "X-API-Key": api_key,
        "Content-Type": "application/json"
    }
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                "https://suppliers-api.wildberries.ru/content/v2/cards/upload",
                json=payload,
                headers=headers
            )
            return {
                "status": response.status_code,
                "body": response.json() if response.text else {},
                "success": response.status_code == 200
            }
    except Exception as e:
        print(f"❌ Wildberries API error: {str(e)}")
        return {"status": 500, "error": str(e), "success": False}

@app.post("/api/ozon/products/batch")
async def batch_create_ozon(
    batch: BatchProducts,
    current_user: dict = Depends(get_current_user)
):
    if len(batch.products) > 100:
        raise HTTPException(status_code=400, detail="Maximum 100 products per batch")
    keys = get_api_keys(current_user["user_id"], "ozon")
    if not keys or "client_id" not in keys or "api_key" not in keys:
        raise HTTPException(status_code=400, detail="Ozon API keys not configured")
    results = []
    for product in batch.products:
        result = await ozon_create_product(product, keys["client_id"], keys["api_key"])
        results.append({
            "offer_id": product.offer_id,
            "result": result
        })
        save_product_history(
            current_user["user_id"],
            "ozon",
            product.offer_id,
            product.dict(),
            "success" if result.get("success") else "failed",
            result
        )
    print(f"✅ Batch created {len(batch.products)} products in Ozon")
    return {"total": len(batch.products), "results": results}

@app.post("/api/wb/products/batch")
async def batch_create_wb(
    batch: BatchProducts,
    current_user: dict = Depends(get_current_user)
):
    if len(batch.products) > 100:
        raise HTTPException(status_code=400, detail="Maximum 100 products per batch")
    keys = get_api_keys(current_user["user_id"], "wildberries")
    if not keys or "api_key" not in keys:
        raise HTTPException(status_code=400, detail="Wildberries API key not configured")
    results = []
    for product in batch.products:
        result = await wb_create_product(product, keys["api_key"])
        results.append({
            "offer_id": product.offer_id,
            "result": result
        })
        save_product_history(
            current_user["user_id"],
            "wildberries",
            product.offer_id,
            product.dict(),
            "success" if result.get("success") else "failed",
            result
        )
    print(f"✅ Batch created {len(batch.products)} products in Wildberries")
    return {"total": len(batch.products), "results": results}

# ======================== MEDIA UPLOAD ENDPOINT (Catbox.moe) ========================
@app.post("/api/upload-media")
async def upload_media(file: UploadFile = File(...)):
    """Загрузка медиа на сервер и сохранение временного пути"""
    try:
        # Создаем временный файл
        temp_dir = tempfile.mkdtemp()
        temp_path = os.path.join(temp_dir, file.filename)
        with open(temp_path, "wb") as f:
            f.write(await file.read())
        
        # Загружаем файл на catbox.moe
        with open(temp_path, "rb") as f:
            file_content = f.read()
        files = {"fileToUpload": (file.filename, file_content, file.content_type)}
        data = {
            "reqtype": "fileupload",
            "userhash": "7efdb06212c7f378d525201d8"
        }
        response = requests.post("https://catbox.moe/user/api.php", data=data, files=files)
        if response.status_code != 200:
            raise HTTPException(status_code=500, detail="Failed to upload file")
        
        # Сохраняем путь во временное хранилище
        file_id = str(id(file))
        temp_files[file_id] = temp_path
        
        return {"file_id": file_id, "url": response.text.strip()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")

@app.post("/api/delete-media")
async def delete_media(file_urls: List[str]):
    """Удаление файлов с catbox.moe"""
    try:
        file_names = [url.split("/")[-1] for url in file_urls]
        data = {
            "reqtype": "deletefiles",
            "userhash": CATBOX_USERHASH,
            "files": " ".join(file_names)
        }
        response = requests.post("https://catbox.moe/user/api.php", data=data)
        if response.status_code == 200:
            return {"status": "ok", "message": "Files deleted"}
        else:
            raise HTTPException(status_code=500, detail="Failed to delete media")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")

# ======================== STATIC FILES ========================
@app.get("/")
async def root():
    index_path = FRONTEND_PATH / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path), media_type="text/html")
    return {"message": "Welcome to Marketplace Manager v3.5"}

@app.get("/index.html")
async def index_page():
    index_path = FRONTEND_PATH / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path), media_type="text/html")
    return {"error": "index.html not found"}

@app.get("/dashboard.html")
async def dashboard_page():
    dashboard_path = FRONTEND_PATH / "dashboard.html"
    if dashboard_path.exists():
        return FileResponse(str(dashboard_path), media_type="text/html")
    return {"error": "dashboard.html not found"}

@app.get("/favicon.ico")
async def favicon():
    favicon_path = FRONTEND_PATH / "favicon.ico"
    if favicon_path.exists():
        return FileResponse(str(favicon_path))
    return {"error": "favicon not found"}

@app.get("/health")
async def health():
    return {"status": "ok", "version": "3.5.0"}

# ======================== STARTUP/SHUTDOWN ========================
@app.on_event("startup")
async def startup():
    print(f"🚀 Application started (version 3.5.0)")
    print(f"📍 Database: {DB_PATH}")
    print(f"📁 Frontend: {FRONTEND_PATH}")
    if OPENROUTER_API_KEY:
        print(f"✅ OpenRouter AI API configured")
        print(f"📊 Model: {OPENROUTER_MODEL}")
    else:
        print(f"⚠️  OpenRouter AI API not configured. Set OPENROUTER_API_KEY environment variable.")
    print(f"📂 Categories endpoints:")
    print(f"   - POST /api/categories/ozon/tree")
    print(f"   - GET /api/categories/wildberries/tree")
    print(f"   - GET /api/categories/wildberries/subjects")
    print(f"   - GET /api/categories/wildberries/characteristics/{{subject_id}}")

@app.on_event("shutdown")
async def shutdown():
    print("🛑 Application shutdown")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "backend.main:app",
        host="127.0.0.1",
        port=8000,
        reload=True
    )
