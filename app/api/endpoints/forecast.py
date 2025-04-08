import uuid
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, status, Query, BackgroundTasks
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
import pandas as pd
import json

from app.database.session import get_db
from app.models.user import User, UserRole
from app.models.product import Product
from app.models.transaction import Transaction
from app.api.endpoints.auth import get_current_user, check_user_role
from app.services.forecasting.engine import ForecastingEngine
from app.services.forecasting.arima_model import ARIMAForecastModel

router = APIRouter()

# Initialize the forecasting engine
forecasting_engine = ForecastingEngine()

# Register ARIMA model
forecasting_engine.register_model(
    "arima", 
    ARIMAForecastModel,
    {"p": 1, "d": 1, "q": 0}  # Default ARIMA parameters
)

# Cache for forecast results
forecast_cache = {}

@router.get("/models")
async def get_available_forecast_models(
    current_user: User = Depends(get_current_user)
):
    """Get the list of available forecasting models."""
    return {
        "models": list(forecasting_engine.models.keys()),
        "default_configs": {
            model_name: config for model_name, config in forecasting_engine.model_configs.items()
        }
    }

@router.post("/generate")
async def generate_forecast(
    product_id: str,
    model_name: str = "arima",
    periods: int = 30,
    background_tasks: BackgroundTasks = None,
    config: Optional[Dict[str, Any]] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Generate a forecast for a product using the specified model.
    If background_tasks is provided, the forecast will be generated in the background.
    """
    # Check if product exists
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Product with ID {product_id} not found"
        )
    
    # Check if model exists
    if model_name not in forecasting_engine.models:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Model {model_name} not found. Available models: {list(forecasting_engine.models.keys())}"
        )
    
    # Get historical data for this product
    historical_data = await _get_historical_data(product_id, db)
    
    if len(historical_data) < 10:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Insufficient historical data for forecasting. Need at least 10 data points."
        )
    
    # Load data into the forecasting engine
    forecasting_engine.load_historical_data(product_id, historical_data)
    
    # Generate a unique forecast ID
    forecast_id = str(uuid.uuid4())
    
    # If we have background tasks, run the forecast asynchronously
    if background_tasks:
        forecast_cache[forecast_id] = {"status": "pending"}
        background_tasks.add_task(
            _generate_forecast_task,
            forecast_id, 
            forecasting_engine, 
            model_name, 
            product_id, 
            periods, 
            config
        )
        
        return {
            "status": "pending",
            "forecast_id": forecast_id,
            "message": f"Forecast is being generated in the background. Check status with /forecast/status/{forecast_id}"
        }
    else:
        # Run the forecast synchronously
        try:
            forecast_data, confidence_intervals = forecasting_engine.forecast_demand(
                model_name, product_id, periods, config
            )
            
            if forecast_data is None:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Failed to generate forecast"
                )
                
            # Store in cache
            forecast_cache[forecast_id] = {
                "status": "completed",
                "forecast": forecast_data.to_dict(orient="records"),
                "confidence_intervals": confidence_intervals.to_dict(orient="records") if confidence_intervals is not None else None,
                "model": model_name,
                "product_id": product_id,
                "product_name": product.name,
                "generated_at": datetime.utcnow().isoformat()
            }
            
            return {
                "status": "completed",
                "forecast_id": forecast_id,
                "forecast": forecast_cache[forecast_id]
            }
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Error generating forecast: {str(e)}"
            )

@router.get("/status/{forecast_id}")
async def get_forecast_status(
    forecast_id: str,
    current_user: User = Depends(get_current_user)
):
    """Get the status of a background forecast generation task."""
    if forecast_id not in forecast_cache:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Forecast with ID {forecast_id} not found"
        )
    
    return forecast_cache[forecast_id]

@router.get("/product/{product_id}")
async def get_latest_product_forecast(
    product_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get the latest forecast for a specific product."""
    # Check if product exists
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Product with ID {product_id} not found"
        )
    
    # Find the latest forecast for this product in the cache
    latest_forecast = None
    latest_time = None
    
    for forecast_id, forecast in forecast_cache.items():
        if forecast.get("product_id") == product_id and forecast.get("status") == "completed":
            generated_at = datetime.fromisoformat(forecast.get("generated_at"))
            if latest_time is None or generated_at > latest_time:
                latest_forecast = forecast
                latest_time = generated_at
    
    if not latest_forecast:
        # Try to generate a new forecast
        return await generate_forecast(product_id, "arima", 30, None, None, db, current_user)
    
    return {
        "status": "completed",
        "forecast": latest_forecast
    }

@router.get("/evaluate/{product_id}")
async def evaluate_forecast_models(
    product_id: str,
    test_periods: int = 30,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Evaluate different forecasting models for a product."""
    # Check if product exists
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Product with ID {product_id} not found"
        )
    
    # Get historical data
    historical_data = await _get_historical_data(product_id, db)
    
    if len(historical_data) < test_periods + 10:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Insufficient historical data for evaluation. Need at least {test_periods + 10} data points."
        )
    
    # Load data into the forecasting engine
    forecasting_engine.load_historical_data(product_id, historical_data)
    
    # Evaluate models
    evaluation_results = forecasting_engine.evaluate_models(product_id, test_periods)
    
    if not evaluation_results:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to evaluate forecasting models"
        )
    
    # Get the best model
    best_model = forecasting_engine.get_best_model(product_id, test_periods)
    
    return {
        "evaluation_results": evaluation_results,
        "best_model": best_model,
        "test_periods": test_periods
    }

async def _get_historical_data(product_id: str, db: Session) -> pd.DataFrame:
    """
    Get historical sales data for a product from transactions.
    Aggregates by day to create a time series.
    """
    # Query all transactions with quantity changes (sales have negative values)
    transactions = db.query(Transaction).filter(
        Transaction.product_id == product_id,
        Transaction.quantity_change < 0  # Only sales transactions
    ).all()
    
    if not transactions:
        return pd.DataFrame(columns=["date", "quantity"])
    
    # Convert to pandas dataframe
    data = []
    for tx in transactions:
        data.append({
            "date": tx.timestamp.date(),
            "quantity": abs(tx.quantity_change)  # Convert to positive values
        })
    
    df = pd.DataFrame(data)
    
    # Aggregate by date
    if not df.empty:
        df = df.groupby("date").sum().reset_index()
    
    # Ensure we have continuous dates (fill missing dates with 0)
    if len(df) > 1:
        date_range = pd.date_range(start=df["date"].min(), end=df["date"].max())
        df = df.set_index("date").reindex(date_range, fill_value=0).reset_index()
        df = df.rename(columns={"index": "date"})
    
    return df

async def _generate_forecast_task(
    forecast_id: str,
    forecasting_engine: ForecastingEngine,
    model_name: str,
    product_id: str,
    periods: int,
    config: Optional[Dict[str, Any]]
):
    """Task to generate a forecast in the background."""
    try:
        # Update status in cache
        forecast_cache[forecast_id]["status"] = "processing"
        
        # Generate the forecast
        forecast_data, confidence_intervals = forecasting_engine.forecast_demand(
            model_name, product_id, periods, config
        )
        
        if forecast_data is None:
            forecast_cache[forecast_id] = {
                "status": "failed",
                "error": "Failed to generate forecast"
            }
            return
        
        # Store the successful result in cache
        forecast_cache[forecast_id] = {
            "status": "completed",
            "forecast": forecast_data.to_dict(orient="records"),
            "confidence_intervals": confidence_intervals.to_dict(orient="records") if confidence_intervals is not None else None,
            "model": model_name,
            "product_id": product_id,
            "generated_at": datetime.utcnow().isoformat()
        }
    except Exception as e:
        # Store the error in cache
        forecast_cache[forecast_id] = {
            "status": "failed",
            "error": str(e)
        }