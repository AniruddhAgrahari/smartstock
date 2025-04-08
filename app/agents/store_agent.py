import logging
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List
import uuid
import asyncio
import random

from app.agents.base import Agent
from app.models.product import Product
from app.models.inventory import Inventory
from app.models.transaction import Transaction, TransactionType
from app.models.order import Order, OrderStatus
from app.models.order_item import OrderItem
from app.services.forecasting.engine import ForecastingEngine

# Configure logging
logger = logging.getLogger(__name__)

class StoreAgent(Agent):
    """
    Store Agent: Handles store-level inventory management, sales processing, and automatic reordering
    """
    
    def __init__(self, store_id: str, db_session, forecasting_engine: Optional[ForecastingEngine] = None):
        super().__init__(f"store_agent_{store_id}", db_session)
        self.store_id = store_id
        self.pending_transactions = {}  # Track sales transactions
        self.pending_orders = {}  # Track customer orders
        self.pending_replenishments = {}  # Track replenishment requests to warehouse
        self.forecasting_engine = forecasting_engine or ForecastingEngine(db_session)
        
    async def process_message(self, message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Process incoming messages based on message type."""
        message_type = message.get("message_type")
        
        if message_type == "SALE_REQUEST":
            # Handle sale request
            return await self._handle_sale_request(message)
        elif message_type == "INVENTORY_UPDATE":
            # Handle inventory update from warehouse
            return await self._handle_inventory_update(message)
        elif message_type == "ORDER_UPDATE":
            # Handle customer order update
            return await self._handle_order_update(message)
        elif message_type == "TRANSFER_FAILED":
            # Handle failed transfer from warehouse
            return await self._handle_transfer_failed(message)
        
        logger.warning(f"Store agent received unknown message type: {message_type}")
        return None
    
    async def run_cycle(self):
        """Run one cycle of store agent operations."""
        # Process any pending sales
        await self._process_pending_sales()
        
        # Check inventory levels and reorder if needed
        await self._check_inventory_levels()
        
        # Update orders in progress
        await self._update_orders()
        
        # Sleep to avoid overloading the system
        await asyncio.sleep(1)
    
    async def _handle_sale_request(self, message: Dict[str, Any]) -> Dict[str, Any]:
        """
        Handle sale request from a point-of-sale system.
        
        Expected message format:
        {
            "message_type": "SALE_REQUEST",
            "content": {
                "transaction_id": "uuid",
                "items": [
                    {
                        "product_id": "uuid",
                        "quantity": 2,
                        "unit_price": 10.99
                    }
                ],
                "customer_id": "customer_123",  # Optional
                "timestamp": "2023-01-01T12:00:00"
            }
        }
        """
        content = message.get("content", {})
        transaction_id = content.get("transaction_id")
        items = content.get("items", [])
        customer_id = content.get("customer_id")
        timestamp = content.get("timestamp", datetime.utcnow().isoformat())
        
        if not all([transaction_id, items]):
            return {
                "status": "error",
                "message": "Invalid sale request: transaction_id and items required"
            }
        
        # Check inventory for all items
        insufficient_items = []
        valid_items = []
        
        for item in items:
            product_id = item.get("product_id")
            quantity = item.get("quantity", 0)
            
            if not product_id or quantity <= 0:
                continue
            
            # Check store inventory
            inventory = self.db.query(Inventory).filter(
                Inventory.product_id == product_id,
                Inventory.location_id == self.store_id
            ).first()
            
            if not inventory or inventory.quantity < quantity:
                insufficient_items.append({
                    "product_id": product_id,
                    "requested": quantity,
                    "available": inventory.quantity if inventory else 0
                })
            else:
                valid_items.append(item)
        
        if insufficient_items:
            # Return error with insufficient items
            return {
                "status": "insufficient_inventory",
                "message": "Insufficient inventory for some items",
                "insufficient_items": insufficient_items
            }
        
        if not valid_items:
            return {
                "status": "error",
                "message": "No valid items in sale request"
            }
        
        # Store the transaction for processing
        try:
            transaction_time = datetime.fromisoformat(timestamp)
        except ValueError:
            transaction_time = datetime.utcnow()
            
        self.pending_transactions[transaction_id] = {
            "transaction_id": transaction_id,
            "items": valid_items,
            "customer_id": customer_id,
            "timestamp": transaction_time,
            "status": "pending"
        }
        
        # Process the sale immediately
        result = await self._process_sale(transaction_id)
        
        if result.get("status") == "success":
            return {
                "status": "success",
                "transaction_id": transaction_id,
                "message": "Sale processed successfully"
            }
        else:
            return result
    
    async def _handle_inventory_update(self, message: Dict[str, Any]) -> Dict[str, Any]:
        """Handle inventory update from warehouse."""
        content = message.get("content", {})
        product_id = content.get("product_id")
        quantity = content.get("quantity", 0)
        update_type = content.get("update_type", "ADD")  # ADD or REMOVE
        reason = content.get("reason", "Warehouse transfer")
        request_id = content.get("request_id")
        transfer_id = content.get("transfer_id")
        
        if not all([product_id, quantity]):
            return {
                "status": "error",
                "message": "Invalid inventory update: product_id and quantity required"
            }
        
        # Update store inventory
        inventory = self.db.query(Inventory).filter(
            Inventory.product_id == product_id,
            Inventory.location_id == self.store_id
        ).first()
        
        if inventory:
            old_quantity = inventory.quantity
            
            if update_type == "ADD":
                inventory.quantity += quantity
            elif update_type == "REMOVE":
                inventory.quantity = max(0, inventory.quantity - quantity)
            
            inventory.last_updated = datetime.utcnow()
        else:
            # Create new inventory record if it doesn't exist
            if update_type == "ADD":
                inventory = Inventory(
                    id=str(uuid.uuid4()),
                    product_id=product_id,
                    location_id=self.store_id,
                    quantity=quantity
                )
                self.db.add(inventory)
                old_quantity = 0
            else:
                return {
                    "status": "error",
                    "message": f"Cannot remove inventory for non-existent product: {product_id}"
                }
        
        # Record transaction
        transaction_type = TransactionType.DELIVERY if update_type == "ADD" else TransactionType.TRANSFER
        reference_id = request_id or transfer_id or str(uuid.uuid4())
        
        transaction = Transaction(
            id=str(uuid.uuid4()),
            product_id=product_id,
            quantity_change=quantity if update_type == "ADD" else -quantity,
            transaction_type=transaction_type,
            reason=reason,
            location_id=self.store_id,
            reference_id=reference_id,
            timestamp=datetime.utcnow()
        )
        self.db.add(transaction)
        
        try:
            self.db.commit()
            logger.info(f"Updated inventory for product {product_id} at store {self.store_id}: {old_quantity} -> {inventory.quantity}")
            
            # If this was a replenishment request, update its status
            if request_id and request_id in self.pending_replenishments:
                self.pending_replenishments[request_id]["status"] = "fulfilled"
                self.pending_replenishments[request_id]["fulfilled_date"] = datetime.utcnow()
            
            return {
                "status": "success",
                "product_id": product_id,
                "new_quantity": inventory.quantity,
                "message": f"Inventory updated successfully"
            }
            
        except Exception as e:
            self.db.rollback()
            logger.error(f"Error updating inventory: {str(e)}")
            return {
                "status": "error",
                "message": f"Error updating inventory: {str(e)}"
            }
    
    async def _handle_order_update(self, message: Dict[str, Any]) -> Dict[str, Any]:
        """Handle customer order update."""
        content = message.get("content", {})
        order_id = content.get("order_id")
        status = content.get("status")
        
        if not all([order_id, status]):
            return {
                "status": "error",
                "message": "Invalid order update: order_id and status required"
            }
        
        # Update order in database
        order = self.db.query(Order).filter(Order.id == order_id).first()
        
        if not order:
            return {
                "status": "error",
                "message": f"Unknown order: {order_id}"
            }
        
        try:
            # Update order status
            order.status = OrderStatus[status]
            order.updated_at = datetime.utcnow()
            
            # If order is cancelled, return items to inventory
            if status == "CANCELLED" and order.status != OrderStatus.DELIVERED:
                order_items = self.db.query(OrderItem).filter(OrderItem.order_id == order_id).all()
                
                for order_item in order_items:
                    # Return to inventory
                    inventory = self.db.query(Inventory).filter(
                        Inventory.product_id == order_item.product_id,
                        Inventory.location_id == self.store_id
                    ).first()
                    
                    if inventory:
                        inventory.quantity += order_item.quantity
                        
                        # Record transaction
                        transaction = Transaction(
                            id=str(uuid.uuid4()),
                            product_id=order_item.product_id,
                            quantity_change=order_item.quantity,
                            transaction_type=TransactionType.RETURN,
                            reason=f"Order cancelled: {order_id}",
                            location_id=self.store_id,
                            reference_id=order_id,
                            timestamp=datetime.utcnow()
                        )
                        self.db.add(transaction)
            
            self.db.commit()
            logger.info(f"Updated order {order_id} status to {status}")
            
            return {
                "status": "success",
                "order_id": order_id,
                "message": f"Order updated successfully"
            }
            
        except Exception as e:
            self.db.rollback()
            logger.error(f"Error updating order: {str(e)}")
            return {
                "status": "error",
                "message": f"Error updating order: {str(e)}"
            }
    
    async def _handle_transfer_failed(self, message: Dict[str, Any]) -> Dict[str, Any]:
        """Handle failed transfer from warehouse."""
        content = message.get("content", {})
        transfer_id = content.get("transfer_id")
        product_id = content.get("product_id")
        reason = content.get("reason", "Unknown reason")
        
        if not all([transfer_id, product_id]):
            return {
                "status": "error",
                "message": "Invalid transfer failed notification: transfer_id and product_id required"
            }
        
        # Check if this was a replenishment request
        for request_id, request in list(self.pending_replenishments.items()):
            if request.get("reference_id") == transfer_id:
                # Mark as failed
                request["status"] = "failed"
                request["failure_reason"] = reason
                
                logger.warning(f"Replenishment request {request_id} failed: {reason}")
                
                # Retry with lower priority if appropriate
                if "retry_count" not in request or request["retry_count"] < 3:
                    # Increment retry count
                    request["retry_count"] = request.get("retry_count", 0) + 1
                    
                    # Lower priority
                    priority = "MEDIUM" if request.get("priority") == "HIGH" else "LOW"
                    
                    # Schedule a retry
                    await self._request_replenishment(
                        product_id=product_id,
                        quantity=request["quantity"],
                        priority=priority,
                        is_retry=True,
                        original_request_id=request_id
                    )
                
                return {
                    "status": "acknowledged",
                    "message": f"Transfer failure processed for request {request_id}"
                }
        
        logger.warning(f"Transfer failure for unknown request: {transfer_id}")
        
        return {
            "status": "acknowledged",
            "message": "Transfer failure acknowledged"
        }
    
    async def _process_sale(self, transaction_id: str) -> Dict[str, Any]:
        """
        Process a sale transaction.
        
        Updates inventory and records transaction. 
        Returns result dictionary with success or error status.
        """
        if transaction_id not in self.pending_transactions:
            return {
                "status": "error",
                "message": f"Unknown transaction: {transaction_id}"
            }
        
        transaction = self.pending_transactions[transaction_id]
        items = transaction["items"]
        
        # Begin transaction
        try:
            for item in items:
                product_id = item["product_id"]
                quantity = item["quantity"]
                
                # Get inventory
                inventory = self.db.query(Inventory).filter(
                    Inventory.product_id == product_id,
                    Inventory.location_id == self.store_id
                ).first()
                
                if not inventory or inventory.quantity < quantity:
                    self.db.rollback()
                    return {
                        "status": "error",
                        "message": f"Insufficient inventory for product {product_id}"
                    }
                
                # Update inventory
                inventory.quantity -= quantity
                inventory.last_updated = datetime.utcnow()
                
                # Record transaction
                db_transaction = Transaction(
                    id=str(uuid.uuid4()),
                    product_id=product_id,
                    quantity_change=-quantity,
                    transaction_type=TransactionType.SALE,
                    reason=f"Sale {transaction_id}",
                    location_id=self.store_id,
                    reference_id=transaction_id,
                    timestamp=transaction["timestamp"]
                )
                self.db.add(db_transaction)
            
            # Commit changes
            self.db.commit()
            
            # Update transaction status
            transaction["status"] = "completed"
            
            logger.info(f"Processed sale transaction {transaction_id} with {len(items)} items")
            
            # Check if we need to reorder any of the sold items
            for item in items:
                await self._check_reorder_needed(item["product_id"])
            
            return {
                "status": "success",
                "transaction_id": transaction_id,
                "message": "Sale processed successfully"
            }
            
        except Exception as e:
            self.db.rollback()
            logger.error(f"Error processing sale: {str(e)}")
            
            return {
                "status": "error",
                "message": f"Error processing sale: {str(e)}"
            }
    
    async def _process_pending_sales(self):
        """Process any pending sales transactions."""
        # Process up to 5 pending transactions per cycle
        pending_ids = [tid for tid, t in self.pending_transactions.items() if t["status"] == "pending"]
        
        for i, transaction_id in enumerate(pending_ids[:5]):
            await self._process_sale(transaction_id)
            
        # Clean up old completed transactions
        now = datetime.utcnow()
        for transaction_id in list(self.pending_transactions.keys()):
            transaction = self.pending_transactions[transaction_id]
            if transaction["status"] == "completed" and now - transaction["timestamp"] > timedelta(days=1):
                del self.pending_transactions[transaction_id]
    
    async def _check_inventory_levels(self):
        """Check inventory levels and request replenishment if needed."""
        # Get all inventory items for this store
        inventory_items = self.db.query(Inventory, Product).join(
            Product, Product.id == Inventory.product_id
        ).filter(
            Inventory.location_id == self.store_id
        ).all()
        
        for inventory, product in inventory_items:
            # Use forecasting to predict demand over the next lead time
            lead_time_days = 3  # Assume 3 days lead time for replenishment
            predicted_demand = await self.forecasting_engine.forecast_demand(
                product_id=product.id,
                location_id=self.store_id,
                days=lead_time_days
            )
            
            # Calculate safety stock (20% of monthly demand or minimum 2 units)
            safety_stock = max(2, int(predicted_demand * 0.2))
            
            # Check if current inventory plus pending replenishments is below reorder point
            reorder_point = predicted_demand + safety_stock
            
            # Count pending replenishments
            pending_qty = sum(
                req["quantity"] for req in self.pending_replenishments.values()
                if req["product_id"] == product.id and req["status"] == "pending"
            )
            
            current_plus_pending = inventory.quantity + pending_qty
            
            if current_plus_pending < reorder_point:
                # Calculate order quantity (reorder up to 2x the reorder point)
                order_qty = (reorder_point * 2) - current_plus_pending
                
                # Set priority based on urgency
                if inventory.quantity < safety_stock:
                    priority = "HIGH"  # Urgent - below safety stock
                elif inventory.quantity < predicted_demand:
                    priority = "MEDIUM"  # Medium - below predicted demand but above safety stock
                else:
                    priority = "LOW"  # Low - above predicted demand but below reorder point
                
                # Request replenishment from warehouse
                await self._request_replenishment(
                    product_id=product.id,
                    quantity=order_qty,
                    priority=priority
                )
    
    async def _request_replenishment(
        self, 
        product_id: str, 
        quantity: int, 
        priority: str = "MEDIUM",
        is_retry: bool = False,
        original_request_id: Optional[str] = None
    ) -> str:
        """Request inventory replenishment from warehouse."""
        request_id = original_request_id if is_retry else str(uuid.uuid4())
        
        # Store the request
        if request_id not in self.pending_replenishments:
            self.pending_replenishments[request_id] = {
                "request_id": request_id,
                "product_id": product_id,
                "quantity": quantity,
                "priority": priority,
                "status": "pending",
                "timestamp": datetime.utcnow(),
                "retry_count": 1 if is_retry else 0,
                "reference_id": None  # Will be filled when we get a response
            }
        
        # Send request to warehouse
        await self.send_message({
            "sender": self.agent_id,
            "recipient": "warehouse_agent_main",  # Assuming "main" is the default warehouse
            "message_type": "REPLENISHMENT_REQUEST",
            "content": {
                "request_id": request_id,
                "product_id": product_id,
                "quantity": quantity,
                "store_id": self.store_id,
                "priority": priority
            }
        })
        
        logger.info(f"Sent replenishment request {request_id} for {quantity} units of product {product_id}")
        
        return request_id
    
    async def _check_reorder_needed(self, product_id: str):
        """Check if immediate reordering is needed for a product after a sale."""
        inventory = self.db.query(Inventory).filter(
            Inventory.product_id == product_id,
            Inventory.location_id == self.store_id
        ).first()
        
        if not inventory:
            return
        
        product = self.db.query(Product).filter(Product.id == product_id).first()
        if not product:
            return
        
        # If inventory is below the critical threshold, request immediate replenishment
        if inventory.quantity <= product.minimum_stock:
            # Calculate how much to order (4x minimum stock or at least 10 units)
            order_qty = max(10, product.minimum_stock * 4)
            
            # Check if there's already a pending request
            pending_qty = sum(
                req["quantity"] for req in self.pending_replenishments.values()
                if req["product_id"] == product_id and req["status"] == "pending"
            )
            
            if pending_qty == 0:
                # No pending requests, create a high priority one
                await self._request_replenishment(
                    product_id=product_id,
                    quantity=order_qty,
                    priority="HIGH"
                )
    
    async def _update_orders(self):
        """Update orders in progress with latest status."""
        # Get all in-progress orders for this store
        orders = self.db.query(Order).filter(
            Order.location_id == self.store_id,
            Order.status.in_([OrderStatus.PROCESSING, OrderStatus.READY_FOR_PICKUP])
        ).all()
        
        now = datetime.utcnow()
        
        for order in orders:
            # Simulate order progress
            if order.status == OrderStatus.PROCESSING:
                # Orders in processing for more than 30 minutes move to ready for pickup
                time_in_processing = now - order.updated_at if order.updated_at else now - order.created_at
                
                if time_in_processing > timedelta(minutes=30):
                    order.status = OrderStatus.READY_FOR_PICKUP
                    order.updated_at = now
                    
                    # TODO: Send notification to customer that order is ready
            
            # Orders ready for pickup for more than 24 hours are considered abandoned
            elif order.status == OrderStatus.READY_FOR_PICKUP:
                time_ready = now - order.updated_at
                
                if time_ready > timedelta(hours=24):
                    order.status = OrderStatus.CANCELLED
                    order.updated_at = now
                    
                    # Return items to inventory
                    order_items = self.db.query(OrderItem).filter(OrderItem.order_id == order.id).all()
                    
                    for order_item in order_items:
                        # Return to inventory
                        inventory = self.db.query(Inventory).filter(
                            Inventory.product_id == order_item.product_id,
                            Inventory.location_id == self.store_id
                        ).first()
                        
                        if inventory:
                            inventory.quantity += order_item.quantity
                            
                            # Record transaction
                            transaction = Transaction(
                                id=str(uuid.uuid4()),
                                product_id=order_item.product_id,
                                quantity_change=order_item.quantity,
                                transaction_type=TransactionType.RETURN,
                                reason=f"Order abandoned: {order.id}",
                                location_id=self.store_id,
                                reference_id=order.id,
                                timestamp=now
                            )
                            self.db.add(transaction)
        
        # Commit any changes
        try:
            if self.db.dirty:
                self.db.commit()
        except Exception as e:
            self.db.rollback()
            logger.error(f"Error updating orders: {str(e)}")

    async def create_order(self, customer_id: str, items: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Create a new customer order.
        
        Args:
            customer_id: ID of the customer
            items: List of order items, each with product_id and quantity
        
        Returns:
            Dictionary with order details or error
        """
        if not items:
            return {
                "status": "error",
                "message": "No items in order"
            }
        
        # Check inventory for all items
        insufficient_items = []
        
        for item in items:
            product_id = item.get("product_id")
            quantity = item.get("quantity", 0)
            
            if not product_id or quantity <= 0:
                continue
            
            # Check store inventory
            inventory = self.db.query(Inventory).filter(
                Inventory.product_id == product_id,
                Inventory.location_id == self.store_id
            ).first()
            
            if not inventory or inventory.quantity < quantity:
                insufficient_items.append({
                    "product_id": product_id,
                    "requested": quantity,
                    "available": inventory.quantity if inventory else 0
                })
        
        if insufficient_items:
            return {
                "status": "insufficient_inventory",
                "message": "Insufficient inventory for some items",
                "insufficient_items": insufficient_items
            }
        
        # Create order
        order_id = str(uuid.uuid4())
        
        try:
            # Create order record
            order = Order(
                id=order_id,
                customer_id=customer_id,
                location_id=self.store_id,
                status=OrderStatus.PROCESSING,
                created_at=datetime.utcnow()
            )
            self.db.add(order)
            
            # Create order items and remove from inventory
            order_total = 0.0
            
            for item in items:
                product_id = item["product_id"]
                quantity = item["quantity"]
                
                # Get product details
                product = self.db.query(Product).filter(Product.id == product_id).first()
                if not product:
                    continue
                
                # Create order item
                order_item = OrderItem(
                    id=str(uuid.uuid4()),
                    order_id=order_id,
                    product_id=product_id,
                    quantity=quantity,
                    unit_price=product.unit_price
                )
                self.db.add(order_item)
                
                # Update inventory
                inventory = self.db.query(Inventory).filter(
                    Inventory.product_id == product_id,
                    Inventory.location_id == self.store_id
                ).first()
                
                inventory.quantity -= quantity
                inventory.last_updated = datetime.utcnow()
                
                # Record transaction
                transaction = Transaction(
                    id=str(uuid.uuid4()),
                    product_id=product_id,
                    quantity_change=-quantity,
                    transaction_type=TransactionType.ORDER,
                    reason=f"Customer order {order_id}",
                    location_id=self.store_id,
                    reference_id=order_id,
                    timestamp=datetime.utcnow()
                )
                self.db.add(transaction)
                
                # Update total
                order_total += quantity * product.unit_price
            
            # Update order total
            order.total_amount = order_total
            
            # Commit changes
            self.db.commit()
            
            logger.info(f"Created order {order_id} for customer {customer_id} with {len(items)} items")
            
            # Check if we need to reorder any items
            for item in items:
                await self._check_reorder_needed(item["product_id"])
            
            return {
                "status": "success",
                "order_id": order_id,
                "total_amount": order_total,
                "message": "Order created successfully"
            }
            
        except Exception as e:
            self.db.rollback()
            logger.error(f"Error creating order: {str(e)}")
            
            return {
                "status": "error",
                "message": f"Error creating order: {str(e)}"
            }
    
    async def get_inventory_status(self) -> Dict[str, Any]:
        """Get current inventory status for this store."""
        # Get all inventory items
        inventory_items = self.db.query(Inventory, Product).join(
            Product, Product.id == Inventory.product_id
        ).filter(
            Inventory.location_id == self.store_id
        ).all()
        
        result = {
            "store_id": self.store_id,
            "items": [],
            "low_stock_items": [],
            "out_of_stock_items": []
        }
        
        for inventory, product in inventory_items:
            item_data = {
                "product_id": product.id,
                "product_name": product.name,
                "quantity": inventory.quantity,
                "minimum_stock": product.minimum_stock,
                "last_updated": inventory.last_updated.isoformat() if inventory.last_updated else None
            }
            
            result["items"].append(item_data)
            
            # Check for low stock and out of stock
            if inventory.quantity == 0:
                result["out_of_stock_items"].append(item_data)
            elif inventory.quantity <= product.minimum_stock:
                result["low_stock_items"].append(item_data)
        
        return result