# Chess explanations thesis - data pipeline

Run in order (config in `config.py`):
1. `step1_filter_games.ipynb`  - raw Lichess file -> filtered games + funnel table
2. `step2_select_good_bad_moves.ipynb` - one good + one bad human move per game (Lichess evals)
3. `step3_stockfish_check.ipynb` - Stockfish re-check -> final positions + KPIs

Every step writes `manifest.json` (versions, config, SHA-256 of input/output).
Later steps (LLM generation A/B/C, claim extraction, verification, Condition D, statistics) will be steps 4+.
