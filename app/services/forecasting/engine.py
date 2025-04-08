import logging
from typing import Dict, List, Optional, Any, Type
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

# Import the base model and specific models
from app.services.forecasting.arima_model import ARIMAForecastModel

logger = logging.getLogger(__name__)

class ForecastingEngine:
    """
    ForecastingEngine manages different forecasting models and provides a unified interface
    for training and prediction.
    """
    def __init__(self):
        self.models = {}
        self.available_model_types = {
            "ARIMA": ARIMAForecastModel,
            # Add more model types as they become available
            # "PROPHET": ProphetForecastModel,
            # "LSTM": LSTMForecastModel,
        }
        
    def register_model(self, model_name: str, model_type: str, **kwargs) -> bool:
        """
        Register a new forecasting model with the engine.
        
        Args:
            model_name: Unique identifier for the model
            model_type: Type of the model (e.g., "ARIMA", "PROPHET", "LSTM")
            **kwargs: Additional parameters to pass to the model constructor
            
        Returns:
            bool: True if the model was registered successfully, False otherwise
        """
        if model_name in self.models:
            logger.warning(f"Model {model_name} already exists. Use update_model to modify.")
            return False
            
        if model_type not in self.available_model_types:
            logger.error(f"Model type {model_type} is not supported.")
            return False
            
        try:
            # Create the model instance
            model_class = self.available_model_types[model_type]
            model_instance = model_class(**kwargs)
            self.models[model_name] = {
                "instance": model_instance,
                "type": model_type,
                "parameters": kwargs,
                "last_trained": None,
                "accuracy_metrics": {},
            }
            logger.info(f"Model {model_name} of type {model_type} registered successfully.")
            return True
        except Exception as e:
            logger.error(f"Failed to create model {model_name}: {str(e)}")
            return False
    
    def update_model(self, model_name: str, **kwargs) -> bool:
        """
        Update an existing model's parameters.
        
        Args:
            model_name: The name of the model to update
            **kwargs: New parameters to update
            
        Returns:
            bool: True if the model was updated successfully, False otherwise
        """
        if model_name not in self.models:
            logger.warning(f"Model {model_name} does not exist.")
            return False
            
        model_info = self.models[model_name]
        model_type = model_info["type"]
        
        # Merge old and new parameters
        updated_params = {**model_info["parameters"], **kwargs}
        
        try:
            # Create a new model instance with updated parameters
            model_class = self.available_model_types[model_type]
            model_instance = model_class(**updated_params)
            
            # Update the model info
            model_info["instance"] = model_instance
            model_info["parameters"] = updated_params
            logger.info(f"Model {model_name} updated successfully.")
            return True
        except Exception as e:
            logger.error(f"Failed to update model {model_name}: {str(e)}")
            return False
    
    def train_model(self, model_name: str, data: pd.DataFrame, target_col: str, 
                   date_col: str, exog_cols: Optional[List[str]] = None) -> bool:
        """
        Train a registered model on the provided data.
        
        Args:
            model_name: The name of the model to train
            data: DataFrame containing training data
            target_col: Name of the column containing the target variable
            date_col: Name of the column containing dates
            exog_cols: Optional list of exogenous variables column names
            
        Returns:
            bool: True if the model was trained successfully, False otherwise
        """
        if model_name not in self.models:
            logger.warning(f"Model {model_name} does not exist.")
            return False
            
        model_info = self.models[model_name]
        model_instance = model_info["instance"]
        
        try:
            # Train the model
            model_instance.train(data, target_col, date_col, exog_cols)
            
            # Update last trained timestamp
            model_info["last_trained"] = datetime.now()
            logger.info(f"Model {model_name} trained successfully.")
            return True
        except Exception as e:
            logger.error(f"Failed to train model {model_name}: {str(e)}")
            return False
    
    def predict(self, model_name: str, horizon: int, 
               future_exog: Optional[pd.DataFrame] = None) -> Optional[pd.DataFrame]:
        """
        Generate forecasts using a trained model.
        
        Args:
            model_name: The name of the model to use for prediction
            horizon: Number of periods to forecast
            future_exog: Optional DataFrame containing future values of exogenous variables
            
        Returns:
            DataFrame containing the forecasts or None if prediction failed
        """
        if model_name not in self.models:
            logger.warning(f"Model {model_name} does not exist.")
            return None
            
        model_info = self.models[model_name]
        model_instance = model_info["instance"]
        
        if model_info["last_trained"] is None:
            logger.warning(f"Model {model_name} has not been trained yet.")
            return None
            
        try:
            # Generate predictions
            forecasts = model_instance.predict(horizon, future_exog)
            logger.info(f"Generated forecasts with model {model_name} for {horizon} periods.")
            return forecasts
        except Exception as e:
            logger.error(f"Failed to generate forecasts with model {model_name}: {str(e)}")
            return None
    
    def evaluate_model(self, model_name: str, test_data: pd.DataFrame, 
                      target_col: str, date_col: str, 
                      exog_cols: Optional[List[str]] = None) -> Dict[str, float]:
        """
        Evaluate a model's performance on test data.
        
        Args:
            model_name: The name of the model to evaluate
            test_data: DataFrame containing test data
            target_col: Name of the column containing the target variable
            date_col: Name of the column containing dates
            exog_cols: Optional list of exogenous variables column names
            
        Returns:
            Dictionary of accuracy metrics or empty dict if evaluation failed
        """
        if model_name not in self.models:
            logger.warning(f"Model {model_name} does not exist.")
            return {}
            
        model_info = self.models[model_name]
        model_instance = model_info["instance"]
        
        if model_info["last_trained"] is None:
            logger.warning(f"Model {model_name} has not been trained yet.")
            return {}
            
        try:
            # Evaluate the model
            metrics = model_instance.evaluate(test_data, target_col, date_col, exog_cols)
            
            # Update the accuracy metrics
            model_info["accuracy_metrics"] = metrics
            logger.info(f"Evaluated model {model_name} with metrics: {metrics}")
            return metrics
        except Exception as e:
            logger.error(f"Failed to evaluate model {model_name}: {str(e)}")
            return {}
    
    def get_model_info(self, model_name: str) -> Dict[str, Any]:
        """
        Get information about a registered model.
        
        Args:
            model_name: The name of the model
            
        Returns:
            Dictionary containing model information
        """
        if model_name not in self.models:
            logger.warning(f"Model {model_name} does not exist.")
            return {}
            
        model_info = self.models[model_name]
        
        # Return a copy without the actual model instance (not serializable)
        return {
            "type": model_info["type"],
            "parameters": model_info["parameters"],
            "last_trained": model_info["last_trained"],
            "accuracy_metrics": model_info["accuracy_metrics"],
        }
    
    def list_models(self) -> List[Dict[str, Any]]:
        """
        List all registered models and their information.
        
        Returns:
            List of dictionaries containing model information
        """
        return [
            {"name": name, **self.get_model_info(name)}
            for name in self.models
        ]
    
    def delete_model(self, model_name: str) -> bool:
        """
        Delete a registered model.
        
        Args:
            model_name: The name of the model to delete
            
        Returns:
            bool: True if the model was deleted successfully, False otherwise
        """
        if model_name not in self.models:
            logger.warning(f"Model {model_name} does not exist.")
            return False
            
        try:
            del self.models[model_name]
            logger.info(f"Model {model_name} deleted successfully.")
            return True
        except Exception as e:
            logger.error(f"Failed to delete model {model_name}: {str(e)}")
            return False