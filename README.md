# Chess explanations thesis

Factual reliability of LLM-generated chess explanations (conditions A, B, C and the repair condition D).

## Settings
All scientific decisions (month, selection criteria, seeds, Stockfish and LLM settings, annotation and analysis plan) live in `config.py`.
Every step imports it and stores a snapshot of it, plus the git commit, in its manifest.
Step-local knobs (test limits, checkpoint intervals) stay in the notebook or script. Restart the kernel after editing `config.py`.
Step 0 reads the funnel thresholds (`MAX_RATING_GAP`, `MIN_EST_SECONDS`) from `config.py` too; they are part of the code fingerprint, so changing them invalidates old checkpoints.

## Environment (Windows, PowerShell)
Python 3.11.9 (also stored in every manifest). From the project folder:

```powershell
python --version                                  # must print 3.11.9
python -m venv .venv
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
.\.venv\Scripts\Activate.ps1
python -c "import sys; print(sys.executable)"     # must end with \.venv\Scripts\python.exe
python -m pip install -r requirements-lock.txt    # exact copy of the environment used
python check_setup.py                             # pre-flight check, must end with ALL CHECKS PASSED
```

- Always write `python -m pip ...`, never a bare `pip`: a bare `pip` can belong to a different Python than the one you are running.
- `requirements.txt` = direct dependencies, exact versions. `requirements-lock.txt` = the full `pip freeze` of the environment used.
  To renew the lock after changing a package: `python -m pip freeze | Set-Content -Encoding ascii requirements-lock.txt`
  (a plain `>` in Windows PowerShell writes UTF-16, which pip cannot read).
- **VS Code:** open this folder, press Ctrl+Shift+P, choose *Python: Select Interpreter*, pick `.venv`; then in the notebook click *Select Kernel*, *Python Environments*, `.venv`.
  Activating the venv in the terminal does **not** change the notebook's kernel. Check in a cell: `import sys, numpy; print(sys.executable, numpy.__version__)`.
- To delete and rebuild `.venv`, first close VS Code (a running kernel locks files inside it).
- `python check_setup.py` verifies, in one go: Python version, that you are inside this project's `.venv`, that the installed packages equal both requirement files,
  that every package imports and works (zstandard, python-chess, pandas CSV writer, `pip check`), that `config.py`, the notebook and `.gitignore` agree,
  and that there is enough disk space. `python check_setup.py --online` also asks the Lichess server for the size of the pinned file.
- Not committed: `data/raw/`, `.venv/`, the Stockfish binary (`stockfish/`; its SHA-256 is recorded in the manifest).

## Step 0 - download, check and profile the raw Lichess file
`step0_download_and_profile.ipynb`

1. Run `python check_setup.py`, then open the notebook from this folder, choose the `.venv` kernel, run all cells.
2. The month is pinned in `config.py` (`MONTH`). Everything else is derived from it.
3. Quick test first: set `MAX_GAMES = 1_000_000` in the first cell (it writes to its own folder `outputs/step0_dataset/<month>_test1000000/`), then set it back to `None`.
   The download cell runs first, so even the test needs the full raw file (about 30 GB) unless you point `RAW_OVERRIDE` at a small sample `.pgn` / `.pgn.zst`.

What it does: downloads the pinned monthly file (resumable), records its size and SHA-256, profiles every game in one streaming pass (no sampling), and checks the data quality:

| Check | Coverage |
|---|---|
| Duplicate game IDs | all games |
| Missing/invalid Elo, date outside the month, result tag vs move-text result, games without moves | all games |
| Analysed games with missing evals | all games |
| Legal moves, result and game length (python-chess replay) | every `LEGALITY_EVERY`-th game (fixed rule, about 4,800 games) |
| Eval values parseable and plausible, last eval agrees with the result | every `EVAL_EVERY`-th analysed game |

The what-if funnel (plot 8 and `funnel_by_rating_tier.csv`) applies, in this order: rating tier, rating gap, rapid or slower, normal termination, valid result, no bots, has evals.
It does not apply `MIN_PLIES`; that filter belongs to step 1, so the last funnel number here is larger than the final dataset size.

Folders:
- `data/raw/<file>.pgn.zst` - the raw file (not committed; byte size and SHA-256 are in `download_manifest.json`).
- `outputs/step0_dataset/<month>/` - committed results: `download_manifest.json`, `profile_manifest.json`, `tables/*.csv` (including `data_quality_checklist.csv`), `figures/*.png`.
- Working files (not committed): `checkpoint.pkl`, `profile_state.pkl`, `game_ids.u64` (about 760 MB for a full month).

Time and space: about 30 GB download, about 1 to 1.5 hours for the profiling pass, about 1 GB of free RAM for the duplicate check. Keep the computer awake; the pass can be stopped and resumed.

Reproducibility: same raw file + same notebook -> identical `results_sha256` in `profile_manifest.json`
(only the tables listed in `hashed_tables` enter the hash; fixed line endings and number formats; the profiling code and the two funnel thresholds are fingerprinted).
The last cell compares key numbers with the independent earlier run of the same month (only for the whole 2026-10 file with gap 100 and 480 s).

Step 0 is done when: the data-quality checklist has no `REVIEW` row, the SHA-256 line says `MATCH` (or "not available"), the cross-check table says `MATCH` on all eight rows,
and `outputs/step0_dataset/<month>/` is committed (without the working files).

## Planned steps
1. Selection: game filters, good moves (<= 30 cp loss), count of bad moves (>= 100 cp, and a win-probability version); pilot positions kept out of the final set
2. Stockfish re-check (version in `config.py`, depth 18, one thread, 128 MB hash, MultiPV 1)
3. Explanation generation A/B/C with the chosen model (CPU-only for the final runs, repeated runs)
4. Claim extraction, verification, blind second annotation (about 20% of claims, Cohen's kappa), repair condition D
5. Analysis (plan frozen and git-tagged before the main generation)
