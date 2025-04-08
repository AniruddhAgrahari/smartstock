from abc import ABC, abstractmethod
import asyncio
import json
import logging
from datetime import datetime
from typing import Dict, Any, List, Optional

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class Agent(ABC):
    """Base Agent class that defines the interface for all agents in the system."""
    
    def __init__(self, agent_id: str, db_session):
        self.agent_id = agent_id
        self.db = db_session
        self.running = False
        self.message_queue = asyncio.Queue()
        logger.info(f"Agent {agent_id} initialized")
    
    @abstractmethod
    async def process_message(self, message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Process incoming messages based on agent-specific logic."""
        pass
    
    @abstractmethod
    async def run_cycle(self):
        """Run one cycle of agent operations."""
        pass
    
    async def send_message(self, message: Dict[str, Any]):
        """Send a message to another agent via the message broker."""
        # In a real implementation, this would use a message broker (RabbitMQ, Kafka, etc.)
        # For now, we'll just log the message
        logger.info(f"Agent {self.agent_id} sending message: {json.dumps(message)}")
        
        # TODO: Implement actual message sending via message broker
        # For now, this is a placeholder
        pass
    
    async def receive_message(self, message: Dict[str, Any]):
        """Receive a message from another agent."""
        await self.message_queue.put(message)
        logger.info(f"Agent {self.agent_id} received message: {json.dumps(message)}")
    
    async def start(self):
        """Start the agent's processing loop."""
        self.running = True
        logger.info(f"Agent {self.agent_id} started")
        
        # Start two tasks: one for processing the message queue and one for running agent cycles
        await asyncio.gather(
            self._process_message_queue(),
            self._run_agent_cycles()
        )
    
    async def stop(self):
        """Stop the agent's processing loop."""
        self.running = False
        logger.info(f"Agent {self.agent_id} stopped")
    
    async def _process_message_queue(self):
        """Process messages from the queue."""
        while self.running:
            try:
                # Try to get a message with a timeout to allow for clean shutdown
                message = await asyncio.wait_for(self.message_queue.get(), timeout=1.0)
                response = await self.process_message(message)
                
                if response:
                    await self.send_message(response)
                
                self.message_queue.task_done()
            except asyncio.TimeoutError:
                # No message within timeout, just continue
                pass
            except Exception as e:
                logger.error(f"Error processing message in agent {self.agent_id}: {str(e)}")
    
    async def _run_agent_cycles(self):
        """Run the agent's operational cycles."""
        while self.running:
            try:
                await self.run_cycle()
                # Wait a bit before the next cycle to avoid consuming too many resources
                await asyncio.sleep(10)  # Run cycle every 10 seconds
            except Exception as e:
                logger.error(f"Error in agent {self.agent_id} cycle: {str(e)}")
                # Wait a bit before retrying after an error
                await asyncio.sleep(5)