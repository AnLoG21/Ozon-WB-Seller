from fastapi import FastAPI, HTTPException, Depends, UploadFile, File
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.staticfiles import StaticFiles
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

# ======================== CONFIG ========================

SECRET_KEY = os.getenv("SECRET_KEY", "your-super-secret-key-change-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 24

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

# ======================== FASTAPI APP ========================

app = FastAPI(
    title="Ozon & Wildberries Product Manager v3.2",
    description="API с аутентификацией, batch upload и загрузкой медиа",
    version="3.2.0",
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
        "exp": datetime.utcnow() + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
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
    return verify_token(credentials.credentials)

# ======================== DATABASE HELPERS ========================

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

# ======================== OZON HELPERS ========================

def build_ozon_product(product: ProductCreate) -> dict:
    """Построение продукта для Ozon API"""
    ozon_product = {
        "offer_id": product.offer_id,
        "name": product.name,
        "brand": product.brand or "",
        "price": str(int(product.price * 100)),
        "description": product.description or "",
    }
    
    # Обработка картинок
    if product.images:
        ozon_product["images"] = [{"file_name": url} for url in product.images if url]
        
        # Главная картинка
        if product.primary_image and product.primary_image > 0:
            ozon_product["primary_image"] = {"file_name": product.images[product.primary_image - 1]}
    
    # Обработка видео
    if product.video_url:
        ozon_product["complex_attributes"] = [
            {
                "complex_id": 100001,
                "id": 21841,  # URL видео
                "values": [{"value": product.video_url}]
            },
            {
                "complex_id": 100001,
                "id": 21837,  # Название видео
                "values": [{"value": "Video 1"}]
            }
        ]
    
    if product.barcode:
        ozon_product["barcode"] = product.barcode
    
    return ozon_product

async def ozon_create_product(product: ProductCreate, client_id: str, api_key: str) -> dict:
    """Загрузка продукта в Ozon"""
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

# ======================== WILDBERRIES HELPERS ========================

async def wb_save_media(nmId: int, media_urls: List[str], api_key: str) -> dict:
    """Загрузка картинок в Wildberries"""
    payload = {
        "nmId": nmId,
        "data": media_urls
    }
    headers = {
        "X-API-Key": api_key,
        "Content-Type": "application/json"
    }
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                "https://content-api.wildberries.ru/content/v3/media/save",
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

async def wb_create_product(product: ProductCreate, api_key: str) -> dict:
    """Загрузка продукта в Wildberries"""
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

# ======================== PRODUCT ENDPOINTS ========================

@app.post("/api/ozon/products/batch")
async def batch_create_ozon(
    batch: BatchProducts,
    current_user: dict = Depends(get_current_user)
):
    """Batch загрузка товаров в Ozon"""
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
    """Batch загрузка товаров в Wildberries"""
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

@app.get("/api/ozon/products/list")
async def list_ozon_products(
    limit: int = 100,
    current_user: dict = Depends(get_current_user)
):
    """Список товаров Ozon"""
    keys = get_api_keys(current_user["user_id"], "ozon")
    if not keys or "client_id" not in keys or "api_key" not in keys:
        raise HTTPException(status_code=400, detail="Ozon API keys not configured")
    
    print(f"Listing Ozon products for user {current_user['username']}")
    return {"message": "Coming soon"}

@app.get("/api/wb/products/list")
async def list_wb_products(
    limit: int = 100,
    current_user: dict = Depends(get_current_user)
):
    """Список товаров Wildberries"""
    keys = get_api_keys(current_user["user_id"], "wildberries")
    if not keys or "api_key" not in keys:
        raise HTTPException(status_code=400, detail="Wildberries API key not configured")
    
    print(f"Listing Wildberries products for user {current_user['username']}")
    return {"message": "Coming soon"}

# ======================== STATIC FILES ========================

@app.get("/")
async def root():
    """Главная страница"""
    index_path = FRONTEND_PATH / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path), media_type="text/html")
    return {"message": "Welcome to Marketplace Manager v3.2"}

@app.get("/index.html")
async def index_page():
    """Страница авторизации"""
    index_path = FRONTEND_PATH / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path), media_type="text/html")
    return {"error": "index.html not found"}

@app.get("/dashboard.html")
async def dashboard_page():
    """Страница dashboard"""
    dashboard_path = FRONTEND_PATH / "dashboard.html"
    if dashboard_path.exists():
        return FileResponse(str(dashboard_path), media_type="text/html")
    return {"error": "dashboard.html not found"}

@app.get("/favicon.ico")
async def favicon():
    """Favicon"""
    favicon_path = FRONTEND_PATH / "favicon.ico"
    if favicon_path.exists():
        return FileResponse(str(favicon_path))
    return {"error": "favicon not found"}

@app.get("/health")
async def health():
    """Health check"""
    return {"status": "ok", "version": "3.2.0"}

# ======================== STARTUP/SHUTDOWN ========================

@app.on_event("startup")
async def startup():
    print(f"🚀 Application started (version 3.2.0)")
    print(f"📍 Database: {DB_PATH}")
    print(f"📁 Frontend: {FRONTEND_PATH}")

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