import logging
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List
import uuid
import asyncio

from app.agents.base import Agent
from app.models.product import Product
from app.models.inventory import Inventory
from app.models.transaction import Transaction, TransactionType
from app.services.optimization.inventory import InventoryOptimizer

# Configure logging
logger = logging.getLogger(__name__)

class WarehouseAgent(Agent):
    """
    Warehouse Agent: Manages inventory transfers and fulfillment between warehouses and stores
    """
    
    def __init__(self, warehouse_id: str, db_session):
        super().__init__(f"warehouse_agent_{warehouse_id}", db_session)
        self.warehouse_id = warehouse_id
        self.inventory_optimizer = InventoryOptimizer()
        self.pending_transfers = {}  # Track transfers in progress
        self.pending_orders = {}  # Track orders from suppliers
        self.transfer_priorities = {}  # Priority queue for transfers
    
    async def process_message(self, message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Process incoming messages based on message type."""
        message_type = message.get("message_type")
        
        if message_type == "INVENTORY_REQUEST":
            # Handle inventory request from store
            return await self._handle_inventory_request(message)
        elif message_type == "SUPPLY_CONFIRMATION":
            # Handle supply confirmation from supplier
            return await self._handle_supply_confirmation(message)
        elif message_type == "TRANSFER_STATUS_UPDATE":
            # Handle transfer status update
            return await self._handle_transfer_status_update(message)
        
        logger.warning(f"Warehouse agent received unknown message type: {message_type}")
        return None
    
    async def run_cycle(self):
        """Run one cycle of warehouse agent operations."""
        # Check stock levels and generate supply requests if needed
        await self._check_stock_levels()
        
        # Process pending transfers based on priority
        await self._process_pending_transfers()
        
        # Check pending orders from suppliers
        await self._check_pending_orders()
        
        # Sleep to avoid excessive CPU usage
        await asyncio.sleep(1)
    
    async def _handle_inventory_request(self, message: Dict[str, Any]) -> Dict[str, Any]:
        """
        Handle inventory request from a store.
        Determines if there is sufficient stock and creates a transfer if possible.
        """
        content = message.get("content", {})
        product_id = content.get("product_id")
        requested_quantity = content.get("quantity")
        store_id = content.get("store_id")
        priority = content.get("priority", "MEDIUM")
        
        logger.info(f"Received inventory request from store {store_id} for product {product_id}, quantity {requested_quantity}")
        
        # Check warehouse inventory
        inventory = self.db.query(Inventory).filter(
            Inventory.product_id == product_id,
            Inventory.location_id == self.warehouse_id
        ).first()
        
        # Get product details
        product = self.db.query(Product).filter(Product.id == product_id).first()
        if not product:
            logger.error(f"Product {product_id} not found in database")
            return {
                "sender": self.agent_id,
                "recipient": message.get("sender"),
                "message_type": "INVENTORY_RESPONSE",
                "content": {
                    "product_id": product_id,
                    "status": "REJECTED",
                    "reason": "Product not found"
                },
                "timestamp": datetime.utcnow().isoformat()
            }
        
        # If inventory doesn't exist or quantity is insufficient
        if not inventory or inventory.quantity < requested_quantity:
            available_quantity = inventory.quantity if inventory else 0
            
            # If we have some but not all, offer partial fulfillment
            if available_quantity > 0:
                # Add to transfer queue with appropriate priority
                transfer_id = str(uuid.uuid4())
                self.pending_transfers[transfer_id] = {
                    "product_id": product_id,
                    "store_id": store_id,
                    "requested_quantity": requested_quantity,
                    "available_quantity": available_quantity,
                    "priority": priority,
                    "status": "PENDING",
                    "created_at": datetime.utcnow()
                }
                
                # Add to priority queue
                priority_score = self._calculate_priority_score(
                    priority, product, available_quantity, requested_quantity
                )
                self.transfer_priorities[transfer_id] = priority_score
                
                return {
                    "sender": self.agent_id,
                    "recipient": message.get("sender"),
                    "message_type": "INVENTORY_RESPONSE",
                    "content": {
                        "product_id": product_id,
                        "status": "PARTIAL",
                        "transfer_id": transfer_id,
                        "available_quantity": available_quantity,
                        "requested_quantity": requested_quantity
                    },
                    "timestamp": datetime.utcnow().isoformat()
                }
            
            # If inventory insufficient, request from supplier
            await self._request_from_supplier(product_id, requested_quantity, store_id)
            
            return {
                "sender": self.agent_id,
                "recipient": message.get("sender"),
                "message_type": "INVENTORY_RESPONSE",
                "content": {
                    "product_id": product_id,
                    "status": "BACKORDERED",
                    "reason": "Insufficient inventory, order placed with supplier",
                    "available_quantity": available_quantity,
                    "requested_quantity": requested_quantity
                },
                "timestamp": datetime.utcnow().isoformat()
            }
        
        # We have sufficient stock, create a transfer
        transfer_id = str(uuid.uuid4())
        self.pending_transfers[transfer_id] = {
            "product_id": product_id,
            "store_id": store_id,
            "requested_quantity": requested_quantity,
            "available_quantity": requested_quantity,
            "priority": priority,
            "status": "PENDING",
            "created_at": datetime.utcnow()
        }
        
        # Add to priority queue
        priority_score = self._calculate_priority_score(
            priority, product, requested_quantity, requested_quantity
        )
        self.transfer_priorities[transfer_id] = priority_score
        
        return {
            "sender": self.agent_id,
            "recipient": message.get("sender"),
            "message_type": "INVENTORY_RESPONSE",
            "content": {
                "product_id": product_id,
                "status": "APPROVED",
                "transfer_id": transfer_id,
                "quantity": requested_quantity
            },
            "timestamp": datetime.utcnow().isoformat()
        }
    
    async def _handle_supply_confirmation(self, message: Dict[str, Any]) -> None:
        """Handle supply confirmation from supplier."""
        content = message.get("content", {})
        order_id = content.get("order_id")
        status = content.get("status")
        items = content.get("items", [])
        
        logger.info(f"Received supply confirmation for order {order_id}: {status}")
        
        if order_id in self.pending_orders:
            order_data = self.pending_orders[order_id]
            
            # Update order status
            order_data["status"] = status
            
            # If delivered, check if there are any pending transfers for these items
            if status == "DELIVERED":
                for item in items:
                    product_id = item.get("product_id")
                    quantity = item.get("quantity")
                    
                    # Check if any store was waiting for this product
                    store_id = order_data.get("store_id")
                    if store_id:
                        # Notify the store that their order is ready
                        await self.send_message({
                            "sender": self.agent_id,
                            "recipient": f"store_agent_{store_id}",
                            "message_type": "INVENTORY_AVAILABLE",
                            "content": {
                                "product_id": product_id,
                                "quantity": quantity,
                                "warehouse_id": self.warehouse_id,
                                "order_id": order_id
                            },
                            "timestamp": datetime.utcnow().isoformat()
                        })
                
                # Remove from pending orders
                del self.pending_orders[order_id]
        
        return None
    
    async def _handle_transfer_status_update(self, message: Dict[str, Any]) -> None:
        """Handle transfer status update."""
        content = message.get("content", {})
        transfer_id = content.get("transfer_id")
        status = content.get("status")
        
        logger.info(f"Received transfer status update for {transfer_id}: {status}")
        
        if transfer_id in self.pending_transfers:
            transfer_data = self.pending_transfers[transfer_id]
            
            # Update transfer status
            transfer_data["status"] = status
            
            # If completed or failed, remove from pending transfers
            if status in ["COMPLETED", "FAILED"]:
                if status == "FAILED":
                    # Log failure reason
                    reason = content.get("reason", "Unknown reason")
                    logger.warning(f"Transfer {transfer_id} failed: {reason}")
                    
                    # Consider re-trying or alternative actions
                    product_id = transfer_data.get("product_id")
                    store_id = transfer_data.get("store_id")
                    quantity = transfer_data.get("requested_quantity")
                    
                    # For now, just notify the store of the failure
                    await self.send_message({
                        "sender": self.agent_id,
                        "recipient": f"store_agent_{store_id}",
                        "message_type": "TRANSFER_UPDATE",
                        "content": {
                            "transfer_id": transfer_id,
                            "product_id": product_id,
                            "status": "FAILED",
                            "reason": reason
                        },
                        "timestamp": datetime.utcnow().isoformat()
                    })
                
                # Remove from transfers and priorities
                del self.pending_transfers[transfer_id]
                if transfer_id in self.transfer_priorities:
                    del self.transfer_priorities[transfer_id]
        
        return None
    
    async def _check_stock_levels(self):
        """Check warehouse stock levels and request supplies if needed."""
        # Get all inventory for this warehouse
        inventory_items = self.db.query(Inventory).filter(
            Inventory.location_id == self.warehouse_id
        ).all()
        
        for inventory in inventory_items:
            # Get product details
            product = self.db.query(Product).filter(Product.id == inventory.product_id).first()
            if not product:
                continue
            
            # Check if below minimum stock level
            if inventory.quantity < product.minimum_stock:
                # Calculate how many to order
                quantity_to_order = product.maximum_stock - inventory.quantity
                
                # Request from supplier if not already ordered
                already_ordered = False
                for order_id, order_data in self.pending_orders.items():
                    if (order_data.get("product_id") == product.id and 
                        order_data.get("status") in ["PENDING", "CONFIRMED", "SHIPPED"]):
                        already_ordered = True
                        break
                
                if not already_ordered and quantity_to_order > 0:
                    await self._request_from_supplier(product.id, quantity_to_order, None)
    
    async def _process_pending_transfers(self):
        """Process pending transfers based on priority."""
        if not self.pending_transfers:
            return
        
        # Sort transfers by priority score (highest first)
        sorted_transfers = sorted(
            self.transfer_priorities.items(), 
            key=lambda x: x[1], 
            reverse=True
        )
        
        for transfer_id, _ in sorted_transfers:
            if transfer_id not in self.pending_transfers:
                continue
                
            transfer_data = self.pending_transfers[transfer_id]
            
            # Skip transfers that are not in PENDING status
            if transfer_data.get("status") != "PENDING":
                continue
            
            # Execute the transfer
            product_id = transfer_data.get("product_id")
            store_id = transfer_data.get("store_id")
            quantity = transfer_data.get("available_quantity")
            
            # Update warehouse inventory
            warehouse_inventory = self.db.query(Inventory).filter(
                Inventory.product_id == product_id,
                Inventory.location_id == self.warehouse_id
            ).first()
            
            # Double-check that we still have the inventory
            if not warehouse_inventory or warehouse_inventory.quantity < quantity:
                # Not enough inventory, update transfer status
                transfer_data["status"] = "FAILED"
                transfer_data["reason"] = "Insufficient inventory"
                
                # Notify store
                await self.send_message({
                    "sender": self.agent_id,
                    "recipient": f"store_agent_{store_id}",
                    "message_type": "TRANSFER_UPDATE",
                    "content": {
                        "transfer_id": transfer_id,
                        "product_id": product_id,
                        "status": "FAILED",
                        "reason": "Insufficient inventory"
                    },
                    "timestamp": datetime.utcnow().isoformat()
                })
                
                continue
            
            # Update warehouse inventory
            warehouse_inventory.quantity -= quantity
            
            # Create transaction record for warehouse
            warehouse_transaction = Transaction(
                id=str(uuid.uuid4()),
                product_id=product_id,
                quantity_change=-quantity,
                transaction_type=TransactionType.TRANSFER,
                reason=f"Transfer to store {store_id}",
                location_id=self.warehouse_id,
                reference_id=transfer_id,
                timestamp=datetime.utcnow()
            )
            self.db.add(warehouse_transaction)
            
            # Update transfer status
            transfer_data["status"] = "IN_TRANSIT"
            
            # Commit changes
            self.db.commit()
            
            # Notify store about the transfer
            await self.send_message({
                "sender": self.agent_id,
                "recipient": f"store_agent_{store_id}",
                "message_type": "TRANSFER_INITIATED",
                "content": {
                    "transfer_id": transfer_id,
                    "product_id": product_id,
                    "quantity": quantity,
                    "warehouse_id": self.warehouse_id,
                    "estimated_arrival": (datetime.utcnow() + timedelta(hours=24)).isoformat()
                },
                "timestamp": datetime.utcnow().isoformat()
            })
            
            logger.info(f"Initiated transfer {transfer_id} of {quantity} units of product {product_id} to store {store_id}")
    
    async def _check_pending_orders(self):
        """Check status of pending orders from suppliers."""
        # In a real system, this would query the supplier API
        # For simulation purposes, we're not implementing this functionality
        # as the SupplierAgent handles the order status updates and deliveries
        pass
    
    async def _request_from_supplier(self, product_id: str, quantity: int, store_id: Optional[str]) -> None:
        """Request supplies from supplier agent."""
        supplier_agent_id = "supplier_agent"  # Default supplier agent ID
        
        # Send supply request to supplier agent
        await self.send_message({
            "sender": self.agent_id,
            "recipient": supplier_agent_id,
            "message_type": "SUPPLY_REQUEST",
            "content": {
                "product_id": product_id,
                "quantity": quantity,
                "warehouse_id": self.warehouse_id,
                "store_id": store_id  # Include the store that requested it, if any
            },
            "timestamp": datetime.utcnow().isoformat()
        })
        
        # Track the request locally
        request_id = str(uuid.uuid4())
        self.pending_orders[request_id] = {
            "product_id": product_id,
            "quantity": quantity,
            "store_id": store_id,
            "status": "REQUESTED",
            "created_at": datetime.utcnow()
        }
        
        logger.info(f"Requested {quantity} units of product {product_id} from supplier")
    
    def _calculate_priority_score(self, priority: str, product: Product, available_quantity: int, requested_quantity: int) -> float:
        """
        Calculate a priority score for a transfer.
        Higher score = higher priority.
        """
        # Base priority scores
        priority_values = {
            "HIGH": 100,
            "MEDIUM": 50,
            "LOW": 10
        }
        
        # Get base score from priority level
        base_score = priority_values.get(priority, 50)
        
        # Adjust based on fulfillment percentage
        fulfillment_pct = min(1.0, available_quantity / requested_quantity)
        
        # Adjust based on product value
        value_factor = min(2.0, product.unit_price / 100.0 + 0.5)
        
        # Calculate final score
        # Higher score for high priority, high value, and high fulfillment percentage
        final_score = base_score * fulfillment_pct * value_factor
        
        return final_score