import pandas as pd
import numpy as np
from statsmodels.tsa.arima.model import ARIMA
from datetime import datetime, timedelta
from typing import Dict, Any, Optional
import logging

from app.services.forecasting.engine import ForecastModel

# Configure logging
logger = logging.getLogger(__name__)

class ARIMAForecastModel(ForecastModel):
    """
    Implements a forecasting model based on ARIMA (AutoRegressive Integrated Moving Average).
    """
    
    def __init__(self, p: int = 1, d: int = 1, q: int = 0, **kwargs):
        """
        Initialize ARIMA model with order parameters.
        
        Args:
            p: The order of the autoregressive part
            d: The degree of differencing
            q: The order of the moving average part
        """
        self.p = p
        self.d = d
        self.q = q
        self.order = (p, d, q)
        self.model = None
        self.results = None
        self.last_date = None
        self.date_freq = 'D'  # Default frequency is daily
        self.forecast_data = None
        self.confidence_intervals = None
        self.is_trained = False
    
    def train(self, historical_data: pd.DataFrame) -> None:
        """
        Train the ARIMA model on historical data.
        
        Args:
            historical_data: DataFrame with at least 'date' and 'quantity' columns
        """
        if 'date' not in historical_data.columns or 'quantity' not in historical_data.columns:
            raise ValueError("Historical data must contain 'date' and 'quantity' columns")
        
        # Sort data by date
        data = historical_data.sort_values('date')
        
        # Determine frequency if enough data points
        if len(data) >= 2:
            date_diff = (data['date'].iloc[1] - data['date'].iloc[0]).days
            if date_diff == 1:
                self.date_freq = 'D'  # Daily
            elif date_diff == 7:
                self.date_freq = 'W'  # Weekly
            elif 28 <= date_diff <= 31:
                self.date_freq = 'M'  # Monthly
            
            logger.info(f"Detected data frequency: {self.date_freq}")
        
        # Store the last date for forecasting
        self.last_date = data['date'].iloc[-1]
        
        # Train ARIMA model
        try:
            self.model = ARIMA(data['quantity'].values, order=self.order)
            self.results = self.model.fit()
            self.is_trained = True
            logger.info(f"ARIMA model trained successfully with order {self.order}")
        except Exception as e:
            logger.error(f"Error training ARIMA model: {str(e)}")
            raise
    
    def predict(self, periods: int) -> pd.DataFrame:
        """
        Generate predictions for the specified number of periods.
        
        Args:
            periods: Number of future periods to forecast
            
        Returns:
            DataFrame with forecasted values
        """
        if not self.is_trained:
            raise ValueError("Model must be trained first")
        
        try:
            # Get forecast
            forecast = self.results.forecast(steps=periods)
            forecast_index = self._generate_future_dates(periods)
            
            # Create forecast DataFrame
            self.forecast_data = pd.DataFrame({
                'date': forecast_index,
                'forecast': forecast
            })
            
            # Get confidence intervals
            self._calculate_confidence_intervals(periods)
            
            return self.forecast_data
        except Exception as e:
            logger.error(f"Error generating ARIMA prediction: {str(e)}")
            raise
    
    def get_confidence_intervals(self) -> pd.DataFrame:
        """
        Get confidence intervals for the predictions.
        
        Returns:
            DataFrame with lower and upper confidence bounds
        """
        if self.confidence_intervals is None:
            raise ValueError("Predictions must be generated first")
        
        return self.confidence_intervals
    
    def _generate_future_dates(self, periods: int) -> pd.DatetimeIndex:
        """Generate future dates based on the frequency of historical data."""
        if self.date_freq == 'D':
            return pd.date_range(start=self.last_date + timedelta(days=1), periods=periods, freq='D')
        elif self.date_freq == 'W':
            return pd.date_range(start=self.last_date + timedelta(days=7), periods=periods, freq='W')
        elif self.date_freq == 'M':
            # For monthly data, use month_start frequency
            next_month = self.last_date.replace(day=1) + pd.DateOffset(months=1)
            return pd.date_range(start=next_month, periods=periods, freq='MS')
        else:
            # Default to daily
            return pd.date_range(start=self.last_date + timedelta(days=1), periods=periods, freq='D')
    
    def _calculate_confidence_intervals(self, periods: int, alpha: float = 0.05) -> None:
        """
        Calculate confidence intervals for the forecast.
        
        Args:
            periods: Number of periods in the forecast
            alpha: Significance level (default: 0.05 for 95% confidence)
        """
        forecast_obj = self.results.get_forecast(steps=periods)
        
        # Get confidence intervals
        conf_int = forecast_obj.conf_int(alpha=alpha)
        forecast_index = self._generate_future_dates(periods)
        
        # Create confidence intervals DataFrame
        self.confidence_intervals = pd.DataFrame(
            conf_int,
            index=forecast_index,
            columns=['lower_bound', 'upper_bound']
        ).reset_index().rename(columns={'index': 'date'})