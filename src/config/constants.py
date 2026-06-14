"""Business and system constants. No magic numbers elsewhere."""

# ── Lead time & ordering ──────────────────────────────────────
LEAD_TIME_DAYS: int = 90          # India → Sri Lanka
ORDER_CYCLE_DAYS: int = 30        # roughly monthly

# ── Inventory policy defaults ─────────────────────────────────
DEFAULT_SERVICE_LEVEL: float = 0.95
DEFAULT_HOLDING_COST_RATE: float = 0.20   # 20 % of unit value p.a.
DEFAULT_ORDERING_COST: float = 5_000.0    # LKR per purchase order

# ── Document type codes ───────────────────────────────────────
PO_DOC_PREFIX: str = "4"       # Sales Document starting with 4 = purchase order
RETURN_DOC_PREFIX: str = "6"   # Sales Document starting with 6 = return

# ── Data quality thresholds ───────────────────────────────────
PDF_CONFIDENCE_THRESHOLD: float = 0.85
ROL_ROQ_SANITY_MULTIPLIER: float = 3.0    # flag if ROL/ROQ > 3× recent demand
RL_POLICY_BAND: float = 0.50              # RL order must be within ±50% of rule-based
DEALER_RETURN_FRAUD_THRESHOLD: float = 0.40  # flag if single dealer > 40% return value

# ── File size limits ──────────────────────────────────────────
PDF_MAX_SIZE_BYTES: int = 50 * 1024 * 1024   # 50 MB

# ── Timezone / currency ───────────────────────────────────────
TIMEZONE: str = "Asia/Colombo"   # UTC+05:30
CURRENCY: str = "LKR"

# ── Supersession ──────────────────────────────────────────────
MAX_SUPERSESSION_DEPTH: int = 10  # cycle guard
