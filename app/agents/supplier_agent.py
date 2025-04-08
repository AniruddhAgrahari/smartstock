import logging
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List
import uuid
import asyncio
import random

from app.agents.base import Agent
from app.models.product import Product
from app.models.supplier import Supplier

# Configure logging
logger = logging.getLogger(__name__)

class SupplierAgent(Agent):
    """
    Supplier Agent: Handles supplier selection, order management, and delivery tracking
    """
    
    def __init__(self, agent_id: str, db_session):
        super().__init__(agent_id, db_session)
        self.pending_orders = {}  # Track orders placed with suppliers
        
    async def process_message(self, message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Process incoming messages based on message type."""
        message_type = message.get("message_type")
        
        if message_type == "PURCHASE_ORDER":
            # Handle purchase order from warehouse
            return await self._handle_purchase_order(message)
        elif message_type == "ORDER_FOLLOWUP":
            # Handle order followup request
            return await self._handle_order_followup(message)
        elif message_type == "ORDER_CANCELLATION":
            # Handle order cancellation
            return await self._handle_order_cancellation(message)
        
        logger.warning(f"Supplier agent received unknown message type: {message_type}")
        return None
    
    async def run_cycle(self):
        """Run one cycle of supplier agent operations."""
        # Simulate supplier responses and update order statuses
        await self._update_order_statuses()
        
        # Process deliveries that are ready
        await self._process_deliveries()
        
        # Sleep to avoid overloading the system
        await asyncio.sleep(1)
    
    async def _handle_purchase_order(self, message: Dict[str, Any]) -> Dict[str, Any]:
        """
        Handle purchase order from warehouse.
        
        Expected message format:
        {
            "message_type": "PURCHASE_ORDER",
            "content": {
                "order_id": "uuid",
                "items": [
                    {
                        "product_id": "uuid",
                        "quantity": 10,
                        "unit_price": 5.99
                    }
                ],
                "warehouse_id": "warehouse_123",
                "status": "PENDING",
                "timestamp": "2023-01-01T12:00:00"
            }
        }
        """
        content = message.get("content", {})
        order_id = content.get("order_id")
        items = content.get("items", [])
        warehouse_id = content.get("warehouse_id")
        
        if not all([order_id, items, warehouse_id]):
            return {
                "status": "error",
                "message": "Invalid purchase order: order_id, items, and warehouse_id required"
            }
        
        # Prepare order for supplier selection and processing
        enriched_items = []
        
        for item in items:
            product_id = item.get("product_id")
            quantity = item.get("quantity")
            unit_price = item.get("unit_price")
            
            if not all([product_id, quantity, unit_price]):
                continue
            
            # Get product details
            product = self.db.query(Product).filter(Product.id == product_id).first()
            if not product:
                logger.warning(f"Unknown product in order {order_id}: {product_id}")
                continue
            
            # Find best supplier for this product
            supplier = await self._select_best_supplier(product_id, quantity)
            if not supplier:
                logger.warning(f"No suitable supplier found for product {product_id}")
                continue
            
            enriched_items.append({
                "product_id": product_id,
                "product_name": product.name,
                "quantity": quantity,
                "unit_price": unit_price,
                "supplier_id": supplier.id,
                "supplier_name": supplier.name,
                "lead_time_days": supplier.lead_time_days
            })
        
        if not enriched_items:
            return {
                "status": "error",
                "message": "No valid items in purchase order"
            }
        
        # Store the order with additional information
        self.pending_orders[order_id] = {
            "order_id": order_id,
            "items": enriched_items,
            "warehouse_id": warehouse_id,
            "status": "PLACED",
            "timestamp": datetime.utcnow(),
            "expected_delivery": datetime.utcnow() + timedelta(days=max(item["lead_time_days"] for item in enriched_items)),
            "updates": [{
                "status": "PLACED",
                "timestamp": datetime.utcnow(),
                "message": "Order placed with suppliers"
            }]
        }
        
        logger.info(f"Processed purchase order {order_id} with {len(enriched_items)} items for warehouse {warehouse_id}")
        
        # Send confirmation to warehouse
        await self.send_message({
            "sender": self.agent_id,
            "recipient": f"warehouse_agent_{warehouse_id}",
            "message_type": "SUPPLY_CONFIRMATION",
            "content": {
                "order_id": order_id,
                "status": "PLACED",
                "message": "Order has been placed with suppliers",
                "expected_delivery": self.pending_orders[order_id]["expected_delivery"].isoformat()
            }
        })
        
        return {
            "status": "success",
            "order_id": order_id,
            "expected_delivery": self.pending_orders[order_id]["expected_delivery"].isoformat(),
            "message": "Order placed successfully"
        }
    
    async def _handle_order_followup(self, message: Dict[str, Any]) -> Dict[str, Any]:
        """Handle order followup request."""
        content = message.get("content", {})
        order_id = content.get("order_id")
        
        if not order_id:
            return {
                "status": "error",
                "message": "Invalid order followup: order_id required"
            }
        
        if order_id not in self.pending_orders:
            return {
                "status": "error",
                "message": f"Unknown order: {order_id}"
            }
        
        order = self.pending_orders[order_id]
        
        # Send current status to requester
        recipient = message.get("sender", f"warehouse_agent_{order['warehouse_id']}")
        
        await self.send_message({
            "sender": self.agent_id,
            "recipient": recipient,
            "message_type": "SUPPLY_CONFIRMATION",
            "content": {
                "order_id": order_id,
                "status": order["status"],
                "updates": order["updates"],
                "expected_delivery": order["expected_delivery"].isoformat() if order["expected_delivery"] else None
            }
        })
        
        return {
            "status": "success",
            "message": f"Followup processed for order {order_id}"
        }
    
    async def _handle_order_cancellation(self, message: Dict[str, Any]) -> Dict[str, Any]:
        """Handle order cancellation request."""
        content = message.get("content", {})
        order_id = content.get("order_id")
        reason = content.get("reason", "No reason provided")
        
        if not order_id:
            return {
                "status": "error",
                "message": "Invalid order cancellation: order_id required"
            }
        
        if order_id not in self.pending_orders:
            return {
                "status": "error",
                "message": f"Unknown order: {order_id}"
            }
        
        order = self.pending_orders[order_id]
        
        # Only allow cancellation if order is not already shipped or delivered
        if order["status"] in ["SHIPPED", "DELIVERED"]:
            return {
                "status": "error",
                "message": f"Cannot cancel order {order_id} with status {order['status']}"
            }
        
        # Update order status
        order["status"] = "CANCELLED"
        order["updates"].append({
            "status": "CANCELLED",
            "timestamp": datetime.utcnow(),
            "message": f"Order cancelled: {reason}"
        })
        
        # Notify warehouse
        await self.send_message({
            "sender": self.agent_id,
            "recipient": f"warehouse_agent_{order['warehouse_id']}",
            "message_type": "SUPPLY_CONFIRMATION",
            "content": {
                "order_id": order_id,
                "status": "CANCELLED",
                "message": f"Order cancelled: {reason}"
            }
        })
        
        logger.info(f"Cancelled order {order_id}: {reason}")
        
        return {
            "status": "success",
            "message": f"Order {order_id} cancelled successfully"
        }
    
    async def _update_order_statuses(self):
        """Simulate supplier responses and update order statuses."""
        now = datetime.utcnow()
        
        for order_id, order in list(self.pending_orders.items()):
            # Skip orders that are already delivered, cancelled, or rejected
            if order["status"] in ["DELIVERED", "CANCELLED", "REJECTED"]:
                # Clean up old completed orders after 30 days
                if now - order["updates"][-1]["timestamp"] > timedelta(days=30):
                    del self.pending_orders[order_id]
                continue
            
            # Get current status
            current_status = order["status"]
            
            # Calculate time since last update
            last_update_time = order["updates"][-1]["timestamp"]
            time_since_update = now - last_update_time
            
            # Simulate status progression based on elapsed time
            new_status = current_status
            
            if current_status == "PLACED" and time_since_update > timedelta(hours=random.randint(1, 4)):
                # Move from PLACED to CONFIRMED
                new_status = "CONFIRMED"
                
            elif current_status == "CONFIRMED" and time_since_update > timedelta(hours=random.randint(6, 24)):
                # Move from CONFIRMED to PROCESSING
                new_status = "PROCESSING"
                
            elif current_status == "PROCESSING" and time_since_update > timedelta(days=random.randint(1, 3)):
                # Move from PROCESSING to SHIPPED
                new_status = "SHIPPED"
                # Update expected delivery to be 1-3 days from now
                order["expected_delivery"] = now + timedelta(days=random.randint(1, 3))
                
            elif current_status == "SHIPPED" and now >= order["expected_delivery"]:
                # Move from SHIPPED to DELIVERED when expected delivery time is reached
                new_status = "DELIVERED"
            
            # If status changed, update the order and notify warehouse
            if new_status != current_status:
                order["status"] = new_status
                order["updates"].append({
                    "status": new_status,
                    "timestamp": now,
                    "message": f"Order status updated to {new_status}"
                })
                
                logger.info(f"Updated order {order_id} status: {current_status} -> {new_status}")
                
                # Notify the warehouse
                await self.send_message({
                    "sender": self.agent_id,
                    "recipient": f"warehouse_agent_{order['warehouse_id']}",
                    "message_type": "SUPPLY_CONFIRMATION",
                    "content": {
                        "order_id": order_id,
                        "status": new_status,
                        "expected_delivery": order["expected_delivery"].isoformat() if "expected_delivery" in order else None,
                        "message": f"Order status updated to {new_status}"
                    }
                })
    
    async def _process_deliveries(self):
        """Process deliveries that are ready to be delivered."""
        for order_id, order in list(self.pending_orders.items()):
            # Only process orders that have just reached DELIVERED status
            if order["status"] == "DELIVERED" and order["updates"][-1]["status"] == "DELIVERED" and (
                datetime.utcnow() - order["updates"][-1]["timestamp"] < timedelta(minutes=5)
            ):
                # Send detailed delivery information to warehouse
                await self.send_message({
                    "sender": self.agent_id,
                    "recipient": f"warehouse_agent_{order['warehouse_id']}",
                    "message_type": "SUPPLY_CONFIRMATION",
                    "content": {
                        "order_id": order_id,
                        "status": "DELIVERED",
                        "items": [
                            {
                                "product_id": item["product_id"],
                                "quantity": item["quantity"],
                                "unit_price": item["unit_price"]
                            }
                            for item in order["items"]
                        ],
                        "warehouse_id": order["warehouse_id"],
                        "delivery_date": datetime.utcnow().isoformat()
                    }
                })
                
                logger.info(f"Processed delivery for order {order_id} to warehouse {order['warehouse_id']}")
    
    async def _select_best_supplier(self, product_id: str, quantity: int) -> Optional[Supplier]:
        """
        Select the best supplier for a product based on lead time, reliability, and cost.
        Returns a Supplier object or None if no suitable supplier is found.
        """
        # Get all suppliers for this product
        suppliers = self.db.query(Supplier).filter(
            Supplier.products.contains([product_id])
        ).all()
        
        if not suppliers:
            return None
        
        # Calculate a score for each supplier (lower is better)
        # Score is based on: lead time (40%), reliability (40%), and cost (20%)
        scored_suppliers = []
        
        for supplier in suppliers:
            # Check if supplier can provide the required quantity
            if supplier.max_capacity < quantity:
                continue
                
            # Calculate score
            lead_time_score = supplier.lead_time_days * 0.4
            reliability_score = (1 - supplier.reliability) * 0.4  # Reliability is 0-1, higher is better
            cost_score = supplier.cost_factor * 0.2
            
            total_score = lead_time_score + reliability_score + cost_score
            
            scored_suppliers.append((total_score, supplier))
        
        if not scored_suppliers:
            return None
        
        # Return the supplier with the lowest score (best overall)
        return min(scored_suppliers, key=lambda x: x[0])[1]