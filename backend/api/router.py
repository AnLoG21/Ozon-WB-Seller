from fastapi import APIRouter, HTTPException
from backend.schemas.product import ProductBase
from backend.services.ozon_service import OzonService
from backend.services.wildberries_service import WildberriesService
from backend.utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/api", tags=["marketplace"])

@router.post("/ozon/build")
async def build_ozon_payload(
    product: ProductBase,
    client_id: str = "",
    api_key: str = ""
):
    """Построить payload для Ozon"""
    try:
        result = OzonService.build_request(product, client_id, api_key)
        return {"success": True, "data": result}
    except Exception as e:
        logger.error(f"Error building Ozon payload: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/ozon/send")
async def send_ozon_request(
    product: ProductBase,
    client_id: str,
    api_key: str,
    env: str = "sandbox"
):
    """Отправить товар в Ozon (или показать payload в sandbox)"""
    try:
        result = await OzonService.send_request(product, client_id, api_key, env)
        return {"success": True, "data": result}
    except Exception as e:
        logger.error(f"Error sending to Ozon: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/wildberries/build")
async def build_wb_payload(product: ProductBase, api_key: str = ""):
    """Построить payload для Wildberries"""
    try:
        result = WildberriesService.build_request(product, api_key)
        return {"success": True, "data": result}
    except Exception as e:
        logger.error(f"Error building WB payload: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/wildberries/send")
async def send_wb_request(
    product: ProductBase,
    api_key: str,
    env: str = "sandbox"
):
    """Отправить товар в Wildberries (или показать payload в sandbox)"""
    try:
        result = await WildberriesService.send_request(product, api_key, env)
        return {"success": True, "data": result}
    except Exception as e:
        logger.error(f"Error sending to WB: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
