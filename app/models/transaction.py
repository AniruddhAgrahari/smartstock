from sqlalchemy import Column, String, Integer, DateTime, ForeignKey, Enum
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
import enum

from app.database.session import Base

class TransactionType(str, enum.Enum):
    PURCHASE = "purchase"
    SALE = "sale"
    ADJUSTMENT = "adjustment"
    TRANSFER = "transfer"
    RETURN = "return"

class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(String, primary_key=True, index=True)
    product_id = Column(String, ForeignKey("products.id"), nullable=False)
    quantity_change = Column(Integer, nullable=False)  # Positive for additions, negative for removals
    transaction_type = Column(Enum(TransactionType), nullable=False)
    reason = Column(String)
    location_id = Column(String, nullable=False)
    reference_id = Column(String, nullable=True)  # Could reference an order ID, etc.
    timestamp = Column(DateTime, default=func.now())
    
    # Relationships
    product = relationship("Product")