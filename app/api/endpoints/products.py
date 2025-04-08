import uuid
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.models.product import Product
from app.models.inventory import Inventory
from app.models.user import User, UserRole
from app.schemas.product import ProductCreate, ProductUpdate, ProductInDB, ProductWithInventory
from app.api.endpoints.auth import get_current_user, check_user_role

router = APIRouter()

@router.post("/", response_model=ProductInDB, status_code=status.HTTP_201_CREATED)
async def create_product(
    product: ProductCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(check_user_role([UserRole.ADMIN, UserRole.MANAGER]))
):
    """Create a new product."""
    # Check if product with same name already exists
    existing_product = db.query(Product).filter(Product.name == product.name).first()
    if existing_product:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Product with name '{product.name}' already exists"
        )
    
    # Create new product
    db_product = Product(
        id=str(uuid.uuid4()),
        name=product.name,
        category=product.category,
        unit_price=product.unit_price,
        minimum_stock=product.minimum_stock,
        maximum_stock=product.maximum_stock or (product.minimum_stock * 3)
    )
    
    db.add(db_product)
    db.commit()
    db.refresh(db_product)
    
    return db_product

@router.get("/", response_model=List[ProductInDB])
async def get_products(
    skip: int = 0,
    limit: int = 100,
    category: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get all products, optionally filtered by category."""
    query = db.query(Product)
    
    if category:
        query = query.filter(Product.category == category)
    
    products = query.offset(skip).limit(limit).all()
    return products

@router.get("/{product_id}", response_model=ProductWithInventory)
async def get_product(
    product_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get a specific product by ID, including inventory information."""
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Product with ID {product_id} not found"
        )
    
    # Get inventory information for this product
    inventory_items = db.query(Inventory).filter(Inventory.product_id == product_id).all()
    
    # Calculate total current stock
    total_stock = sum(item.quantity for item in inventory_items)
    
    # Get inventory by location
    locations = [
        {
            "location_id": item.location_id,
            "quantity": item.quantity,
            "last_updated": item.last_updated
        }
        for item in inventory_items
    ]
    
    # Convert to ProductWithInventory
    product_with_inventory = ProductWithInventory(
        **product.__dict__,
        current_stock=total_stock,
        locations=locations
    )
    
    return product_with_inventory

@router.put("/{product_id}", response_model=ProductInDB)
async def update_product(
    product_id: str,
    product_update: ProductUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(check_user_role([UserRole.ADMIN, UserRole.MANAGER]))
):
    """Update a product."""
    db_product = db.query(Product).filter(Product.id == product_id).first()
    if not db_product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Product with ID {product_id} not found"
        )
    
    # Update product attributes
    update_data = product_update.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(db_product, key, value)
    
    db.commit()
    db.refresh(db_product)
    
    return db_product

@router.delete("/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_product(
    product_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(check_user_role([UserRole.ADMIN]))
):
    """Delete a product (admin only)."""
    db_product = db.query(Product).filter(Product.id == product_id).first()
    if not db_product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Product with ID {product_id} not found"
        )
    
    # Check if product has inventory
    inventory = db.query(Inventory).filter(Inventory.product_id == product_id).first()
    if inventory:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot delete product with existing inventory. Remove inventory first."
        )
    
    # Delete the product
    db.delete(db_product)
    db.commit()
    
    return None

@router.get("/search/", response_model=List[ProductInDB])
async def search_products(
    query: str = Query(..., min_length=2),
    limit: int = 20,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Search for products by name or category."""
    search_term = f"%{query}%"
    products = db.query(Product).filter(
        (Product.name.ilike(search_term)) | (Product.category.ilike(search_term))
    ).limit(limit).all()
    
    return products