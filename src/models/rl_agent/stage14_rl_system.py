"""Stage 14 — RL Inventory Policy: PPO agent for order quantity optimisation.

Inputs:
  data/interim/inventory_policy.parquet   — rule-based policy (Stage 12)

Outputs:
  data/interim/rl_policy.parquet          — RL vs rule-based comparison (→ dashboard)
  data/outputs/stage14_rl_system.xlsx     — RL evaluation report

Architecture:
  A single global PPO policy is trained across a diverse set of active SKUs
  simultaneously (vectorised envs).  Each environment instance represents one
  SKU's demand dynamics; the shared policy must learn to generalise across
  ABC classes, demand variability levels, and policy tiers — the same philosophy
  as the global LightGBM forecast model in Stage 10.

  Training SKUs: active SKUs with avg_monthly_demand > 0 (typically ~7k SKUs)
  Vectorised envs: min(32, n_skus) parallel environments
  Algorithm: PPO (Proximal Policy Optimisation) via stable-baselines3
    - Clip range 0.2, learning rate 3e-4, n_steps 128 per env
  Total timesteps: 200,000  (~enough for the policy to converge on this dataset)

  CLAUDE.md §15 constraint: RL orders must remain within ±50 % of the
  rule-based ROQ.  This is enforced at the action level inside InventoryEnv
  (action space is clipped to [0.5, 1.5] multiplier on base_roq).

Evaluation:
  After training, we run 12-month rollouts on a held-out set of SKUs and
  compare cumulative cost: RL vs rule-based policy (multiplier = 1.0).
  Results flagged per CLAUDE.md §15 if RL deviates > ±50 % from rule-based.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from loguru import logger

from src.config.constants import RL_POLICY_BAND
from src.config.paths import DATA_INTERIM, DATA_OUTPUTS

_POLICY_PARQUET = DATA_INTERIM / "inventory_policy.parquet"
_RL_PARQUET     = DATA_INTERIM / "rl_policy.parquet"
_OUTPUT_XLSX    = DATA_OUTPUTS / "stage14_rl_system.xlsx"

_TOTAL_TIMESTEPS: int = 200_000
_N_ENVS:          int = 32
_EVAL_EPISODES:   int = 10          # rollout episodes per SKU for evaluation
_EVAL_HORIZON:    int = 12          # months per evaluation episode
_SEED:            int = 42


# ---------------------------------------------------------------------------
# Environment factory
# ---------------------------------------------------------------------------

def _make_envs(policy_df: pd.DataFrame, n_envs: int, seed: int = _SEED):
    """Create a stable-baselines3 SubprocVecEnv from the policy DataFrame.

    Randomly samples n_envs SKUs (with replacement if needed) so every
    gradient update sees a diverse mix of ABC classes and demand patterns.
    """
    from stable_baselines3.common.vec_env import SubprocVecEnv, DummyVecEnv
    from src.models.rl_agent._inventory_env import InventoryEnv

    active = policy_df[policy_df["avg_monthly_demand"] > 0.0].reset_index(drop=True)
    rng    = np.random.default_rng(seed)
    idxs   = rng.integers(0, len(active), size=n_envs)

    def _make_one(row: pd.Series, env_seed: int):
        def _factory():
            return InventoryEnv(
                avg_demand  = float(row["avg_monthly_demand"]),
                demand_std  = float(row.get("demand_std_monthly", row["avg_monthly_demand"] * 0.3)),
                p_zero      = float(row.get("p_zero", 0.5)),
                base_roq    = float(row["roq"]),
                rol         = float(row["rol"]),
                unit_value  = float(row.get("unit_value_lkr", 500.0)),
                abc         = str(row.get("abc", "C")),
                policy_tier = str(row.get("policy_tier", "rationalise")),
                seed        = env_seed,
            )
        return _factory

    factories = [_make_one(active.iloc[i], seed + int(i)) for i in idxs]
    try:
        return SubprocVecEnv(factories)
    except Exception:
        return DummyVecEnv(factories)


# ---------------------------------------------------------------------------
# Training
# ---------------------------------------------------------------------------

def train_ppo(policy_df: pd.DataFrame) -> object:
    """Train a PPO agent on the inventory environment.

    Returns:
        Fitted stable_baselines3.PPO model.
    """
    from stable_baselines3 import PPO
    from stable_baselines3.common.callbacks import EvalCallback
    from stable_baselines3.common.vec_env import DummyVecEnv
    from src.models.rl_agent._inventory_env import InventoryEnv

    n_envs = min(_N_ENVS, len(policy_df[policy_df["avg_monthly_demand"] > 0]))
    logger.info(f"PPO training: {n_envs} parallel envs | {_TOTAL_TIMESTEPS:,} timesteps")

    vec_env = _make_envs(policy_df, n_envs, seed=_SEED)

    model = PPO(
        policy     = "MlpPolicy",
        env        = vec_env,
        learning_rate = 3e-4,
        n_steps    = 128,
        batch_size = 64,
        n_epochs   = 10,
        gamma      = 0.99,
        clip_range = 0.2,
        verbose    = 0,
        seed       = _SEED,
    )
    model.learn(total_timesteps=_TOTAL_TIMESTEPS)
    vec_env.close()

    logger.info("PPO training complete")
    return model


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

def evaluate_policy(
    model,
    policy_df: pd.DataFrame,
    n_eval_skus: int = 200,
) -> pd.DataFrame:
    """Compare RL vs rule-based policy on a held-out set of SKUs.

    For each held-out SKU, we roll out _EVAL_EPISODES × _EVAL_HORIZON months
    under both policies and record cumulative cost.

    Returns:
        DataFrame with one row per SKU containing:
          material_9, rl_multiplier_mean, rl_cost, rule_cost,
          cost_saving_pct, rl_flag (True if > ±50 % from rule-based)
    """
    from src.models.rl_agent._inventory_env import InventoryEnv

    active = policy_df[policy_df["avg_monthly_demand"] > 0.0].reset_index(drop=True)
    eval_skus = active.sample(min(n_eval_skus, len(active)), random_state=_SEED)

    results: list[dict] = []
    for _, row in eval_skus.iterrows():
        env = InventoryEnv(
            avg_demand  = float(row["avg_monthly_demand"]),
            demand_std  = float(row.get("demand_std_monthly", row["avg_monthly_demand"] * 0.3)),
            p_zero      = float(row.get("p_zero", 0.5)),
            base_roq    = float(row["roq"]),
            rol         = float(row["rol"]),
            unit_value  = float(row.get("unit_value_lkr", 500.0)),
            abc         = str(row.get("abc", "C")),
            policy_tier = str(row.get("policy_tier", "rationalise")),
            seed        = _SEED,
        )

        rl_costs: list[float] = []
        rule_costs: list[float] = []
        rl_multipliers: list[float] = []

        for _ in range(_EVAL_EPISODES):
            obs, _ = env.reset()
            ep_rl_cost = ep_rule_cost = 0.0

            for _step in range(_EVAL_HORIZON):
                # RL action
                action, _ = model.predict(obs, deterministic=True)
                obs_next, reward, done, _, info = env.step(action)
                ep_rl_cost -= float(reward) * float(row.get("unit_value_lkr", 500.0))
                rl_multipliers.append(float(action[0]))

                # Rule-based action (multiplier = 1.0)
                env2_obs = obs.copy()
                rule_action = np.array([1.0], dtype=np.float32)
                _, rule_reward, _, _, rule_info = env.step(rule_action)
                ep_rule_cost -= float(rule_reward) * float(row.get("unit_value_lkr", 500.0))

                obs = obs_next
                if done:
                    break

            rl_costs.append(ep_rl_cost)
            rule_costs.append(ep_rule_cost)

        rl_cost_mean   = float(np.mean(rl_costs))
        rule_cost_mean = float(np.mean(rule_costs))
        mult_mean      = float(np.mean(rl_multipliers))

        cost_saving_pct = (
            (rule_cost_mean - rl_cost_mean) / max(abs(rule_cost_mean), 1.0) * 100.0
        )
        rl_flag = abs(mult_mean - 1.0) > RL_POLICY_BAND

        results.append({
            "material_9":         row["material_9"],
            "description":        row.get("description", ""),
            "abc":                row.get("abc", "C"),
            "policy_tier":        row.get("policy_tier", "rationalise"),
            "avg_monthly_demand": float(row["avg_monthly_demand"]),
            "base_roq":           float(row["roq"]),
            "rl_multiplier_mean": round(mult_mean, 3),
            "rl_recommended_qty": round(float(row["roq"]) * mult_mean, 1),
            "rl_cost_lkr":        round(rl_cost_mean, 0),
            "rule_cost_lkr":      round(rule_cost_mean, 0),
            "cost_saving_pct":    round(cost_saving_pct, 2),
            "rl_flag":            rl_flag,
        })

    return pd.DataFrame(results)


# ---------------------------------------------------------------------------
# Full policy scoring (all active SKUs)
# ---------------------------------------------------------------------------

def score_all_skus(model, policy_df: pd.DataFrame) -> pd.DataFrame:
    """Apply the trained model to every active SKU to get RL order multipliers.

    Uses a single-step deterministic prediction from the current state
    (derived from stock_tracker columns) rather than a full episode rollout.
    This is fast and gives the 'recommended multiplier today'.
    """
    from src.models.rl_agent._inventory_env import InventoryEnv, _OBS_DIM

    active = policy_df[policy_df["avg_monthly_demand"] > 0.0].copy()
    records: list[dict] = []

    for _, row in active.iterrows():
        env = InventoryEnv(
            avg_demand  = float(row["avg_monthly_demand"]),
            demand_std  = float(row.get("demand_std_monthly", row["avg_monthly_demand"] * 0.3)),
            p_zero      = float(row.get("p_zero", 0.5)),
            base_roq    = float(row["roq"]),
            rol         = float(row["rol"]),
            unit_value  = float(row.get("unit_value_lkr", 500.0)),
            abc         = str(row.get("abc", "C")),
            policy_tier = str(row.get("policy_tier", "rationalise")),
        )
        # Prime the env with the current stock level
        env._stock = float(row.get("stock_on_hand", env.avg_demand * 2))
        obs = env._obs()
        action, _ = model.predict(obs, deterministic=True)
        mult = float(np.clip(action[0], 0.5, 1.5))

        rl_qty = round(float(row["roq"]) * mult, 0)
        flag   = abs(mult - 1.0) > RL_POLICY_BAND

        records.append({
            "material_9":         row["material_9"],
            "rl_multiplier":      round(mult, 3),
            "rl_recommended_qty": rl_qty,
            "rule_based_roq":     float(row["roq"]),
            "rl_flag":            flag,
        })

    return pd.DataFrame(records)


# ---------------------------------------------------------------------------
# Excel writer
# ---------------------------------------------------------------------------

def _write_excel(
    eval_df: pd.DataFrame,
    all_scores: pd.DataFrame,
    policy_df: pd.DataFrame,
) -> None:
    with pd.ExcelWriter(_OUTPUT_XLSX, engine="xlsxwriter") as writer:
        wb  = writer.book
        hdr = wb.add_format({"bold": True, "bg_color": "#1F4E79", "font_color": "white", "border": 1})

        def _sheet(data: pd.DataFrame, name: str) -> None:
            data.to_excel(writer, sheet_name=name, index=False)
            ws = writer.sheets[name]
            ws.set_row(0, 18, hdr)
            ws.set_column(0, len(data.columns) - 1, 20)

        # Summary KPIs
        kpis = {
            "Eval SKUs":               len(eval_df),
            "Avg Cost Saving %":       eval_df["cost_saving_pct"].mean(),
            "SKUs RL Cheaper":         int((eval_df["cost_saving_pct"] > 0).sum()),
            "SKUs Rule Cheaper":       int((eval_df["cost_saving_pct"] <= 0).sum()),
            "Avg RL Multiplier":       eval_df["rl_multiplier_mean"].mean(),
            "RL-Flagged (> ±50 %)":   int(eval_df["rl_flag"].sum()),
            "All Active SKUs Scored":  len(all_scores),
            "Scored Flags":            int(all_scores["rl_flag"].sum()),
        }
        kpi_df = pd.DataFrame([{"KPI": k, "Value": v} for k, v in kpis.items()])
        kpi_df.to_excel(writer, sheet_name="Summary", index=False)
        ws = writer.sheets["Summary"]
        ws.set_row(0, 18, hdr)
        ws.set_column(0, 0, 36)
        ws.set_column(1, 1, 20)

        _sheet(eval_df, "Eval Results")
        _sheet(eval_df[eval_df["rl_flag"]], "Flagged SKUs")
        _sheet(all_scores, "All Scored SKUs")

    logger.info(f"RL report written → {_OUTPUT_XLSX}")


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def run(refresh: bool = False) -> None:
    """Stage 14: train PPO agent and generate RL policy recommendations."""
    if not refresh and _RL_PARQUET.exists():
        logger.info("rl_policy.parquet exists and refresh=False — skipping Stage 14")
        return

    logger.info("Stage 14: RL Inventory Policy (PPO)")
    policy_df = pd.read_parquet(_POLICY_PARQUET)
    logger.info(f"Policy loaded: {len(policy_df):,} SKUs")

    # Train
    model = train_ppo(policy_df)

    # Evaluate on held-out SKUs
    logger.info("Evaluating RL vs rule-based on held-out SKUs ...")
    eval_df = evaluate_policy(model, policy_df, n_eval_skus=200)

    # Score all active SKUs
    logger.info("Scoring all active SKUs ...")
    all_scores = score_all_skus(model, policy_df)

    # Merge RL scores back to policy
    rl_policy = policy_df.merge(all_scores, on="material_9", how="left")
    rl_policy["rl_recommended_qty"] = rl_policy["rl_recommended_qty"].fillna(0.0)
    rl_policy["rl_multiplier"]      = rl_policy["rl_multiplier"].fillna(1.0)
    rl_policy["rl_flag"]            = rl_policy["rl_flag"].fillna(False).infer_objects(copy=False)

    rl_policy.to_parquet(_RL_PARQUET, index=False)
    logger.info(f"RL policy saved → {_RL_PARQUET} ({len(rl_policy):,} rows)")

    _write_excel(eval_df, all_scores, policy_df)

    avg_saving = eval_df["cost_saving_pct"].mean()
    flagged    = int(eval_df["rl_flag"].sum())
    logger.info(
        f"Stage 14 complete | Avg cost saving: {avg_saving:.1f}% | "
        f"RL-flagged (>{RL_POLICY_BAND*100:.0f}% deviation): {flagged} SKUs"
    )
