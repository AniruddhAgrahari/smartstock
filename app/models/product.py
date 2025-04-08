from sqlalchemy import Column, String, Float, Integer, DateTime
from sqlalchemy.sql import func

from app.database.session import Base

class Product(Base):
    __tablename__ = "products"

    id = Column(String, primary_key=True, index=True)
    name = Column(String, nullable=False)
    category = Column(String, index=True)
    unit_price = Column(Float, nullable=False)
    minimum_stock = Column(Integer, default=0)
    maximum_stock = Column(Integer)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())