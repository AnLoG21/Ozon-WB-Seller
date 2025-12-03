from pydantic import BaseModel, Field
from typing import List, Optional

class ProductBase(BaseModel):
    sku: str = Field(..., min_length=1, description="Артикул товара")
    name: str = Field(..., min_length=1, description="Название товара")
    brand: str = Field(default="", description="Бренд")
    price: float = Field(..., ge=0, description="Цена в рублях")
    stock: int = Field(default=0, ge=0, description="Остаток в шт")
    category: str = Field(default="", description="Категория")
    description: str = Field(default="", description="Описание товара")
    images: List[str] = Field(default_factory=list, description="Ссылки на изображения")

class ProductResponse(ProductBase):
    id: Optional[str] = None

class OzonPayloadRequest(ProductBase):
    pass

class WildberriesPayloadRequest(ProductBase):
    pass
