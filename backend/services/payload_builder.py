from typing import Dict, Any, List
from backend.schemas.product import ProductBase

class PayloadBuilder:
    @staticmethod
    def build_ozon_payload(product: ProductBase) -> Dict[str, Any]:
        """Построить payload для Ozon API /v2/product/import"""
        return {
            "items": [
                {
                    "offer_id": product.sku,
                    "name": product.name,
                    "brand": product.brand,
                    "price": str(round(product.price, 2)),
                    "description": product.description,
                    "images": [{"file_name": url} for url in product.images],
                    "attributes": [],
                    "vat": "0"
                }
            ]
        }
    
    @staticmethod
    def build_wb_payload(product: ProductBase) -> Dict[str, Any]:
        """Построить payload для Wildberries API content/v2/cards/upload"""
        return [
            {
                "vendorCode": product.sku,
                "brand": product.brand,
                "title": product.name,
                "description": product.description,
                "sizes": [
                    {
                        "skus": [product.sku],
                        "price": int(product.price * 100),  # WB в копейках
                        "stocks": [
                            {
                                "warehouseId": 1,  # Основской склад
                                "quantity": product.stock
                            }
                        ]
                    }
                ],
                "mediaFiles": product.images
            }
        ]
