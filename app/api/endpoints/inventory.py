import uuid
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from datetime import datetime

from app.database.session import get_db
from app.models.inventory import Inventory
from app.models.product import Product
from app.models.transaction import Transaction, TransactionType
from app.models.user import User, UserRole
from app.api.endpoints.auth import get_current_user, check_user_role

router = APIRouter()

@router.get("/")
async def get_inventory(
    location_id: Optional[str] = None,
    product_id: Optional[str] = None,
    min_stock: Optional[int] = None, 
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get inventory items, optionally filtered by location or product.
    Can also filter to show only items below minimum stock level.
    """
    query = db.query(Inventory)
    
    if location_id:
        query = query.filter(Inventory.location_id == location_id)
    
    if product_id:
        query = query.filter(Inventory.product_id == product_id)
    
    inventory_items = query.offset(skip).limit(limit).all()
    
    # Include product details with each inventory item
    result = []
    for item in inventory_items:
        product = db.query(Product).filter(Product.id == item.product_id).first()
        
        # Skip if product is not found or not below minimum if filter is applied
        if not product or (min_stock and item.quantity >= product.minimum_stock):
            continue
            
        result.append({
            "id": item.id,
            "location_id": item.location_id,
            "product_id": item.product_id,
            "product_name": product.name if product else None,
            "category": product.category if product else None,
            "quantity": item.quantity,
            "min_stock": product.minimum_stock if product else None,
            "max_stock": product.maximum_stock if product else None,
            "unit_price": product.unit_price if product else None,
            "last_updated": item.last_updated
        })
    
    return result

@router.post("/")
async def create_or_update_inventory(
    product_id: str,
    location_id: str,
    quantity: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(check_user_role([UserRole.ADMIN, UserRole.MANAGER]))
):
    """Create or update inventory for a product at a location."""
    # Check if product exists
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Product with ID {product_id} not found"
        )
    
    # Check if inventory already exists for this product/location
    inventory = db.query(Inventory).filter(
        Inventory.product_id == product_id,
        Inventory.location_id == location_id
    ).first()
    
    # Determine transaction type and quantity change
    transaction_type = TransactionType.ADJUSTMENT
    
    if inventory:
        # Record the adjustment amount
        quantity_change = quantity - inventory.quantity
        
        # Update existing inventory
        inventory.quantity = quantity
        inventory.last_updated = datetime.utcnow()
    else:
        # Create new inventory
        inventory = Inventory(
            id=str(uuid.uuid4()),
            product_id=product_id,
            location_id=location_id,
            quantity=quantity
        )
        db.add(inventory)
        quantity_change = quantity
    
    # Create transaction record
    transaction = Transaction(
        id=str(uuid.uuid4()),
        product_id=product_id,
        quantity_change=quantity_change,
        transaction_type=transaction_type,
        reason=f"Manual inventory adjustment by {current_user.username}",
        location_id=location_id,
        timestamp=datetime.utcnow()
    )
    db.add(transaction)
    
    db.commit()
    
    return {
        "status": "success",
        "inventory_id": inventory.id,
        "product_id": product_id,
        "location_id": location_id,
        "quantity": quantity,
        "quantity_change": quantity_change,
        "transaction_id": transaction.id
    }

@router.post("/transfer")
async def transfer_inventory(
    product_id: str,
    from_location_id: str,
    to_location_id: str,
    quantity: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(check_user_role([UserRole.ADMIN, UserRole.MANAGER]))
):
    """Transfer inventory from one location to another."""
    if quantity <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Transfer quantity must be greater than zero"
        )
    
    # Check if product exists
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Product with ID {product_id} not found"
        )
    
    # Check from location inventory
    from_inventory = db.query(Inventory).filter(
        Inventory.product_id == product_id,
        Inventory.location_id == from_location_id
    ).first()
    
    if not from_inventory or from_inventory.quantity < quantity:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Insufficient inventory at source location"
        )
    
    # Check to location inventory
    to_inventory = db.query(Inventory).filter(
        Inventory.product_id == product_id,
        Inventory.location_id == to_location_id
    ).first()
    
    # Generate a common reference ID for linking the transactions
    transfer_reference = str(uuid.uuid4())
    
    # Update from location inventory
    from_inventory.quantity -= quantity
    from_inventory.last_updated = datetime.utcnow()
    
    # Create transaction for from location
    from_transaction = Transaction(
        id=str(uuid.uuid4()),
        product_id=product_id,
        quantity_change=-quantity,
        transaction_type=TransactionType.TRANSFER,
        reason=f"Transfer to {to_location_id}",
        location_id=from_location_id,
        reference_id=transfer_reference,
        timestamp=datetime.utcnow()
    )
    db.add(from_transaction)
    
    # Update or create to location inventory
    if to_inventory:
        to_inventory.quantity += quantity
        to_inventory.last_updated = datetime.utcnow()
    else:
        to_inventory = Inventory(
            id=str(uuid.uuid4()),
            product_id=product_id,
            location_id=to_location_id,
            quantity=quantity
        )
        db.add(to_inventory)
    
    # Create transaction for to location
    to_transaction = Transaction(
        id=str(uuid.uuid4()),
        product_id=product_id,
        quantity_change=quantity,
        transaction_type=TransactionType.TRANSFER,
        reason=f"Transfer from {from_location_id}",
        location_id=to_location_id,
        reference_id=transfer_reference,
        timestamp=datetime.utcnow()
    )
    db.add(to_transaction)
    
    db.commit()
    
    return {
        "status": "success",
        "product_id": product_id,
        "from_location_id": from_location_id,
        "to_location_id": to_location_id,
        "quantity": quantity,
        "transfer_reference": transfer_reference
    }

@router.get("/low-stock")
async def get_low_stock_inventory(
    location_id: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get inventory items that are below their minimum stock level."""
    # This query is more complex as it involves joining inventory with products
    # and filtering based on product's minimum_stock field
    query = db.query(Inventory, Product).join(
        Product, Product.id == Inventory.product_id
    ).filter(
        Inventory.quantity < Product.minimum_stock
    )
    
    if location_id:
        query = query.filter(Inventory.location_id == location_id)
    
    results = query.all()
    
    # Format the response
    low_stock_items = []
    for inventory, product in results:
        low_stock_items.append({
            "id": inventory.id,
            "location_id": inventory.location_id,
            "product_id": product.id,
            "product_name": product.name,
            "category": product.category,
            "current_quantity": inventory.quantity,
            "minimum_stock": product.minimum_stock,
            "shortage": product.minimum_stock - inventory.quantity,
            "last_updated": inventory.last_updated
        })
    
    return low_stock_items

@router.get("/transactions")
async def get_inventory_transactions(
    product_id: Optional[str] = None,
    location_id: Optional[str] = None,
    transaction_type: Optional[TransactionType] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get inventory transactions with various filters."""
    query = db.query(Transaction)
    
    if product_id:
        query = query.filter(Transaction.product_id == product_id)
    
    if location_id:
        query = query.filter(Transaction.location_id == location_id)
    
    if transaction_type:
        query = query.filter(Transaction.transaction_type == transaction_type)
    
    if start_date:
        query = query.filter(Transaction.timestamp >= start_date)
    
    if end_date:
        query = query.filter(Transaction.timestamp <= end_date)
    
    # Sort by timestamp descending (most recent first)
    query = query.order_by(Transaction.timestamp.desc())
    
    transactions = query.limit(limit).all()
    
    # Format response with product details
    results = []
    for transaction in transactions:
        product = db.query(Product).filter(Product.id == transaction.product_id).first()
        
        results.append({
            "id": transaction.id,
            "product_id": transaction.product_id,
            "product_name": product.name if product else None,
            "quantity_change": transaction.quantity_change,
            "transaction_type": transaction.transaction_type,
            "reason": transaction.reason,
            "location_id": transaction.location_id,
            "reference_id": transaction.reference_id,
            "timestamp": transaction.timestamp
        })
    
    return results