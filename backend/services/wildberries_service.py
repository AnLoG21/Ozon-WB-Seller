from backend.utils.logger import get_logger
from backend.schemas.product import ProductBase
from backend.services.payload_builder import PayloadBuilder
from typing import Dict, Any

logger = get_logger(__name__)

class WildberriesService:
    ENDPOINT = "https://suppliers-api.wildberries.ru/content/v2/cards/upload"
    
    @staticmethod
    def build_request(product: ProductBase, api_key: str) -> Dict[str, Any]:
        """Подготовить запрос к Wildberries API"""
        payload = PayloadBuilder.build_wb_payload(product)
        
        headers = {
            "X-API-Key": api_key,
            "Content-Type": "application/json"
        }
        
        logger.info(f"Wildberries payload built for SKU: {product.sku}")
        
        return {
            "endpoint": WildberriesService.ENDPOINT,
            "method": "POST",
            "headers": headers,
            "body": payload
        }
    
    @staticmethod
    async def send_request(product: ProductBase, api_key: str, env: str = "sandbox"):
        """В режиме prod отправить запрос к Wildberries"""
        request_data = WildberriesService.build_request(product, api_key)
        
        if env == "sandbox":
            logger.info("Running in SANDBOX mode - request not sent")
            return {"status": "sandbox", "request": request_data}
        
        logger.info(f"Sending request to Wildberries...")
        # response = requests.post(...)
        
        return {"status": "sent", "request": request_data}
