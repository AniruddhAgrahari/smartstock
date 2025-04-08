from sqlalchemy import Column, String, Float, Integer, DateTime
from sqlalchemy.sql import func

from app.database.session import Base

class Supplier(Base):
    __tablename__ = "suppliers"

    id = Column(String, primary_key=True, index=True)
    name = Column(String, nullable=False)
    contact = Column(String)
    lead_time = Column(Integer)  # Average lead time in days
    reliability_score = Column(Float, default=0.0)  # Score from 0 to 1
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())