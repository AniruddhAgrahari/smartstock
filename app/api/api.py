from fastapi import APIRouter

from app.api.endpoints import products, inventory, forecast, agents, dashboard, auth

api_router = APIRouter()

# Include all API endpoint routers
api_router.include_router(auth.router, prefix="/auth", tags=["Authentication"])
api_router.include_router(products.router, prefix="/products", tags=["Products"])
api_router.include_router(inventory.router, prefix="/inventory", tags=["Inventory"])
api_router.include_router(forecast.router, prefix="/forecast", tags=["Forecasting"])
api_router.include_router(agents.router, prefix="/agents", tags=["Agents"])
api_router.include_router(dashboard.router, prefix="/dashboard", tags=["Dashboard"])