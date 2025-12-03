from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
import os
from pathlib import Path
from typing import Optional, List
import httpx

from backend.config import settings
from backend.utils.logger import get_logger

logger = get_logger(__name__)

# Инициализация FastAPI
app = FastAPI(
    title="Ozon & Wildberries Product Manager",
    description="API для управления карточками товаров на маркетплейсах",
    version="2.0.0",
    debug=settings.DEBUG
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Определить путь к frontend папке
BASE_DIR = Path(__file__).parent.parent
FRONTEND_PATH = BASE_DIR / "frontend"

# Подключить статические файлы
try:
    app.mount("/static", StaticFiles(directory=str(FRONTEND_PATH)), name="static")
    logger.info(f"✅ Static files mounted from {FRONTEND_PATH}")
except Exception as e:
    logger.warning(f"❌ Could not mount static files: {e}")

# ======================== MODELS ========================

from pydantic import BaseModel
from typing import Dict, Any, List

class ProductCreate(BaseModel):
    """Модель для создания/обновления товара"""
    offer_id: str  # Артикул
    name: str
    brand: str
    price: float
    stock: int
    category: str
    description: str
    images: List[str] = []
    barcode: Optional[str] = None
    old_price: Optional[float] = None
    vat: str = "0"

class OzonProduct(BaseModel):
    """Модель товара для Ozon API"""
    offer_id: str
    name: str
    brand: str
    price: str
    description: str
    images: List[Dict[str, str]] = []
    attributes: List[Dict[str, Any]] = []
    barcode: str = ""
    old_price: str = ""
    premium_price: str = ""
    vat: str = "0"

class WBProduct(BaseModel):
    """Модель товара для Wildberries API"""
    vendorCode: str
    brand: str
    title: str
    description: str
    sizes: List[Dict[str, Any]] = []
    mediaFiles: List[str] = []

# ======================== OZON API ========================

async def ozon_create_product(product: ProductCreate, client_id: str, api_key: str) -> Dict[str, Any]:
    """Создание/обновление товара в Ozon"""
    
    ozon_product = {
        "offer_id": product.offer_id,
        "name": product.name,
        "brand": product.brand,
        "price": str(int(product.price * 100)),  # Копейки
        "description": product.description,
        "images": [{"file_name": url} for url in product.images],
        "attributes": [],
        "barcode": product.barcode or "",
        "old_price": str(int(product.old_price * 100)) if product.old_price else "",
        "premium_price": "",
        "vat": product.vat
    }
    
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
                "body": response.json() if response.text else {}
            }
    except Exception as e:
        logger.error(f"Ozon API error: {str(e)}")
        return {"status": 500, "error": str(e)}

async def ozon_get_products(client_id: str, api_key: str, limit: int = 100) -> Dict[str, Any]:
    """Получение списка товаров из Ozon"""
    
    payload = {
        "limit": limit,
        "offset": 0,
        "filter": {"visibility": "ALL"}
    }
    
    headers = {
        "Client-Id": client_id,
        "Api-Key": api_key,
        "Content-Type": "application/json"
    }
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                "https://api-seller.ozon.ru/v2/product/list",
                json=payload,
                headers=headers
            )
            return {
                "status": response.status_code,
                "body": response.json() if response.text else {}
            }
    except Exception as e:
        logger.error(f"Ozon API error: {str(e)}")
        return {"status": 500, "error": str(e)}

async def ozon_get_product_info(product_id: str, client_id: str, api_key: str) -> Dict[str, Any]:
    """Получение информации о товаре"""
    
    payload = {
        "product_id": int(product_id),
        "sku": 0
    }
    
    headers = {
        "Client-Id": client_id,
        "Api-Key": api_key,
        "Content-Type": "application/json"
    }
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                "https://api-seller.ozon.ru/v2/product/info",
                json=payload,
                headers=headers
            )
            return {
                "status": response.status_code,
                "body": response.json() if response.text else {}
            }
    except Exception as e:
        logger.error(f"Ozon API error: {str(e)}")
        return {"status": 500, "error": str(e)}

# ======================== WILDBERRIES API ========================

async def wb_create_product(product: ProductCreate, api_key: str) -> Dict[str, Any]:
    """Создание/обновление товара в Wildberries"""
    
    wb_product = {
        "vendorCode": product.offer_id,
        "brand": product.brand,
        "title": product.name,
        "description": product.description,
        "sizes": [
            {
                "skus": [product.offer_id],
                "price": int(product.price * 100),  # Копейки
                "stocks": [{"warehouseId": 0, "quantity": product.stock}]
            }
        ],
        "mediaFiles": product.images
    }
    
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
                "body": response.json() if response.text else {}
            }
    except Exception as e:
        logger.error(f"Wildberries API error: {str(e)}")
        return {"status": 500, "error": str(e)}

async def wb_get_products(api_key: str, limit: int = 100) -> Dict[str, Any]:
    """Получение списка товаров из Wildberries"""
    
    headers = {
        "X-API-Key": api_key,
        "Content-Type": "application/json"
    }
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"https://suppliers-api.wildberries.ru/content/v1/cards/list?take={limit}&skip=0",
                headers=headers
            )
            return {
                "status": response.status_code,
                "body": response.json() if response.text else {}
            }
    except Exception as e:
        logger.error(f"Wildberries API error: {str(e)}")
        return {"status": 500, "error": str(e)}

# ======================== REST ENDPOINTS ========================

@app.get("/")
async def root():
    """Главная страница"""
    index_path = FRONTEND_PATH / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path), media_type="text/html")
    return {"error": "index.html not found"}

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
    return {"status": "ok", "version": "2.0.0"}

# ======================== OZON ENDPOINTS ========================

@app.post("/api/ozon/products/create")
async def create_ozon_product(
    product: ProductCreate,
    client_id: str,
    api_key: str
):
    """Создать товар в Ozon"""
    logger.info(f"Creating product in Ozon: {product.offer_id}")
    result = await ozon_create_product(product, client_id, api_key)
    logger.info(f"Ozon response: {result}")
    return result

@app.get("/api/ozon/products/list")
async def list_ozon_products(client_id: str, api_key: str, limit: int = 100):
    """Получить список товаров из Ozon"""
    logger.info(f"Listing Ozon products")
    result = await ozon_get_products(client_id, api_key, limit)
    logger.info(f"Ozon response: {result}")
    return result

@app.get("/api/ozon/products/{product_id}")
async def get_ozon_product(product_id: str, client_id: str, api_key: str):
    """Получить информацию о товаре"""
    logger.info(f"Getting Ozon product: {product_id}")
    result = await ozon_get_product_info(product_id, client_id, api_key)
    logger.info(f"Ozon response: {result}")
    return result

# ======================== WILDBERRIES ENDPOINTS ========================

@app.post("/api/wb/products/create")
async def create_wb_product(
    product: ProductCreate,
    api_key: str
):
    """Создать товар в Wildberries"""
    logger.info(f"Creating product in Wildberries: {product.offer_id}")
    result = await wb_create_product(product, api_key)
    logger.info(f"Wildberries response: {result}")
    return result

@app.get("/api/wb/products/list")
async def list_wb_products(api_key: str, limit: int = 100):
    """Получить список товаров из Wildberries"""
    logger.info(f"Listing Wildberries products")
    result = await wb_get_products(api_key, limit)
    logger.info(f"Wildberries response: {result}")
    return result

# ======================== DUAL ENDPOINTS ========================

@app.post("/api/both/products/create")
async def create_both_products(
    product: ProductCreate,
    ozon_client_id: Optional[str] = None,
    ozon_api_key: Optional[str] = None,
    wb_api_key: Optional[str] = None
):
    """Создать товар одновременно в Ozon и Wildberries"""
    logger.info(f"Creating product in both marketplaces: {product.offer_id}")
    
    results = {
        "ozon": None,
        "wildberries": None
    }
    
    if ozon_client_id and ozon_api_key:
        results["ozon"] = await ozon_create_product(product, ozon_client_id, ozon_api_key)
    
    if wb_api_key:
        results["wildberries"] = await wb_create_product(product, wb_api_key)
    
    logger.info(f"Both marketplaces response: {results}")
    return results

# ======================== STARTUP/SHUTDOWN ========================

@app.on_event("startup")
async def startup():
    logger.info(f"🚀 Application started (version 2.0.0)")
    logger.info(f"📍 CORS origins: {settings.CORS_ORIGINS}")

@app.on_event("shutdown")
async def shutdown():
    logger.info("🛑 Application shutdown")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "backend.main:app",
        host=settings.BACKEND_HOST,
        port=settings.BACKEND_PORT,
        reload=settings.BACKEND_RELOAD
    )