"""Django config for the LandedCostCapacity plugin."""

from django.apps import AppConfig

class LandedCostCapacityConfig(AppConfig):
    """Config class for the LandedCostCapacity plugin."""

    name = 'landed_cost_capacity'

    def ready(self):
        """This function is called whenever the LandedCostCapacity plugin is loaded."""
        ...
