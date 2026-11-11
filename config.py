"""Single source of truth for the pilot. Change values HERE only, never in the scripts."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RAW_DIR = ROOT / "data" / "raw"
OUT_DIR = ROOT / "outputs"

# ---- Reproducibility ----
SEED = 42
N_PILOT = 10          # final number of position-move pairs in the pilot
N_CANDIDATES = 40     # oversample: some candidates get dropped by the engine filters (step 3)

# ---- Data source (pin ONE monthly file; 2013-01 is tiny, ~17 MB) ----
LICHESS_MONTH = "2013-01"
LICHESS_FILE = f"lichess_db_standard_rated_{LICHESS_MONTH}.pgn.zst"
LICHESS_URL = f"https://database.lichess.org/standard/{LICHESS_FILE}"

# ---- Inclusion criteria (same for every position) ----
ELO_MIN, ELO_MAX = 1800, 2200     # BOTH players inside this range
MIN_EST_SECONDS = 480             # base + 40*increment >= 480 s -> Lichess "rapid" or slower
REQUIRE_NORMAL_TERMINATION = True # no abandoned / time-forfeit games
MIN_PLIES = 60                    # game must be long enough
FULLMOVE_MIN, FULLMOVE_MAX = 10, 30  # position BEFORE the move: past opening, before long endgames
# applied in step 3 (need the engine)
MAX_ABS_EVAL_CP = 500             # drop already-decided positions
MAX_CP_LOSS = 30                  # drop candidate moves that lose > 30 cp vs engine best

# ---- Candidate move selection rule ----
CANDIDATE_RULE = "human_move_played_in_game + centipawn-loss filter (<= MAX_CP_LOSS)"

# ---- Stockfish (step 3). Version string is read from the engine and logged automatically ----
STOCKFISH_DEPTH = 18
STOCKFISH_THREADS = 1             # 1 thread + fixed depth = deterministic
STOCKFISH_HASH_MB = 128
STOCKFISH_MULTIPV = 1

# ---- Generating LLM (step 4). Open-weight, run locally with Ollama ----
LLM_BACKEND = "ollama"
LLM_MODEL = "llama3.1:8b"         # pick one your machine can run; the model digest is logged
LLM_TEMPERATURE = 0.0
LLM_SEED = SEED
LLM_MAX_TOKENS = 400
