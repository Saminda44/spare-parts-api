"""Modular pipeline wrappers for the Yamaha spare-parts system.

Each module is a self-contained class that wraps one or more pipeline stages
and exposes a single ``run()`` method returning a typed dataclass result.

Modules:
    1 — VehicleIntelligence  (sales forecast, UIO forecast, age distribution)
"""
