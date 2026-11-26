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
- **Status: Step 2 code is `step2-v2`** (the win-probability drop is now stored with 4 decimals; v1 rounded it to 1 decimal). Step 1 is unchanged and its outputs stay valid, but Step 2 and everything after it must be re-run once: delete `outputs/stockfish_recheck`, `outputs/step1_pilot`, `outputs/step3_llm_pilot` and `outputs/step4_claims` (keep `data/raw`, `outputs/step0_dataset`, `outputs/step1_dataset`), commit, then run Step 2 (final cell only), `step1_pilot.ipynb`, `check_main_dataset.py`, Step 3 and Step 4 in that order. The v1 run gave 506 analysed / 446 kept (248 good, 198 bad); the counts must be identical after the re-run because only the stored rounding changed. `RUN_STOCKFISH = True` sits inside the tagged Step 2 cell and is part of its source hash, so do not toggle it after the run.
- The 60 rejected positions have two possible reasons (53 `label_not_confirmed_by_stockfish`, 12 `already_decided`). The counts sum to 65 because five positions failed both checks.

The notebooks under `chess-explanations-thesis_OLD/` document the earlier three-notebook workflow. The current Step 1 combines the old game-filter and move-selection stages; the old Stockfish notebook is not used by this pipeline.

## Step 1 pilot - prepare a shared LLM pilot set
After Step 2 completes, run `step1_pilot.ipynb`. It samples `PILOT_POSITIONS_PER_KIND` good and the same number of bad positions from `outputs/stockfish_recheck/<month>/positions_stockfish.csv`; the default is 5 of each. This is a small, fast sampling step and does not rerun raw-data selection or Stockfish analysis.

The notebook writes `outputs/step1_pilot/<month>/pilot_for_llm.csv` with only the position, side to move, and played move, plus `pilot_key.csv` with the hidden labels and Stockfish reference data. Give every LLM the same `pilot_for_llm.csv`; keep the key out of model prompts. Treat this input as fixed across models and save each model's generations separately under `outputs/step3_llm_pilot/<month>/<model>/` so comparisons use identical positions. The notebook verifies the source manifests and hashes and refuses to replace an existing different pilot sample.

## Main dataset - pilot games removed
The last cell of `step1_pilot.ipynb` writes `outputs/step1_pilot/<month>/positions_main.csv`: every Stockfish-checked position except those from games that appear in `pilot_key.csv`. The exclusion is by game, so a pilot game contributes nothing to the main set (not even its other good/bad position). `main_manifest.json` stores the pilot game IDs, the counts and the SHA-256 of `positions_main.csv` and `pilot_manifest.json`.

Current counts: 429 positions (239 good, 190 bad) from 258 games; 171 games have both a good and a bad position. **Use `positions_main.csv`, not `positions_final.csv` or `positions_stockfish.csv`, for every later step.** In this file `stockfish_winprob_drop` is recomputed exactly from the stored evaluations (see the known issue below).

`python check_main_dataset.py` validates it: unique IDs, no pilot games or FENs, labels and cp thresholds, cp loss recomputed from the evals, evaluation and move-number windows, FEN/SAN/UCI parsing, and that no "bad" move is Stockfish's own best move. It writes `main_checks.json` and changes no data. It passed all checks on the current data (0 bad positions with an engine-best move). Re-run it after any change to `positions_main.csv`.

## Known issues and limitations
- **Rounded win-probability drop (fixed in `step2-v2`).** Step 2 v1 stored `round(wp_drop, 1)`; v2 stores 4 decimals. Good/bad labels use centipawns (`BAD_DEFINITION = "cp"`) and were never affected. `positions_main.csv` additionally recomputes the exact value from `eval_before_cp` and `eval_after_cp`. Outputs produced before the fix must not be used.
- **Search noise.** Root and child searches at depth 18 sometimes disagree (some rejected positions have an engine-best human move with a large cp loss). Rejected rows are excluded; the check above confirms no kept bad position is engine-best.
- **Players repeat.** 258 games involve 173 players; the most frequent account appears in 33 games and the top five accounts are involved in 38% of the games. The analysis needs a player-level sensitivity check (for example capping games per player).
- **Time control.** 89% of the main games are 10+0, so conclusions concern 10-minute games.
- **Selection effects.** Only games with Lichess computer evaluations are used, and the P99 threshold is computed over game-player rating records, not unique accounts.
- Manifests of Steps 1-2 and the pilot record `git_uncommitted_changes: true` because they were produced before the final commit; the file hashes and the git tags (`pilot-v1`, `main-checked-v1`) document the final state.

## Step 3 pilot - A/B/C with four local models
Use `step3_llm_pilot.ipynb` from the project folder with the `.venv` kernel. It generates separate A, B, and C explanations for each of the 10 fixed pilot positions. Ollama runs locally at `LLM_PILOT_OLLAMA_URL`; the position data and prompts are not sent to a hosted model service. Ollama is installed separately and is not in `requirements.txt`.

The current local inventory supplied by `ollama list` is:

| Ollama tag | Local ID prefix | Listed size |
|---|---:|---:|
| `qwen2.5:7b` | `845dbda0ea48` | 4.7 GB |
| `deepseek-r1:8b` | `6995872bfe4c` | 5.2 GB |
| `phi4-mini:latest` | `78fad5d182a7` | 2.5 GB |
| `llama3.1:8b` | `46e0c10c039e` | 4.9 GB |

These are pinned in `config.py`. Before generation the notebook checks each installed model's digest prefix against that inventory. Each model's full digest and the Ollama server version are then recorded in its manifest; the notebook obtains the server version from Ollama's local API at run time. If a model has been pulled or updated to a different digest, generation stops until the pin is deliberately reviewed and updated.

The default run is 4 models x 10 positions x 3 conditions x 2 calls per condition = 240 generations (60 per model). The pilot conditions and repeat count are fixed in `config.py`: A receives only the position and played move; B receives those plus pre- and post-move Stockfish evaluations; C receives the same evaluations as B plus `engine_best_pv_san`, the best PV from the original position. Both scores are given in centipawns from the point of view of the side that moves (positive = good for the mover). A, B, and C are separate requests with no shared chat history. The LLM receives no good/bad label, CP-loss target, or separate engine-best-move field; the PV in C is the only condition-specific source that contains an engine line. This pilot does not include condition D.

The prompt asks why a human might choose the move in at most 3 short sentences and ends with a machine-readable line `Verdict: good | inaccurate | bad`; it is identical in all conditions except for the engine information. Settings are temperature `LLM_TEMPERATURE` (0.0), seed `SEED_LLM` (46), CPU only (`num_gpu: 0`), `LLM_NUM_CTX` (2048), and `LLM_V2_MAX_TOKENS` (160). `deepseek-r1:8b` is configured with context 4096 and a `LLM_V2_REASONING_MAX_TOKENS` (1500) output budget; truncated answers are excluded and reported in Step 4, never repaired. Its `<think>` text is separated from the final answer in the results. The main-run `LLM_REPEATS` value (3) does not apply to this pilot. The thread count is based on the local CPU count and recorded per run. The optional ASCII board is off by default. The system prompt, user template, exact condition prompts, prompt hash, options, source/input hashes, Python/platform, Git state, and code hashes are also recorded.

The notebook contains the generation implementation; there is no separate Step 3 Python script. Start Ollama first (`ollama serve`) and make sure the listed tags are installed. Save the notebook, run its setup and preflight cells, then set `RUN_PILOT = True` and execute only the generation cell. Leave `RUN_PILOT = False` during **Run All** to avoid starting all 240 generations accidentally. `USE_MOCK = True` provides a quick offline smoke test.

Before generation, the notebook verifies the pilot sample, private key, Stockfish result CSV, and both manifests and their hashes. The key and its labels remain local and are used only to join and validate Stockfish evidence; they are not included in any prompt. Outputs in `outputs/step3_llm_pilot/<month>/` are separated by model. Each model folder has `generations.jsonl` (condition, repeat, prompt, final text, raw response, thinking, `done_reason`, truncation, token counts, and timing) and `run_manifest.json`. Cross-model outputs include `summary.csv` (per model and condition), `condition_comparison.csv` (A/B/C responses side-by-side per position, repeat, and model), and `review_sheet.csv`. Completed (position, condition, repeat) calls resume only when model, digest, input/evidence hashes, settings, and notebook source match. The manifest stores the notebook code hash.

After the generation and summary cells, the optional final reproducibility-check cell is off by default. Set `RUN_REPRO_CHECK = True` and execute only that cell to re-run one or two positions for every model and condition, then compare the responses with the saved generations. It writes no result files; the comparison is displayed in the notebook. Clear the cell output before saving if you do not want that diagnostic table retained in the notebook.

`check_setup.py` remains the project-wide environment/dependency preflight. `check_main_dataset.py` is not needed to run this pilot; retain and run it after modifying `positions_main.csv` and before main generation, where it validates the main dataset rather than the 10-row pilot input.

## Step 4 - claim splitting, automatic verification, taxonomy
`step4_claims.ipynb` contains the claim splitting, taxonomy, and verification helpers in tagged notebook cells; no Step 4 helper `.py` files are needed. Output: `outputs/step4_claims/<month>/`.

On Windows, the notebook selects the Proactor event-loop policy before launching live Stockfish from Jupyter.

1. The notebook first validates the Step 3 hash chain, CPU-only settings, model digests and prompts, and excludes truncated or empty generations (`excluded_generations.csv`).
2. The `Verdict:` line is separated and becomes its own claim, checked against the Stockfish label.
3. A fixed splitter LLM (`SPLITTER_MODEL`, CPU, temperature 0, seed 46) splits each explanation into atomic claims. It sees only the explanation text, never the position, label, model or condition. Every quote must be a verbatim, in-order span of the explanation and the quotes must cover at least 80% of it; a supplied subject must occur in the same source sentence as its quote. `claim_text` is the verbatim quote; the subject is kept separately and never prepended to create a new phrase. Ambiguous or invalid splits fall back to the rule-based splitter and are counted (`split_source`). The blind sheet retains the associated sentence, quote and subject separately. Results are cached in `splitter_cache.jsonl`; cached raw splitter responses are revalidated on reruns, so this fix does not require another splitter-model call. The split cell prints per-explanation progress, including cache hits and splitter request timing.
4. The embedded verifier checks claims with python-chess (captures, attacks, defences, forks, pins, hanging pieces, castling, mate, piece positions, legality of named moves and sequences) and with Stockfish (stored depth-18 evaluations, best move and lines from Step 2; optional live Stockfish via `RUN_LIVE_STOCKFISH = True` for alternative moves named in an explanation, cached in `live_stockfish_cache.jsonl`). Capture checks recognize both “captures the pawn on e4” and “captures the e4 pawn,” then compare the claimed piece with the FEN. Movement statements giving both source and destination squares are checked against the played move, so an incorrect origin square is flagged too. Statements that say the player chose or played a SAN move are compared directly with the supplied move (for example, “chooses Nd7”); named pawn-advance statements such as “the move g5 advances the pawn” are checked against the actual move. Splitter subjects are checked separately against the board, including when they are not repeated in the exact quote. The `claims_extend` helper adds conservative checks for newly connected rooks, newly opened lines for a stationary friendly slider, explicit centralizing moves and newly attacked center squares, plus checkable attack/defence cores in selected hedged claims. It also rejects a pinned-piece claim when its explicitly named target square contains the wrong piece. These checks only auto-verify positive board evidence; they do not infer that a piece is strategically “more active,” and ambiguous relations, unsupported threats and strategic judgments remain unresolved. Only standalone discourse markers are treated as filler; short chess claims are not excluded just for being short. The taxonomy category comes from the check that fired, otherwise from the notebook's keyword rules.
   Verification normalizes leading conjunctions and square-first piece names such as `g7-pawn` for matching only; the original quote remains unchanged for annotation. A subject naming the mover's origin square resolves to the moved piece only when the before- and after-move pieces match. Sacrifice claims remain unresolved unless another check establishes their material meaning; one board position cannot prove a strategic sacrifice.
5. Each claim gets a route: `excluded_framing`, `auto_python_chess`, `auto_stockfish_stored`, `auto_stockfish_live`, `auto_label`, `unverifiable_strategic`, `unverifiable_hedged` or `manual`. The notebook prints the share of factual claims left for manual work.
6. Annotation: `annotation_sheet_blind.csv` holds all `manual` claims plus a seeded 10% sample of auto-verified claims (`AUTO_AUDIT_FRACTION`), shuffled and without route, model, condition or proposed verdict. Comparing the annotator's labels with the automatic ones on that sample gives the checker's precision per rule. `annotation_second_subset.csv` is the stratified second-annotator subset (`ANNOTATION_FRACTION`, Cohen's kappa). `splitting_audit.csv` is a seeded sample for checking the splitter by hand.
7. `step4_manifest.json` records the splitter settings and model digest, per-helper and notebook source hashes, config, Git state, determinism self-test and `results_sha256`.

Automatic verdicts are proposals until the audit sample has been checked by hand. Run-to-run determinism of the generating models on CPU is not guaranteed by the pilot (two repeats per condition): use the reproducibility-check cell in Step 3 and report any differences.

No Step 4 output folder cleanup is needed after a code change. Keep `splitter_cache.jsonl` and `live_stockfish_cache.jsonl`; rerunning the workflow reuses cached work and rewrites its result tables. Close Step 4 CSV files in Excel before the write cell, or Windows may deny overwrite access.

## Step 5 - blinded manual claim review
Open `step5_manual_check.ipynb` using the project's `.venv` kernel and project folder as the working directory; **you do not need to rerun Step 4 just to build a context sheet**. Its context-sheet cell reads the existing blind sheet/key and maps them to Step 3 generations, then writes a separate `outputs/step4_claims/<month>/annotation_sheet_with_context.csv`. It does not modify the existing blind sheet or key. If a later Step 4 run changes the source rows, the notebook saves a new numbered context sheet rather than overwriting the old one. The sheet includes the FEN, played move, complete multi-sentence explanation, associated sentence, claim text, and flags for whether the claim text appears verbatim in the sentence or original LLM output. The separate model self-verdict line is omitted from the explanation to reduce rating bias.

`sentence` is the source sentence associated with the splitter's quote, not necessarily the complete context or an exact container for the constructed `claim_text`. Review the whole LLM explanation and board; if the claim text is not verbatim in its context or is unclear, record the split mismatch in `NOTES` and use `unknown`. Otherwise rate it `correct`, `incorrect`, or `unknown`. Unknown claims are excluded from the primary error-rate denominator. Set `VERDICT` and optionally `CATEGORY_MANUAL` / `NOTES`, then run the save cell. Ratings are written to `outputs/step4_claims/<month>/manual_annotations_step5.csv`, separately from the original blind sheet and key. Do not rerun Step 4 after annotating; it regenerates the blind files.

## Planned steps
3. Test candidate LLMs on the fixed pilot set with `step3_llm_pilot.ipynb`, comparing conditions A-C on every position. Choose the model and settings before main generation; condition D is outside this pilot.
4. Claim extraction and verification (`step4_claims.ipynb`, above), blind second annotation (about 20% of claims, Cohen's kappa), repair condition D
5. Manual claim review (`step5_manual_check.ipynb`, above)
6. Analysis (plan frozen and git-tagged before the main generation)
