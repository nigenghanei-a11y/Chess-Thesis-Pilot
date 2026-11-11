# Chess Explanations Pilot (10 positions)

Pilot for the M.Sc. thesis *Exploring the Factual Reliability of LLM-Generated Chess Explanations*.
Conditions: A (no feedback), B (Stockfish eval), C (eval + PV), D (verified repair of A).

## Reproduce
```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python 01_sample_candidates.py
```
All settings live in `config.py` (seed, inclusion criteria, Stockfish, LLM).
Every step writes `outputs/NN_manifest.json` with versions, config and input hashes.

## Pipeline
1. `01_sample_candidates.py` - seeded sampling from one pinned Lichess monthly file
2. Stockfish analysis + filtering (todo)
3. Generate A/B/C (todo)
4. Claim extraction (todo)
5. Verification (todo)
6. Manual review sheet (todo)
7. Condition D repair + re-verification (todo)
8. Pilot report (todo)

## Status
Pilot only: tests the workflow, not the research questions.
