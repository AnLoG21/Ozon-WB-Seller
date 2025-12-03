from backend.utils.logger import get_logger
from backend.schemas.product import ProductBase
from backend.services.payload_builder import PayloadBuilder
from typing import Dict, Any

logger = get_logger(__name__)

class OzonService:
    ENDPOINT = "https://api-seller.ozon.ru/v2/product/import"
    
    @staticmethod
    def build_request(product: ProductBase, client_id: str, api_key: str) -> Dict[str, Any]:
        """Подготовить запрос к Ozon API"""
        payload = PayloadBuilder.build_ozon_payload(product)
        
        headers = {
            "Client-Id": client_id,
            "Api-Key": api_key,
            "Content-Type": "application/json"
        }
        
        logger.info(f"Ozon payload built for SKU: {product.sku}")
        
        return {
            "endpoint": OzonService.ENDPOINT,
            "method": "POST",
            "headers": headers,
            "body": payload
        }
    
    @staticmethod
    async def send_request(product: ProductBase, client_id: str, api_key: str, env: str = "sandbox"):
        """В режиме prod отправить запрос к Ozon"""
        request_data = OzonService.build_request(product, client_id, api_key)
        
        if env == "sandbox":
            logger.info("Running in SANDBOX mode - request not sent")
            return {"status": "sandbox", "request": request_data}
        
        # Здесь реальный HTTP запрос через requests/httpx
        logger.info(f"Sending request to Ozon...")
        # response = requests.post(...)
        
        return {"status": "sent", "request": request_data}
