import pandas as pd
import numpy as np
from typing import Dict, Any, List, Optional, Tuple
import logging
import math

# Configure logging
logger = logging.getLogger(__name__)

class InventoryOptimizer:
    """
    Inventory optimization engine that calculates optimal order quantities,
    reorder points, and safety stock levels based on forecasts and business constraints.
    """
    
    def __init__(self, holding_cost_pct: float = 0.25, ordering_cost: float = 20.0):
        """
        Initialize the inventory optimizer.
        
        Args:
            holding_cost_pct: Annual inventory holding cost as a percentage of item value (default: 25%)
            ordering_cost: Fixed cost of placing an order (default: $20)
        """
        self.holding_cost_pct = holding_cost_pct
        self.ordering_cost = ordering_cost
    
    def calculate_eoq(self, annual_demand: float, unit_cost: float) -> float:
        """
        Calculate the Economic Order Quantity (EOQ) using the classic Wilson formula.
        
        Args:
            annual_demand: Annual demand in units
            unit_cost: Cost per unit
            
        Returns:
            Economic Order Quantity (EOQ)
        """
        if annual_demand <= 0 or unit_cost <= 0:
            return 0
        
        # Calculate holding cost per unit per year
        holding_cost = unit_cost * self.holding_cost_pct
        
        # Wilson formula: EOQ = sqrt(2 * D * K / h)
        # where D = annual demand, K = ordering cost, h = holding cost per unit
        eoq = math.sqrt((2 * annual_demand * self.ordering_cost) / holding_cost)
        
        return math.ceil(eoq)
    
    def calculate_reorder_point(self, avg_daily_demand: float, lead_time_days: float, 
                              service_level: float = 0.95) -> int:
        """
        Calculate the reorder point based on lead time and desired service level.
        
        Args:
            avg_daily_demand: Average daily demand
            lead_time_days: Lead time in days
            service_level: Desired service level (default: 95%)
            
        Returns:
            Reorder point in units
        """
        if avg_daily_demand <= 0 or lead_time_days <= 0:
            return 0
        
        # Average demand during lead time
        lead_time_demand = avg_daily_demand * lead_time_days
        
        # Calculate safety stock
        safety_stock = self.calculate_safety_stock(avg_daily_demand, lead_time_days, service_level)
        
        # Reorder point = lead time demand + safety stock
        reorder_point = lead_time_demand + safety_stock
        
        return math.ceil(reorder_point)
    
    def calculate_safety_stock(self, avg_daily_demand: float, lead_time_days: float, 
                             service_level: float = 0.95, demand_std_dev: Optional[float] = None) -> float:
        """
        Calculate safety stock based on service level and variability.
        
        Args:
            avg_daily_demand: Average daily demand
            lead_time_days: Lead time in days
            service_level: Desired service level (default: 95%)
            demand_std_dev: Standard deviation of daily demand (if None, estimated from avg_daily_demand)
            
        Returns:
            Safety stock in units
        """
        if avg_daily_demand <= 0 or lead_time_days <= 0:
            return 0
        
        # If standard deviation not provided, estimate it (assuming coefficient of variation of 0.3)
        if demand_std_dev is None:
            demand_std_dev = avg_daily_demand * 0.3
        
        # Calculate Z-score for given service level
        # Common values: 1.645 (95%), 2.326 (99%), 2.576 (99.5%)
        if service_level >= 0.99:
            z_score = 2.326
        elif service_level >= 0.95:
            z_score = 1.645
        else:
            z_score = 1.282  # 90%
        
        # Safety stock = Z * standard deviation of demand during lead time
        # Standard deviation during lead time = sqrt(L) * daily standard deviation
        safety_stock = z_score * demand_std_dev * math.sqrt(lead_time_days)
        
        return safety_stock
    
    def optimize_inventory_levels(self, product_data: Dict[str, Any], forecast_data: pd.DataFrame,
                                 supplier_data: Dict[str, Any], constraints: Dict[str, Any]) -> Dict[str, Any]:
        """
        Calculate optimal inventory levels based on forecasts and constraints.
        
        Args:
            product_data: Product information including cost, current stock
            forecast_data: DataFrame with demand forecasts
            supplier_data: Supplier information including lead time
            constraints: Business constraints like budget, storage capacity
            
        Returns:
            Dictionary with optimization results
        """
        try:
            # Extract relevant data
            unit_cost = product_data.get('unit_price', 0)
            current_stock = product_data.get('current_stock', 0)
            lead_time_days = supplier_data.get('lead_time', 7)
            reliability = supplier_data.get('reliability_score', 0.9)
            
            # Calculate daily demand from forecast
            if len(forecast_data) > 0:
                avg_daily_demand = forecast_data['forecast'].mean()
                max_daily_demand = forecast_data['forecast'].max()
                
                # If we have confidence intervals, use those for variability
                if 'lower_bound' in forecast_data.columns and 'upper_bound' in forecast_data.columns:
                    demand_variability = (forecast_data['upper_bound'] - forecast_data['lower_bound']).mean() / 4
                else:
                    demand_variability = forecast_data['forecast'].std()
            else:
                logger.warning("Empty forecast data, using defaults")
                avg_daily_demand = 1
                max_daily_demand = 2
                demand_variability = 0.5
            
            # Annual demand (for EOQ calculation)
            annual_demand = avg_daily_demand * 365
            
            # Calculate EOQ
            eoq = self.calculate_eoq(annual_demand, unit_cost)
            
            # Adjust for supplier reliability
            adjusted_lead_time = lead_time_days / reliability
            
            # Calculate reorder point
            reorder_point = self.calculate_reorder_point(
                avg_daily_demand, adjusted_lead_time, 
                service_level=constraints.get('service_level', 0.95),
                demand_std_dev=demand_variability
            )
            
            # Calculate safety stock
            safety_stock = self.calculate_safety_stock(
                avg_daily_demand, adjusted_lead_time,
                service_level=constraints.get('service_level', 0.95),
                demand_std_dev=demand_variability
            )
            
            # Maximum stock level
            max_stock = reorder_point + eoq
            
            # Order recommendation
            order_recommendation = 0
            if current_stock <= reorder_point:
                order_recommendation = eoq
            
            # Calculate days of supply
            days_of_supply = current_stock / avg_daily_demand if avg_daily_demand > 0 else 0
            
            # Probability of stockout
            stockout_prob = self._calculate_stockout_probability(
                current_stock, avg_daily_demand, demand_variability, lead_time_days
            )
            
            # Results
            results = {
                'economic_order_quantity': int(eoq),
                'reorder_point': int(reorder_point),
                'safety_stock': int(safety_stock),
                'max_stock_level': int(max_stock),
                'current_stock': int(current_stock),
                'order_recommendation': int(order_recommendation),
                'days_of_supply': round(days_of_supply, 1),
                'avg_daily_demand': round(avg_daily_demand, 2),
                'stockout_probability': round(stockout_prob * 100, 2),  # as percentage
                'annual_holding_cost': round(max_stock * unit_cost * self.holding_cost_pct, 2),
                'order_cycle_days': round(eoq / avg_daily_demand if avg_daily_demand > 0 else 0, 1)
            }
            
            return results
        
        except Exception as e:
            logger.error(f"Error in inventory optimization: {str(e)}")
            # Return default values
            return {
                'economic_order_quantity': 0,
                'reorder_point': 0,
                'safety_stock': 0,
                'max_stock_level': 0,
                'current_stock': current_stock,
                'order_recommendation': 0,
                'days_of_supply': 0,
                'avg_daily_demand': 0,
                'stockout_probability': 100,
                'annual_holding_cost': 0,
                'order_cycle_days': 0,
                'error': str(e)
            }
    
    def _calculate_stockout_probability(self, current_stock: float, avg_demand: float, 
                                      demand_std_dev: float, lead_time: float) -> float:
        """
        Calculate the probability of stockout given current inventory levels.
        
        Returns a probability between 0 and 1.
        """
        if avg_demand <= 0:
            return 0.0 if current_stock > 0 else 1.0
        
        # Expected demand during lead time
        expected_demand = avg_demand * lead_time
        
        # If current stock exceeds expected demand by a large margin, probability is near zero
        if current_stock >= expected_demand + 3 * demand_std_dev * math.sqrt(lead_time):
            return 0.0
        
        # If current stock is much less than expected demand, probability is near one
        if current_stock <= expected_demand - 3 * demand_std_dev * math.sqrt(lead_time):
            return 1.0
        
        # Calculate Z-score: (current_stock - expected_demand) / std_dev_during_lead_time
        z_score = (current_stock - expected_demand) / (demand_std_dev * math.sqrt(lead_time))
        
        # Approximate stockout probability using normal distribution
        # This is a simple approximation of the cumulative normal distribution
        if z_score < 0:
            prob = 0.5 + 0.5 * (1 - math.exp(-0.7 * z_score * z_score)) * (-1 if z_score < 0 else 1)
        else:
            prob = 0.5 - 0.5 * (1 - math.exp(-0.7 * z_score * z_score))
        
        return prob
    
    def generate_transfer_recommendations(self, locations_inventory: List[Dict[str, Any]], 
                                        forecast_data: Dict[str, pd.DataFrame]) -> List[Dict[str, Any]]:
        """
        Generate recommendations for inventory transfers between locations.
        
        Args:
            locations_inventory: List of dictionaries with location inventory data
            forecast_data: Dictionary mapping location IDs to forecast DataFrames
            
        Returns:
            List of transfer recommendations
        """
        recommendations = []
        
        # Group by product
        products = {}
        for loc_inv in locations_inventory:
            location_id = loc_inv.get('location_id')
            for item in loc_inv.get('inventory', []):
                product_id = item.get('product_id')
                if product_id not in products:
                    products[product_id] = []
                products[product_id].append({
                    'location_id': location_id,
                    'quantity': item.get('quantity', 0),
                    'forecast': forecast_data.get(location_id, {}).get(product_id)
                })
        
        # For each product, find imbalances between locations
        for product_id, locations in products.items():
            if len(locations) < 2:
                continue
            
            # Calculate days of supply for each location
            for loc in locations:
                avg_demand = 1  # Default if no forecast
                if loc.get('forecast') is not None and len(loc.get('forecast', [])) > 0:
                    avg_demand = loc['forecast']['forecast'].mean()
                
                loc['days_supply'] = loc['quantity'] / avg_demand if avg_demand > 0 else float('inf')
            
            # Sort by days of supply (ascending)
            locations.sort(key=lambda x: x.get('days_supply', 0))
            
            # If significant imbalance exists, recommend transfer
            if len(locations) >= 2:
                lowest = locations[0]
                highest = locations[-1]
                
                # Only recommend if there's a significant imbalance
                if highest.get('days_supply', 0) > 2 * lowest.get('days_supply', 0) and lowest.get('days_supply', 0) < 7:
                    # Calculate transfer quantity
                    avg_demand_low = 1
                    if lowest.get('forecast') is not None and len(lowest.get('forecast', [])) > 0:
                        avg_demand_low = lowest['forecast']['forecast'].mean()
                    
                    # Transfer enough to equalize days of supply, but not too much
                    transfer_qty = min(
                        highest['quantity'] // 2,  # Don't transfer more than half
                        int(avg_demand_low * 7)    # About a week's worth of demand
                    )
                    
                    if transfer_qty > 0:
                        recommendations.append({
                            'product_id': product_id,
                            'from_location': highest['location_id'],
                            'to_location': lowest['location_id'],
                            'quantity': transfer_qty,
                            'reason': f"Balancing inventory: {highest['days_supply']:.1f} days vs {lowest['days_supply']:.1f} days",
                            'priority': 'HIGH' if lowest['days_supply'] < 3 else 'MEDIUM'
                        })
        
        return recommendations