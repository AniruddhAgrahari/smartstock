from app.database.session import Base

# Import all models here so that Base has them registered
# The following imports are for SQLAlchemy to create the tables
from app.models.product import Product
from app.models.inventory import Inventory
from app.models.supplier import Supplier
from app.models.order import Order
from app.models.user import User
from app.models.transaction import Transaction