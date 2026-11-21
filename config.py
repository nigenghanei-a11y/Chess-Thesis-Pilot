"""Single source of truth for the thesis pipeline (steps 1 onwards).
Every step imports this file and writes config_snapshot() + the git commit into its manifest.
Scientific decisions live HERE; step-local knobs (test limits, checkpoint intervals) stay in the notebook/script.
Change a value -> re-run the affected steps -> the new manifests show what was used."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RAW_DIR, OUT_DIR = ROOT / "data" / "raw", ROOT / "outputs"

# ---- Data source (pinned) ----
MONTH = "2026-10"
BASE_URL = "https://database.lichess.org/standard/"
LICHESS_FILE = f"lichess_db_standard_rated_{MONTH}.pgn.zst"

# ---- Seeds: one per purpose, never reused ----
SEED_SELECTION = 42          # which eligible position is taken from each game
SEED_PILOT_SPLIT = 43        # which positions go to the pilot (kept OUT of the final dataset)
SEED_ANNOTATION = 44         # stratified claim subset for the second annotator
SEED_BOOTSTRAP = 45          # cluster bootstrap in the analysis
SEED_LLM = 46                # generation seed

# ---- Game-level criteria ----
ELO_MIN, MAX_RATING_GAP = 2600, 100
MIN_EST_SECONDS = 480        # base + 40 * increment (rapid or slower)
REQUIRE_NORMAL_TERMINATION, EXCLUDE_BOTS, REQUIRE_LICHESS_EVALS = True, True, True
MIN_PLIES = 60

# ---- Position / move criteria ----
FULLMOVE_MIN, FULLMOVE_MAX = 10, 30
MAX_ABS_EVAL_CP = 500        # position not decided
GOOD_MAX_CP_LOSS = 30        # good move: loses at most this much vs the best move
BAD_MIN_CP_LOSS = 100        # bad move (secondary set): to be fixed after counting; also count a win-probability version
BAD_MIN_WINPROB_DROP = 0.10  # alternative definition, in win probability (0-1)
POSITIONS_PER_GAME = {"good": 1, "bad": 1}

# ---- Pilot ----
PILOT_N = 10                  # whole games in the optional Step 1 pilot split
PILOT_POSITIONS_PER_KIND = 5  # fixed LLM pilot sample: 5 Stockfish-checked good + 5 bad positions
PILOT_EXCLUDED_FROM_FINAL = True

# ---- Stockfish ----
STOCKFISH_VERSION = "19"      # as stated in the proposal emails; the binary's SHA-256 is recorded in the step-2 manifest
STOCKFISH_DEPTH, STOCKFISH_THREADS, STOCKFISH_HASH_MB, STOCKFISH_MULTIPV = 18, 1, 128, 1
PV_MAX_PLIES = 8
STOCKFISH_PATH = "stockfish/stockfish-windows-x86-64-universal.exe"   # relative to the project folder (Windows); falls back to `stockfish` on PATH. Binary is not committed

# ---- Generating LLM (fix after the pilot) ----
LLM_BACKEND, LLM_MODEL = "ollama", None      # main-run model remains undecided until the pilot
LLM_TEMPERATURE, LLM_NUM_CTX, LLM_MAX_TOKENS = 0.0, 2048, 400
LLM_DEVICE = "cpu"           # final runs: CPU only (CUDA_VISIBLE_DEVICES=-1) for stable output
LLM_REPEATS = 3              # repeated generations for later main runs; the pilot compares three conditions once each
LLM_PILOT_MODELS = ["qwen2.5:7b", "deepseek-r1:8b", "phi4-mini:latest", "llama3.1:8b"]
LLM_PILOT_CONDITIONS = ["A", "B", "C"]
LLM_PILOT_REPEATS_PER_CONDITION = 1
LLM_PILOT_PV_COLUMN = "engine_best_pv_san"  # principal variation from the original position, before the candidate move
LLM_PILOT_MODEL_ID_PREFIXES = {
    "qwen2.5:7b": "845dbda0ea48",
    "deepseek-r1:8b": "6995872bfe4c",
    "phi4-mini:latest": "78fad5d182a7",
    "llama3.1:8b": "46e0c10c039e",
}
LLM_PILOT_OLLAMA_URL = "http://localhost:11434"
LLM_PILOT_REASONING_MODELS = ["deepseek-r1:8b"]
LLM_PILOT_REASONING_NUM_CTX, LLM_PILOT_REASONING_MAX_TOKENS = 4096, 3000

# ---- Annotation and analysis plan (freeze BEFORE the main generation; tag the git commit) ----
ANNOTATION_FRACTION = 0.20   # stratified by condition and claim type, labelled blind to condition
ANNOTATION_KAPPA = "cohen"
PRIMARY_METRIC = "claim-level error rate = incorrect / (correct + incorrect)"
COMPARISONS = [("A", "B"), ("A", "C"), ("B", "C"), ("A", "D")]
MULTIPLE_TESTING, ALPHA = "holm", 0.05
ANALYSIS_PLAN_FROZEN = None  # set to the git tag / date when frozen, e.g. "analysis-plan-v1"


# ---- Step 1: selection (added) ----
# ELO_MIN above is only used by the step-0 what-if funnel. Step 1 uses the percentile below instead.
ELO_PERCENTILE = 99.0        # BOTH players must be >= this percentile of the month's player ratings (from step 0's elo_exact.csv, weighted by games played)
GAMES_TARGET = None          # None = every game that passes all criteria; an integer = random sample of that many games (SEED_SELECTION)
BAD_DEFINITION = "cp"        # which definition picks the secondary "bad" position: "cp" (loss >= BAD_MIN_CP_LOSS) or "winprob" (drop >= BAD_MIN_WINPROB_DROP)
BAD_ONLY_IN_GAMES_WITH_GOOD = True   # bad positions are only taken from games that also have a good position (one good + one bad per game)


def config_snapshot():
    return {k: str(v) for k, v in globals().items() if k.isupper()}
