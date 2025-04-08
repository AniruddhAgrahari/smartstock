import uuid
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from sqlalchemy import func, desc, text
from datetime import datetime, timedelta

from app.database.session import get_db
from app.models.user import User, UserRole
from app.models.product import Product
from app.models.inventory import Inventory
from app.models.transaction import Transaction, TransactionType
from app.models.order import Order, OrderStatus
from app.api.endpoints.auth import get_current_user, check_user_role
from app.services.optimization.inventory import InventoryOptimizer

router = APIRouter()

# Initialize the inventory optimizer
inventory_optimizer = InventoryOptimizer()

@router.get("/overview")
async def get_dashboard_overview(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get an overview of key metrics for the dashboard."""
    # Total product count
    total_products = db.query(func.count(Product.id)).scalar()
    
    # Total inventory value
    inventory_value_query = db.query(
        func.sum(Inventory.quantity * Product.unit_price)
    ).join(
        Product, Product.id == Inventory.product_id
    )
    total_inventory_value = inventory_value_query.scalar() or 0
    
    # Low stock count
    low_stock_query = db.query(
        func.count(Inventory.id)
    ).join(
        Product, Product.id == Inventory.product_id
    ).filter(
        Inventory.quantity < Product.minimum_stock
    )
    low_stock_count = low_stock_query.scalar() or 0
    
    # Orders in progress
    orders_in_progress = db.query(
        func.count(Order.id)
    ).filter(
        Order.status.in_([OrderStatus.PENDING, OrderStatus.CONFIRMED, OrderStatus.SHIPPED])
    ).scalar() or 0
    
    # Recent transactions count (last 7 days)
    week_ago = datetime.utcnow() - timedelta(days=7)
    recent_transactions = db.query(
        func.count(Transaction.id)
    ).filter(
        Transaction.timestamp >= week_ago
    ).scalar() or 0
    
    # Locations count (distinct location_ids from inventory)
    locations_count = db.query(
        func.count(func.distinct(Inventory.location_id))
    ).scalar() or 0
    
    return {
        "total_products": total_products,
        "total_inventory_value": round(total_inventory_value, 2),
        "low_stock_count": low_stock_count,
        "orders_in_progress": orders_in_progress,
        "recent_transactions": recent_transactions,
        "locations_count": locations_count,
        "timestamp": datetime.utcnow()
    }

@router.get("/inventory-summary")
async def get_inventory_summary(
    location_id: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get inventory summary by location or product category."""
    # Base query
    base_query = db.query(
        Inventory.location_id,
        func.sum(Inventory.quantity).label("total_quantity"),
        func.sum(Inventory.quantity * Product.unit_price).label("total_value"),
        func.count(func.distinct(Inventory.product_id)).label("product_count")
    ).join(
        Product, Product.id == Inventory.product_id
    )
    
    # Apply location filter if provided
    if location_id:
        base_query = base_query.filter(Inventory.location_id == location_id)
    
    # Group by location
    location_summary = base_query.group_by(
        Inventory.location_id
    ).all()
    
    # Format the results
    result = []
    for location in location_summary:
        result.append({
            "location_id": location.location_id,
            "total_quantity": location.total_quantity,
            "total_value": round(location.total_value, 2),
            "product_count": location.product_count
        })
    
    # Get category breakdown
    category_query = db.query(
        Product.category,
        func.sum(Inventory.quantity).label("total_quantity"),
        func.sum(Inventory.quantity * Product.unit_price).label("total_value"),
        func.count(func.distinct(Inventory.product_id)).label("product_count")
    ).join(
        Inventory, Inventory.product_id == Product.id
    )
    
    # Apply location filter if provided
    if location_id:
        category_query = category_query.filter(Inventory.location_id == location_id)
    
    # Group by category
    category_summary = category_query.group_by(
        Product.category
    ).all()
    
    # Format category results
    categories = []
    for category in category_summary:
        if not category.category:  # Skip null categories
            continue
        categories.append({
            "category": category.category,
            "total_quantity": category.total_quantity,
            "total_value": round(category.total_value, 2),
            "product_count": category.product_count
        })
    
    return {
        "locations": result,
        "categories": categories,
        "timestamp": datetime.utcnow()
    }

@router.get("/transaction-trends")
async def get_transaction_trends(
    days: int = 30,
    product_id: Optional[str] = None,
    location_id: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get transaction trends over time."""
    # Calculate start date
    start_date = datetime.utcnow() - timedelta(days=days)
    
    # Base query for daily transactions
    base_query = db.query(
        func.date_trunc('day', Transaction.timestamp).label('day'),
        func.sum(
            func.case(
                [(Transaction.quantity_change < 0, func.abs(Transaction.quantity_change))],
                else_=0
            )
        ).label('sales'),
        func.sum(
            func.case(
                [(Transaction.quantity_change > 0, Transaction.quantity_change)],
                else_=0
            )
        ).label('purchases')
    ).filter(
        Transaction.timestamp >= start_date
    )
    
    # Apply filters if provided
    if product_id:
        base_query = base_query.filter(Transaction.product_id == product_id)
    
    if location_id:
        base_query = base_query.filter(Transaction.location_id == location_id)
    
    # Group by day
    daily_transactions = base_query.group_by(
        func.date_trunc('day', Transaction.timestamp)
    ).order_by(
        func.date_trunc('day', Transaction.timestamp)
    ).all()
    
    # Format results
    result = []
    for day in daily_transactions:
        result.append({
            "date": day.day.date().isoformat(),
            "sales": int(day.sales or 0),
            "purchases": int(day.purchases or 0)
        })
    
    # Get top product transactions
    top_products_query = db.query(
        Transaction.product_id,
        Product.name.label("product_name"),
        func.sum(func.abs(Transaction.quantity_change)).label("total_volume")
    ).join(
        Product, Product.id == Transaction.product_id
    ).filter(
        Transaction.timestamp >= start_date
    )
    
    if location_id:
        top_products_query = top_products_query.filter(Transaction.location_id == location_id)
    
    top_products = top_products_query.group_by(
        Transaction.product_id, Product.name
    ).order_by(
        func.sum(func.abs(Transaction.quantity_change)).desc()
    ).limit(5).all()
    
    # Format top products
    top_products_result = []
    for product in top_products:
        top_products_result.append({
            "product_id": product.product_id,
            "product_name": product.product_name,
            "total_volume": int(product.total_volume or 0)
        })
    
    return {
        "daily_transactions": result,
        "top_products": top_products_result,
        "period_days": days,
        "timestamp": datetime.utcnow()
    }

@router.get("/optimization-recommendations")
async def get_optimization_recommendations(
    location_id: Optional[str] = None,
    limit: int = 10,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get inventory optimization recommendations."""
    # Get inventory items to analyze
    query = db.query(
        Inventory, Product
    ).join(
        Product, Product.id == Inventory.product_id
    )
    
    if location_id:
        query = query.filter(Inventory.location_id == location_id)
    
    inventory_items = query.all()
    
    recommendations = []
    transfer_recommendations = []
    
    # Collect inventory by location for transfer analysis
    locations_inventory = {}
    
    for inventory, product in inventory_items:
        # Dummy forecast data since we don't have real forecasts for each product here
        # In a real implementation, we would use the forecasting service
        dummy_forecast = pd.DataFrame({
            "date": pd.date_range(start=datetime.now(), periods=30),
            "forecast": [5] * 30,  # Assume constant daily demand of 5 units
            "lower_bound": [3] * 30,
            "upper_bound": [7] * 30
        })
        
        # Get a random supplier for demo purposes
        supplier = db.query(app.models.supplier.Supplier).first()
        supplier_data = {
            "lead_time": supplier.lead_time if supplier else 7,
            "reliability_score": supplier.reliability_score if supplier else 0.9
        }
        
        # Business constraints
        constraints = {
            "service_level": 0.95,
            "budget": 10000
        }
        
        # Get inventory optimization results
        product_data = {
            "id": product.id,
            "name": product.name,
            "unit_price": product.unit_price,
            "current_stock": inventory.quantity,
            "minimum_stock": product.minimum_stock,
            "maximum_stock": product.maximum_stock
        }
        
        optimization_result = inventory_optimizer.optimize_inventory_levels(
            product_data, dummy_forecast, supplier_data, constraints
        )
        
        # Check if optimization generated an order recommendation
        if optimization_result.get("order_recommendation", 0) > 0:
            recommendations.append({
                "product_id": product.id,
                "product_name": product.name,
                "location_id": inventory.location_id,
                "current_stock": inventory.quantity,
                "reorder_point": optimization_result.get("reorder_point", 0),
                "order_recommendation": optimization_result.get("order_recommendation", 0),
                "days_of_supply": optimization_result.get("days_of_supply", 0),
                "stockout_probability": optimization_result.get("stockout_probability", 0)
            })
        
        # Collect inventory data by location for transfer analysis
        if inventory.location_id not in locations_inventory:
            locations_inventory[inventory.location_id] = {
                "location_id": inventory.location_id,
                "inventory": []
            }
        
        locations_inventory[inventory.location_id]["inventory"].append({
            "product_id": product.id,
            "quantity": inventory.quantity
        })
    
    # Generate transfer recommendations if we have multiple locations
    if len(locations_inventory) > 1:
        # Collect forecast data by location and product
        forecast_data = {}
        for location_id in locations_inventory:
            forecast_data[location_id] = {}
            for item in locations_inventory[location_id]["inventory"]:
                # Dummy forecast for each product
                forecast_data[location_id][item["product_id"]] = pd.DataFrame({
                    "date": pd.date_range(start=datetime.now(), periods=30),
                    "forecast": [5] * 30  # Assume constant daily demand
                })
        
        # Generate transfer recommendations
        transfer_recommendations = inventory_optimizer.generate_transfer_recommendations(
            list(locations_inventory.values()),
            forecast_data
        )
    
    # Sort recommendations by stockout probability (highest first)
    recommendations.sort(key=lambda x: x["stockout_probability"], reverse=True)
    
    return {
        "order_recommendations": recommendations[:limit],
        "transfer_recommendations": transfer_recommendations[:limit],
        "timestamp": datetime.utcnow()
    }

@router.get("/order-status")
async def get_order_status_summary(
    days: int = 30,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get summary of order statuses."""
    # Calculate start date
    start_date = datetime.utcnow() - timedelta(days=days)
    
    # Count orders by status
    status_counts = db.query(
        Order.status,
        func.count(Order.id).label("count")
    ).filter(
        Order.created_at >= start_date
    ).group_by(
        Order.status
    ).all()
    
    # Format status counts
    status_summary = {}
    for status in OrderStatus:
        status_summary[status.value] = 0
    
    for status, count in status_counts:
        status_summary[status.value] = count
    
    # Get recent orders
    recent_orders = db.query(Order).filter(
        Order.created_at >= start_date
    ).order_by(
        Order.created_at.desc()
    ).limit(5).all()
    
    # Format recent orders
    recent_orders_result = []
    for order in recent_orders:
        # Get supplier name
        supplier = db.query(app.models.supplier.Supplier).filter(
            app.models.supplier.Supplier.id == order.supplier_id
        ).first()
        
        # Get order items
        items_count = db.query(func.count(app.models.order_item.OrderItem.id)).filter(
            app.models.order_item.OrderItem.order_id == order.id
        ).scalar() or 0
        
        recent_orders_result.append({
            "order_id": order.id,
            "supplier_name": supplier.name if supplier else "Unknown",
            "status": order.status.value,
            "total_amount": round(order.total_amount, 2),
            "created_at": order.created_at,
            "delivery_date": order.delivery_date,
            "items_count": items_count
        })
    
    return {
        "status_summary": status_summary,
        "recent_orders": recent_orders_result,
        "period_days": days,
        "timestamp": datetime.utcnow()
    }