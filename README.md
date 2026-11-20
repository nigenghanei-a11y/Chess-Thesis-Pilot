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

## Step 1 - game filters and full position selection
Save `step1_select_positions.ipynb` (Ctrl+S), then use **Run All** from the project folder with the `.venv` kernel to run Step 1. The notebook also contains the separate, manual Step 2 Stockfish re-check. Keep `RUN_STOCKFISH = False` while using **Run All**; after Step 1 is complete, enable it and execute only the final cell. Save before execution so the source hash in the manifest matches the saved code. Needs the finished step 0 outputs.

- Rating criterion: both players at or above `ELO_PERCENTILE` of the month's player-game rating records, weighted by games played and read from step 0's `elo_exact.csv`. For the current 2026-10 Step 0 table, P99 resolves to **2556**; the resolved threshold is printed and stored in the manifest. `ELO_MIN` only affects step 0's what-if funnel. This is not a percentile of unique player accounts.
- Pass 1 verifies the raw file's SHA-256 against step 0's manifest, then streams it, applies the game-level criteria with "normal termination" and "no bots" as separate steps, checks duplicate game IDs (all games by default; survivors only if `DUP_CHECK_ALL = False`), and caches survivors in `candidate_games.pgn`. Later runs reuse the cache only when its input/settings/source fingerprint matches. The checksum verification adds a full read of the compressed raw file.
- Pass 2 replays those games, selects good moves (<= `GOOD_MAX_CP_LOSS`, pre-move position within +-`MAX_ABS_EVAL_CP`, no mate score, not in check, >= 2 legal moves, and position unused) from the Lichess evaluations, and picks one per game with a per-game seeded shuffle (`SEED_SELECTION`). It counts and selects bad moves under both definitions (`bad_move_counts.csv`), optionally samples `GAMES_TARGET` games, and by default keeps the full selected data without reserving pilot games. Stockfish re-check is a separate, optional Step 2.
- Outputs in `outputs/step1_dataset/<month>/`: `pass1_manifest.json`, `funnel_game_level.csv`, `bad_move_counts.csv`, `positions_all.csv`, `positions_pilot.csv`, `positions_final.csv`, `game_summary.csv`, `kpis_final_positions.csv`, `side_to_move.csv`, and `selection_manifest.json` (raw SHA-256, thresholds, notebook code hash, run parameters, config snapshot, Git commit, uncommitted-changes flag, `results_sha256`). Initially `positions_final.csv` contains the full selected set and `positions_pilot.csv` is empty. The optional `MAKE_PILOT_SPLIT = True` path samples `PILOT_N` whole games and removes them from `positions_final.csv`; it is separate from the shared, balanced LLM pilot sample created by `step1_pilot.ipynb` after Stockfish. `candidate_games.pgn` is a regenerable working file and is ignored by Git. Test runs (`MAX_GAMES` or `RAW_OVERRIDE`) use the `_TEST` sibling folder.
- The default all-game duplicate-ID check stores 8 bytes per game and sorts the IDs; allow roughly 1.5 GB of peak RAM for the ID array and sort copy.

## Step 2 - Stockfish re-check
Step 2 is the Stockfish run in the final section of `step1_select_positions.ipynb`; it reads `outputs/step1_dataset/<month>/positions_final.csv` and accepts the full selected pool before pilot sampling. Keep `RUN_STOCKFISH = False` for **Run All**. When ready, set it to `True`, save the notebook, and execute only the final cell—do not use **Run All** with the flag enabled. The source fingerprint is read from the saved notebook. For a separate session, run the Step 2 setup and function cells first, then the final cell. This produces the complete Stockfish-checked pool.

On Windows, the final cell selects the Proactor event-loop policy so Jupyter can launch Stockfish as a subprocess.

- Uses the Stockfish binary configured by `STOCKFISH_PATH` (or `stockfish` on `PATH`) with the configured version, depth, threads, hash, and MultiPV.
- Rechecks the position before and after the played move, confirms the good/bad label using the configured thresholds, and records the best move, evaluations, principal variations, and rejection reasons.
- Outputs go to `outputs/stockfish_recheck/<month>/`: `analysed_all.jsonl`, `positions_stockfish.csv`, `positions_rejected.csv`, `kpis.json`, and `stockfish_manifest.json`. The analysis is resumable; `resume_state.json` is a working file and is ignored by Git.
- The manifest records hashes for the input, engine, Step 2 notebook code, and outputs, plus config and Git state. Commit notebook/config changes before running for a clean Git provenance record.
- **Status: Step 2 is finished for 2026-10 (506 analysed, 446 kept: 248 good, 198 bad; 60 rejected). Do not edit or re-run the Step 2 cells.** `RUN_STOCKFISH = True` sits inside the tagged Step 2 cell and is part of its source hash, so changing it makes the pilot notebook's validation fail and would require re-running Stockfish on all 506 positions.
- The 60 rejected positions have two possible reasons (53 `label_not_confirmed_by_stockfish`, 12 `already_decided`). The counts sum to 65 because five positions failed both checks.

The notebooks under `chess-explanations-thesis_OLD/` document the earlier three-notebook workflow. The current Step 1 combines the old game-filter and move-selection stages; the old Stockfish notebook is not used by this pipeline.

## Step 1 pilot - prepare a shared LLM pilot set
After Step 2 completes, run `step1_pilot.ipynb`. It samples `PILOT_POSITIONS_PER_KIND` good and the same number of bad positions from `outputs/stockfish_recheck/<month>/positions_stockfish.csv`; the default is 5 of each. This is a small, fast sampling step and does not rerun raw-data selection or Stockfish analysis.

The notebook writes `outputs/step1_pilot/<month>/pilot_for_llm.csv` with only the position, side to move, and played move, plus `pilot_key.csv` with the hidden labels and Stockfish reference data. Give every LLM the same `pilot_for_llm.csv`; keep the key out of model prompts. Treat this input as fixed across models and save each model's generations separately (`step3_llm_pilot.py` writes them under `outputs/step3_llm_pilot/<month>/<model>/`) so comparisons use identical positions. The notebook verifies the source manifests and hashes and refuses to replace an existing different pilot sample.

## Main dataset - pilot games removed
The last cell of `step1_pilot.ipynb` writes `outputs/step1_pilot/<month>/positions_main.csv`: every Stockfish-checked position except those from games that appear in `pilot_key.csv`. The exclusion is by game, so a pilot game contributes nothing to the main set (not even its other good/bad position). `main_manifest.json` stores the pilot game IDs, the counts and the SHA-256 of `positions_main.csv` and `pilot_manifest.json`.

Current counts: 429 positions (239 good, 190 bad) from 258 games; 171 games have both a good and a bad position. **Use `positions_main.csv`, not `positions_final.csv` or `positions_stockfish.csv`, for every later step.** In this file `stockfish_winprob_drop` is recomputed exactly from the stored evaluations (see the known issue below).

`python check_main_dataset.py` validates it: unique IDs, no pilot games or FENs, labels and cp thresholds, cp loss recomputed from the evals, evaluation and move-number windows, FEN/SAN/UCI parsing, and that no "bad" move is Stockfish's own best move. It writes `main_checks.json` and changes no data. It passed all checks on the current data (0 bad positions with an engine-best move). Re-run it after any change to `positions_main.csv`.

## Known issues and limitations
- **Rounded win-probability drop.** Step 2 stored `round(wp_drop, 1)`, so `winprob_drop` / `stockfish_winprob_drop` in `positions_stockfish.csv`, `positions_rejected.csv`, `analysed_all.jsonl` and `pilot_key.csv` have one decimal only. Good/bad labels use centipawns (`BAD_DEFINITION = "cp"`) and are not affected. `positions_main.csv` has the exact value recomputed from `eval_before_cp` and `eval_after_cp`; never use the rounded columns for analysis. Step 2 was not changed, because editing its cell would invalidate the hash chain and require a full Stockfish re-run. If you rebuild the pipeline from scratch before submission, remove the rounding in `analyse_position`.
- **Search noise.** Root and child searches at depth 18 sometimes disagree (some rejected positions have an engine-best human move with a large cp loss). Rejected rows are excluded; the check above confirms no kept bad position is engine-best.
- **Players repeat.** 258 games involve 173 players; the most frequent account appears in 33 games and the top five accounts are involved in 38% of the games. The analysis needs a player-level sensitivity check (for example capping games per player).
- **Time control.** 89% of the main games are 10+0, so conclusions concern 10-minute games.
- **Selection effects.** Only games with Lichess computer evaluations are used, and the P99 threshold is computed over game-player rating records, not unique accounts.
- Manifests of Steps 1-2 and the pilot record `git_uncommitted_changes: true` because they were produced before the final commit; the file hashes and the git tags (`pilot-v1`, `main-checked-v1`) document the final state.

## Step 3 pilot - Condition A with four local models
`step3_llm_pilot.py` generates Condition A explanations (no engine information) for the 10 pilot positions with local Ollama models (default tags: `qwen2.5:7b`, `llama3.1:8b`, `deepseek-r1:8b`, `phi4-mini`; check with `ollama list`). Ollama is installed separately and is not in `requirements.txt`. Run inside the `.venv`:

```powershell
python step3_llm_pilot.py --models llama3.1:8b --limit 2 --repeats 1   # smoke test
python step3_llm_pilot.py --repeats 2                                    # all four models, 10 positions
python step3_llm_pilot.py --mock --repeats 2                             # test the script without Ollama
```

- Settings per request: temperature `LLM_TEMPERATURE`, seed `SEED_LLM`, CPU only (`num_gpu: 0`), fixed thread count, `LLM_NUM_CTX` and `LLM_MAX_TOKENS`; `deepseek-r1` gets a larger budget (3000 tokens, context 4096) and its `<think>` text is stored separately. Repeats use identical settings to measure run-to-run variation.
- The prompt is neutral (it does not say the move is good), is identical for all models, and its hash is stored. `--with-board` adds an ASCII board; decide once and keep it fixed.
- Outputs in `outputs/step3_llm_pilot/<month>/`: per model `generations.jsonl` (text, thinking, raw output, `done_reason`, truncation flag, tokens, seconds) and `run_manifest.json` (model digest, Ollama version, settings, prompt hash, input hash, git commit, script hash, hardware); across models `summary.csv` (speed, truncation), `variation.csv` (distinct texts per position across repeats) and `review_sheet.csv` (for counting claims and errors by hand). `--mock` writes to a separate `_MOCK` folder.
- Resuming skips finished (position, repeat) pairs; if the settings or prompt changed, the script refuses and asks you to move the old folder aside.

## Planned steps
3. Test candidate LLMs on the fixed pilot set with `step3_llm_pilot.py` (Condition A first; conditions B-D follow once the model and prompt are fixed). Choose the model and settings before main generation.
4. Claim extraction, verification, blind second annotation (about 20% of claims, Cohen's kappa), repair condition D
5. Analysis (plan frozen and git-tagged before the main generation)
