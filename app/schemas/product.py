from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field

# Base schema for Product shared properties
class ProductBase(BaseModel):
    name: str
    category: Optional[str] = None
    unit_price: float = Field(gt=0)
    minimum_stock: int = 0
    maximum_stock: Optional[int] = None

# Schema for creating a new Product
class ProductCreate(ProductBase):
    pass

# Schema for updating an existing Product
class ProductUpdate(BaseModel):
    name: Optional[str] = None
    category: Optional[str] = None
    unit_price: Optional[float] = Field(None, gt=0)
    minimum_stock: Optional[int] = None
    maximum_stock: Optional[int] = None

# Schema for Product in DB (returned to client)
class ProductInDB(ProductBase):
    id: str
    created_at: datetime
    updated_at: datetime
    
    class Config:
        from_attributes = True

# Schema for Product with inventory data
class ProductWithInventory(ProductInDB):
    current_stock: int = 0
    locations: list[dict] = []