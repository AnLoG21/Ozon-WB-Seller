from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
import os
from pathlib import Path

from backend.config import settings
from backend.api.router import router
from backend.utils.logger import get_logger

logger = get_logger(__name__)

# Инициализация FastAPI
app = FastAPI(
    title="Ozon & Wildberries Product Manager",
    description="API для управления карточками товаров на маркетплейсах",
    version="1.0.0",
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

# Подключить API router
app.include_router(router)

# Определить путь к frontend папке
BASE_DIR = Path(__file__).parent.parent
FRONTEND_PATH = BASE_DIR / "frontend"

logger.info(f"BASE_DIR: {BASE_DIR}")
logger.info(f"FRONTEND_PATH: {FRONTEND_PATH}")
logger.info(f"FRONTEND_PATH exists: {FRONTEND_PATH.exists()}")

# Подключить статические файлы (CSS, JS, assets)
try:
    static_path = str(FRONTEND_PATH)
    app.mount("/static", StaticFiles(directory=static_path), name="static")
    logger.info(f"✅ Static files mounted from {static_path}")
except Exception as e:
    logger.warning(f"❌ Could not mount static files: {e}")

# Главная страница (serve index.html)
@app.get("/")
async def root():
    index_path = FRONTEND_PATH / "index.html"
    logger.info(f"Trying to serve index.html from: {index_path}")
    logger.info(f"index.html exists: {index_path.exists()}")
    
    if index_path.exists():
        return FileResponse(str(index_path), media_type="text/html")
    else:
        logger.error(f"❌ index.html not found at {index_path}")
        return {
            "error": "index.html not found",
            "path": str(index_path),
            "frontend_path": str(FRONTEND_PATH),
            "files_in_frontend": os.listdir(str(FRONTEND_PATH)) if FRONTEND_PATH.exists() else "folder doesn't exist"
        }

# Favicon (убирает 404)
@app.get("/favicon.ico")
async def favicon():
    favicon_path = FRONTEND_PATH / "favicon.ico"
    if favicon_path.exists():
        return FileResponse(str(favicon_path))
    else:
        # Возвращаем пустой PNG если нет favicon
        return FileResponse(
            path="data:image/x-icon;base64,AAABAAEAEBAAAAEAIABoBAAAFgAAACgAAAAQAAAAIAAAAAEAIAAAAAAAAAQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
            media_type="image/x-icon"
        )

# Health check
@app.get("/health")
async def health():
    return {"status": "ok", "env": settings.ENV}

# Startup event
@app.on_event("startup")
async def startup():
    logger.info(f"🚀 Application started in {settings.ENV} mode")
    logger.info(f"📍 CORS origins: {settings.CORS_ORIGINS}")
    logger.info(f"📁 Frontend path: {FRONTEND_PATH}")

# Shutdown event
@app.on_event("shutdown")
async def shutdown():
    logger.info("🛑 Application shutdown")

if __name__ == "__main__":
    import uvicorn
    logger.info(f"Starting server on {settings.BACKEND_HOST}:{settings.BACKEND_PORT}")
    uvicorn.run(
        "backend.main:app",
        host=settings.BACKEND_HOST,
        port=settings.BACKEND_PORT,
        reload=True
    )