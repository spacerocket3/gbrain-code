# Benchmarks

`evaluate.py` compares exact lexical anchors with the structural map using a
normalized file-level JSONL dataset. It intentionally contains no bundled public
benchmark corpora.

Agent Retrieval Bench is the preferred external benchmark, especially
`edit2ripple`, `trace2code`, `code2test` and no-gold subsets. Download and license
those artifacts from the upstream project.

Additional evaluators:

- `evaluate_ripple.py`: normalized edit-seed evaluation, including graph
  representation ablations;
- `evaluate_arb_edit2ripple.py`: reconstructs frozen ARB snapshots and reports
  lexical, structural, reactive and fused rankings;
- `evaluate_git_history_ripple.py`: parent-snapshot retrospective co-change
  diagnostic for relation-heavy repositories.
- `evaluate_repository_twin.py`: controlled dual-snapshot structural-delta lab,
  including a changed-state-only control for relationships deleted by a change.
- `evaluate_impact_simulation.py`: equal-budget scheduling lab that executes
  frozen scenarios on T0 and T1 and counts only newly introduced failures.

`experiments/reactive_repository_state.py` is intentionally not installed as a
CLI or exposed over MCP. See `docs/REACTIVE_REPOSITORY_STATE.md` for the current
mixed results and limitations.

The fixtures in `repository_twin_lab/` are small public mechanics cases, not an
agent benchmark or secret held-out set. See `docs/REPOSITORY_TWIN_LAB.md`.

The fixture in `impact_simulation_lab/` is also public and synthetic. Its 2,000
trials are deterministic schedule replays over six scenarios executed once per
snapshot, not 2,000 independently generated programs. See
`docs/EXECUTABLE_IMPACT_SIMULATION_LAB.md`.
