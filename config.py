"""Single source of truth. Change values HERE only (notebooks import this)."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RAW_DIR = ROOT / "data" / "raw"
OUT_DIR = ROOT / "outputs"

SEED = 42

# ---- Data source (one pinned monthly Lichess file; keep the .sha256 in the manifest) ----
LICHESS_MONTH = "2026-10"
RAW_FILE = f"lichess_db_standard_rated_{LICHESS_MONTH}.pgn.zst"
LICHESS_URL = f"https://database.lichess.org/standard/{RAW_FILE}"

# ---- Game-level inclusion criteria (step 1) ----
ELO_MIN = 2600            # BOTH players
MAX_ELO_GAP = 100
MIN_EST_SECONDS = 480     # base + 40*increment >= 480 s  -> Lichess "rapid" or slower
MIN_PLIES = 60

# ---- Move-level rules (steps 2 and 3) ----
FULLMOVE_MIN, FULLMOVE_MAX = 10, 30   # position BEFORE the move
MAX_ABS_EVAL_CP = 500                 # |eval before| <= 500, no mate scores
GOOD_MAX_CP_LOSS = 30                 # good move: loses <= 30 cp
METRIC = "cp"                         # "cp" or "wp" (win-probability points) for BAD moves
BAD_MIN_CP_LOSS = 100                 # used when METRIC == "cp"
BAD_MIN_WP_LOSS = 10                  # used when METRIC == "wp"

# ---- Stockfish (step 3) ----
STOCKFISH_PATH = os.environ.get("STOCKFISH_PATH", r"stockfish\stockfish-windows-x86-64-universal.exe")
STOCKFISH_DEPTH = 18
STOCKFISH_THREADS = 1
STOCKFISH_HASH_MB = 128
STOCKFISH_MULTIPV = 1
PV_MAX_PLIES = 8
