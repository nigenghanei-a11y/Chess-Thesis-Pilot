# Chess explanations thesis

Factual reliability of LLM-generated chess explanations (conditions A, B, C and the repair condition D).

## Settings
All scientific decisions (month, selection criteria, seeds, Stockfish and LLM settings, annotation and analysis plan) live in `config.py`.
Every step imports it and stores a snapshot of it, plus the git commit, in its manifest.
Step-local knobs (test limits, checkpoint intervals) stay in the notebook or script. Restart the kernel after editing `config.py`.
Step 0 reads the funnel thresholds (`MAX_RATING_GAP`, `MIN_EST_SECONDS`) from `config.py` too. Its checkpoint fingerprint includes these settings, `CODE_VERSION`, and the current Git commit.
The notebook records `git rev-parse HEAD`; it does not detect uncommitted changes or hash the notebook source. For exact source provenance, commit the notebook, config, and scripts before running Step 0.
Changing the commit changes the fingerprint and can require a new profiling pass.

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
3. Optional quick test: set `MAX_GAMES = 1_000_000` in the first cell. A partial test writes to the same `outputs/step0_dataset/<month>/` folder as the full run; it does not use a separate test folder. Set it back to `None` and run the notebook again before treating the outputs as final.
   The download cell runs first, so even the test needs the full raw file (about 30 GB) unless you point `RAW_OVERRIDE` at a small sample `.pgn` / `.pgn.zst`.

What it does: downloads or reuses the pinned monthly file (resumable), records its size and SHA-256, profiles the games in one streaming pass, and writes descriptive tables and plots. The overview records basic counts such as games without moves, missing/invalid Elo, missing clock time controls, and unfinished `*` results. The current notebook does not perform duplicate-ID checks, sampled legal-move/eval validation, or produce a data-quality checklist.

The what-if funnel (plot 8 and `funnel_by_rating_tier.csv`) summarizes rating tiers after applying the rating-gap, estimated-time, normal-termination, no-bot, and eval-tag criteria. It does not apply `MIN_PLIES` or validate result tags against move text; those are not Step 0 checks.

Folders:
- `data/raw/<file>.pgn.zst` - the raw file (not committed; byte size and SHA-256 are in `download_manifest.json`).
- `outputs/step0_dataset/<month>/` - results: `download_manifest.json`, `profile_manifest.json`, `tables/*.csv`, `tables/overview.json`, and `figures/*.png`.
- Working files (not committed): `checkpoint.pkl` and `profile_state.pkl`.

Time and space: about 30 GB to download and roughly 30-90 minutes for a full profiling pass, depending on the computer. Keep the computer awake; the pass can be stopped and resumed.

Reproducibility: `profile_manifest.json` records the raw-file SHA-256, config snapshot, parameters, package versions, Git commit, and `results_sha256`.
After all plots and tables have been written, the final cell hashes a fixed list of 18 CSV files plus `overview.json`, records that list as `hashed_tables`, and updates `results_sha256` in `profile_manifest.json`. Figures and manifests are excluded. CSV serialization uses pandas defaults, so byte-for-byte equality across different platforms is not guaranteed.
The preceding cell prints the recorded file hash, result hash, game count, and figure names. The final cell prints the hash over the complete fixed table set; neither cell compares against an independent earlier run.

Step 0 is done when the full run has `partial: false`, the raw-file checksum matches the official checksum when available, the generated tables and figures are present, and the intended outputs are committed (without the working files).

## Planned steps
1. Selection: game filters, good moves (<= 30 cp loss), count of bad moves (>= 100 cp, and a win-probability version); pilot positions kept out of the final set
2. Stockfish re-check (version in `config.py`, depth 18, one thread, 128 MB hash, MultiPV 1)
3. Explanation generation A/B/C with the chosen model (CPU-only for the final runs, repeated runs)
4. Claim extraction, verification, blind second annotation (about 20% of claims, Cohen's kappa), repair condition D
5. Analysis (plan frozen and git-tagged before the main generation)
