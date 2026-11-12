# Chess Explanations Thesis: dataset and pilot

M.Sc. thesis *Exploring the Factual Reliability of LLM-Generated Chess Explanations*.
Conditions: A (no engine feedback), B (Stockfish eval), C (eval + PV), D (verified repair of A).

## Dataset: 113 position-move pairs from Lichess, October 2026
Every game of the month with both players rated >= 2600, rating gap <= 100, rapid or slower, normal
termination, no bot accounts, >= 60 plies and Lichess evaluations. The candidate move is the move the human
played, kept only if it lost <= 30 cp (Stockfish re-check), in a position with |eval| <= 500 cp,
at full move 10-30, not in check, with >= 2 legal moves. One position per game.

## Reproduce (project folder, venv active)
```
pip install -r requirements.txt
# download lichess_db_standard_rated_2026-10.pgn.zst into data\raw and check its SHA-256 starts with 043906eb
python profile_raw_dataset.py --input data\raw\lichess_db_standard_rated_2026-10.pgn.zst
python count_eligible_elite.py --input data\raw\lichess_db_standard_rated_2026-10.pgn.zst --n 150 --max-per-player 0 --run-name 2026-10_E5_all
python run_elite_step.py 02 --run 2026-10_E5_all      # needs N_PILOT >= 121 in config.py (150 is set)
python profile_final_dataset.py --run 2026-10_E5_all
```
Outputs: `outputs/elite_count/2026-10_E5_all/` (`02_positions.jsonl` = the 113 rows) and `outputs/dataset_profile/`.

## Pinned environment
- Python 3.11.9, packages in `requirements.txt`.
- Stockfish 19 (name reported by the engine), depth 18, 1 thread, 128 MB hash, MultiPV 1.
  The engine file's SHA-256 is stored as `engine_sha256` in `02_manifest.json`; keep that binary.
- Settings live in `config.py`; every step writes a manifest with versions, config and input hashes.

## Pipeline
1. Profile of the raw file (`profile_raw_dataset.py`)
2. Count, funnel and candidate export (`count_eligible_elite.py`)
3. Stockfish check and final filter (`run_elite_step.py 02`)
4. Generate A/B/C with Ollama (`run_elite_step.py 03`)
5. Claim extraction, verification, manual review, condition D (todo)

## Known limitations
Evaluations exist only for Lichess-analysed games (9.86%); the Lichess evaluation is a pre-filter only;
one month and strong players only; bots are excluded only where Lichess marks the account.
