"""Gymnasium inventory environment for RL policy training.

Models a single-SKU periodic-review inventory system with:
  - Monthly decision epochs (one step = one month)
  - 3-month import lead time (orders placed now arrive in 3 months)
  - Demand sampled from the SKU's empirical distribution
  - Episode length: 36 months (3 years of simulated operations)

State vector (8 dimensions, all normalised to [0, 1]):
  [0] stock_ratio       current stock / (avg_demand × 12)
  [1] in_transit_1      order arriving in 2 months / avg_demand
  [2] in_transit_2      order arriving in 1 month  / avg_demand
  [3] demand_std_norm   demand_std_monthly / max(avg_demand, 1)
  [4] coverage_months   min(stock / avg_demand, 12) / 12
  [5] abc_code          0=A, 0.5=B, 1=C  (normalised)
  [6] tier_code         0=critical … 1=rationalise  (normalised)
  [7] cycle_frac        month_in_episode / episode_length

Action space: continuous [0.5, 1.5] — multiplier on the rule-based ROQ.
  Constrained to ±50 % of the rule-based order as required by CLAUDE.md §15.
  The environment clips any action outside this band before applying it.

Reward (negative cost):
  holding_cost    = stock × unit_value × holding_rate / 12    (per month)
  stockout_cost   = stockout_qty × unit_value × stockout_mult (per unit short)
  ordering_cost   = ordering_cost_fixed (flat fee when order > 0)

The reward is scaled by 1/unit_value so the policy generalises across SKUs
with very different price points.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import gymnasium as gym
from gymnasium import spaces

from src.config.constants import (
    DEFAULT_HOLDING_COST_RATE,
    DEFAULT_ORDERING_COST,
    LEAD_TIME_DAYS,
)

_LEAD_TIME_MONTHS: int = LEAD_TIME_DAYS // 30          # 3
_EPISODE_LENGTH:   int = 36                             # months
_STOCKOUT_MULT:    float = 5.0                          # penalty multiplier vs unit value
_OBS_DIM:          int = 8
_ROQ_MULTIPLIER_LOW:  float = 0.5
_ROQ_MULTIPLIER_HIGH: float = 1.5

_TIER_CODE: dict[str, float] = {
    "critical":    0.0,
    "managed":     0.33,
    "watch":       0.67,
    "rationalise": 1.0,
}
_ABC_CODE: dict[str, float] = {"A": 0.0, "B": 0.5, "C": 1.0}


class InventoryEnv(gym.Env):
    """Single-SKU inventory environment for PPO training.

    Each environment instance represents one SKU's inventory dynamics.
    For cross-SKU policy learning, use VecEnv to run many instances in parallel.

    Args:
        avg_demand:    average monthly demand (units).
        demand_std:    monthly demand standard deviation.
        p_zero:        probability of a zero-demand month (intermittency).
        base_roq:      rule-based reorder quantity (units) — action is a multiplier on this.
        rol:           reorder level (units) — triggers order if stock ≤ rol.
        unit_value:    LKR value per unit — scales holding and stockout costs.
        abc:           'A', 'B', or 'C'.
        policy_tier:   'critical', 'managed', 'watch', or 'rationalise'.
        seed:          random seed for reproducibility.
    """

    metadata = {"render_modes": []}

    def __init__(
        self,
        avg_demand:  float,
        demand_std:  float,
        p_zero:      float,
        base_roq:    float,
        rol:         float,
        unit_value:  float,
        abc:         str = "B",
        policy_tier: str = "managed",
        seed:        int | None = None,
    ) -> None:
        super().__init__()
        self.avg_demand  = max(avg_demand, 0.01)
        self.demand_std  = max(demand_std, 0.0)
        self.p_zero      = float(np.clip(p_zero, 0.0, 1.0))
        self.base_roq    = max(base_roq, 1.0)
        self.rol         = max(rol, 0.0)
        self.unit_value  = max(unit_value, 1.0)
        self.abc         = abc
        self.policy_tier = policy_tier
        self._rng        = np.random.default_rng(seed)

        # Precomputed cost parameters
        self._hold_rate_monthly = DEFAULT_HOLDING_COST_RATE / 12.0
        self._ordering_cost     = DEFAULT_ORDERING_COST
        self._stockout_cost_pu  = unit_value * _STOCKOUT_MULT

        # normalisation anchors
        self._annual_demand = self.avg_demand * 12.0

        self.observation_space = spaces.Box(
            low=0.0, high=1.0, shape=(_OBS_DIM,), dtype=np.float32
        )
        self.action_space = spaces.Box(
            low=np.float32(_ROQ_MULTIPLIER_LOW),
            high=np.float32(_ROQ_MULTIPLIER_HIGH),
            shape=(1,),
            dtype=np.float32,
        )

        self._stock: float = 0.0
        self._pipeline: list[float] = [0.0] * _LEAD_TIME_MONTHS
        self._t: int = 0

    # ------------------------------------------------------------------
    def reset(
        self,
        *,
        seed: int | None = None,
        options: dict | None = None,
    ) -> tuple[np.ndarray, dict]:
        if seed is not None:
            self._rng = np.random.default_rng(seed)
        # Initialise at a random stock level between 0 and 6 months of supply
        self._stock    = float(self._rng.uniform(0.0, self.avg_demand * 6.0))
        self._pipeline = [0.0] * _LEAD_TIME_MONTHS
        self._t        = 0
        return self._obs(), {}

    # ------------------------------------------------------------------
    def step(self, action: np.ndarray) -> tuple[np.ndarray, float, bool, bool, dict]:
        multiplier = float(np.clip(action[0], _ROQ_MULTIPLIER_LOW, _ROQ_MULTIPLIER_HIGH))

        # Place order if stock at or below ROL
        order_qty = 0.0
        if self._stock <= self.rol:
            order_qty = round(self.base_roq * multiplier)
        self._pipeline.append(order_qty)

        # Receive order that was placed 3 months ago
        arriving = self._pipeline.pop(0)
        self._stock += arriving

        # Sample demand
        demand = self._sample_demand()

        # Compute shortfall
        shortfall = max(0.0, demand - self._stock)
        self._stock = max(0.0, self._stock - demand)

        # Costs (negative reward)
        holding_cost  = self._stock    * self.unit_value * self._hold_rate_monthly
        stockout_cost = shortfall      * self._stockout_cost_pu
        order_cost    = self._ordering_cost if order_qty > 0 else 0.0
        total_cost    = holding_cost + stockout_cost + order_cost

        # Scale reward by unit_value so cross-SKU training is stable
        reward = -total_cost / self.unit_value

        self._t += 1
        terminated = self._t >= _EPISODE_LENGTH
        info = {
            "stock": self._stock,
            "demand": demand,
            "order_qty": order_qty,
            "shortfall": shortfall,
            "holding_cost": holding_cost,
            "stockout_cost": stockout_cost,
        }
        return self._obs(), reward, terminated, False, info

    # ------------------------------------------------------------------
    def _sample_demand(self) -> float:
        """Sample one month's demand using a zero-inflated Poisson model."""
        if self._rng.random() < self.p_zero:
            return 0.0
        # Use Poisson for integer demands, clipped at 0
        lam = max(self.avg_demand / (1.0 - self.p_zero + 1e-9), 0.01)
        return float(self._rng.poisson(lam))

    # ------------------------------------------------------------------
    def _obs(self) -> np.ndarray:
        ann = self._annual_demand
        obs = np.array([
            min(self._stock / ann, 1.0)                           if ann > 0 else 0.0,
            min(self._pipeline[0] / self.avg_demand, 1.0)         if self.avg_demand > 0 else 0.0,
            min(self._pipeline[1] / self.avg_demand, 1.0)         if self.avg_demand > 0 else 0.0,
            min(self.demand_std / max(self.avg_demand, 1.0), 1.0),
            min(self._stock / (self.avg_demand * 12.0), 1.0)      if self.avg_demand > 0 else 0.0,
            _ABC_CODE.get(self.abc, 0.5),
            _TIER_CODE.get(self.policy_tier, 1.0),
            self._t / _EPISODE_LENGTH,
        ], dtype=np.float32)
        return np.clip(obs, 0.0, 1.0)
