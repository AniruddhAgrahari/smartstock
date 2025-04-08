import uuid
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, status, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from datetime import datetime
import asyncio
import json

from app.database.session import get_db, SessionLocal
from app.models.user import User, UserRole
from app.models.product import Product
from app.models.inventory import Inventory
from app.api.endpoints.auth import get_current_user, check_user_role
from app.agents.store_agent import StoreAgent
from app.agents.warehouse_agent import WarehouseAgent
from app.agents.supplier_agent import SupplierAgent

router = APIRouter()

# Keep track of active agents
active_agents = {}

@router.post("/start-store-agent")
async def start_store_agent(
    store_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(check_user_role([UserRole.ADMIN, UserRole.MANAGER]))
):
    """Start a store agent for a specific store location."""
    agent_id = f"store_agent_{store_id}"
    
    # Check if agent is already running
    if agent_id in active_agents:
        return {"status": "already_running", "agent_id": agent_id}
    
    # Create a new database session for the agent
    agent_db = SessionLocal()
    
    # Create and start the agent
    agent = StoreAgent(store_id, agent_db)
    
    # Store the agent
    active_agents[agent_id] = {
        "agent": agent,
        "db_session": agent_db,
        "type": "store",
        "location_id": store_id,
        "started_at": datetime.utcnow(),
        "started_by": current_user.username
    }
    
    # Start the agent in a background task
    asyncio.create_task(agent.start())
    
    return {
        "status": "started",
        "agent_id": agent_id,
        "type": "store",
        "location_id": store_id
    }

@router.post("/start-warehouse-agent")
async def start_warehouse_agent(
    warehouse_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(check_user_role([UserRole.ADMIN, UserRole.MANAGER]))
):
    """Start a warehouse agent for a specific warehouse location."""
    agent_id = f"warehouse_agent_{warehouse_id}"
    
    # Check if agent is already running
    if agent_id in active_agents:
        return {"status": "already_running", "agent_id": agent_id}
    
    # Create a new database session for the agent
    agent_db = SessionLocal()
    
    # Create and start the agent
    agent = WarehouseAgent(warehouse_id, agent_db)
    
    # Store the agent
    active_agents[agent_id] = {
        "agent": agent,
        "db_session": agent_db,
        "type": "warehouse",
        "location_id": warehouse_id,
        "started_at": datetime.utcnow(),
        "started_by": current_user.username
    }
    
    # Start the agent in a background task
    asyncio.create_task(agent.start())
    
    return {
        "status": "started",
        "agent_id": agent_id,
        "type": "warehouse",
        "location_id": warehouse_id
    }

@router.post("/start-supplier-agent")
async def start_supplier_agent(
    agent_id: str = "supplier_agent",
    db: Session = Depends(get_db),
    current_user: User = Depends(check_user_role([UserRole.ADMIN, UserRole.MANAGER]))
):
    """Start a supplier agent to handle ordering and supplier management."""
    # Check if agent is already running
    if agent_id in active_agents:
        return {"status": "already_running", "agent_id": agent_id}
    
    # Create a new database session for the agent
    agent_db = SessionLocal()
    
    # Create and start the agent
    agent = SupplierAgent(agent_id, agent_db)
    
    # Store the agent
    active_agents[agent_id] = {
        "agent": agent,
        "db_session": agent_db,
        "type": "supplier",
        "started_at": datetime.utcnow(),
        "started_by": current_user.username
    }
    
    # Start the agent in a background task
    asyncio.create_task(agent.start())
    
    return {
        "status": "started",
        "agent_id": agent_id,
        "type": "supplier"
    }

@router.post("/stop-agent/{agent_id}")
async def stop_agent(
    agent_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(check_user_role([UserRole.ADMIN, UserRole.MANAGER]))
):
    """Stop a running agent."""
    if agent_id not in active_agents:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent {agent_id} not found or not running"
        )
    
    agent_data = active_agents[agent_id]
    
    # Stop the agent
    await agent_data["agent"].stop()
    
    # Close the database session
    agent_data["db_session"].close()
    
    # Remove from active agents
    del active_agents[agent_id]
    
    return {"status": "stopped", "agent_id": agent_id}

@router.get("/running-agents")
async def get_running_agents(
    current_user: User = Depends(get_current_user)
):
    """Get a list of all running agents."""
    # Format the response to not include the actual agent objects
    agents_info = []
    for agent_id, agent_data in active_agents.items():
        agents_info.append({
            "agent_id": agent_id,
            "type": agent_data["type"],
            "location_id": agent_data.get("location_id"),
            "started_at": agent_data["started_at"],
            "started_by": agent_data["started_by"],
            "running": agent_data["agent"].running
        })
    
    return {"agents": agents_info}

@router.post("/send-message")
async def send_message_to_agent(
    agent_id: str,
    message: Dict[str, Any],
    db: Session = Depends(get_db),
    current_user: User = Depends(check_user_role([UserRole.ADMIN, UserRole.MANAGER]))
):
    """Send a message to a specific agent."""
    if agent_id not in active_agents:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent {agent_id} not found or not running"
        )
    
    agent = active_agents[agent_id]["agent"]
    
    # Add sender and timestamp if not provided
    if "sender" not in message:
        message["sender"] = f"user_{current_user.username}"
    
    if "timestamp" not in message:
        message["timestamp"] = datetime.utcnow().isoformat()
    
    # Send the message to the agent
    await agent.receive_message(message)
    
    return {"status": "message_sent", "agent_id": agent_id}

@router.websocket("/ws/{agent_id}")
async def websocket_endpoint(
    websocket: WebSocket,
    agent_id: str
):
    """WebSocket endpoint for real-time communication with an agent."""
    await websocket.accept()
    
    if agent_id not in active_agents:
        await websocket.send_json({"error": f"Agent {agent_id} not found or not running"})
        await websocket.close()
        return
    
    agent = active_agents[agent_id]["agent"]
    
    # Create a queue for messages from the agent
    message_queue = asyncio.Queue()
    
    # Register the WebSocket in the agent
    # This is a simplified approach - in a real system, we would need a proper
    # message broker or pub/sub system
    original_send_message = agent.send_message
    
    async def patched_send_message(message):
        # Call the original method
        await original_send_message(message)
        
        # Also send to WebSocket if the message is meant for the UI
        if message.get("recipient", "").startswith("user_"):
            await message_queue.put(message)
    
    # Replace the method temporarily
    agent.send_message = patched_send_message
    
    try:
        # Create tasks for receiving from WebSocket and sending to WebSocket
        receive_task = asyncio.create_task(receive_from_websocket(websocket, agent))
        send_task = asyncio.create_task(send_to_websocket(websocket, message_queue))
        
        # Wait for either task to complete
        done, pending = await asyncio.wait(
            [receive_task, send_task],
            return_when=asyncio.FIRST_COMPLETED
        )
        
        # Cancel the remaining task
        for task in pending:
            task.cancel()
            
    except WebSocketDisconnect:
        pass
    finally:
        # Restore the original method
        agent.send_message = original_send_message

async def receive_from_websocket(websocket: WebSocket, agent):
    """Receive messages from the WebSocket and forward them to the agent."""
    try:
        while True:
            data = await websocket.receive_json()
            
            # Add sender information if not provided
            if "sender" not in data:
                data["sender"] = "user_websocket"
            
            if "timestamp" not in data:
                data["timestamp"] = datetime.utcnow().isoformat()
            
            # Forward to the agent
            await agent.receive_message(data)
    except WebSocketDisconnect:
        pass

async def send_to_websocket(websocket: WebSocket, message_queue: asyncio.Queue):
    """Send messages from the agent to the WebSocket."""
    try:
        while True:
            # Wait for messages
            message = await message_queue.get()
            
            # Send to the WebSocket
            await websocket.send_json(message)
            
            message_queue.task_done()
    except WebSocketDisconnect:
        pass