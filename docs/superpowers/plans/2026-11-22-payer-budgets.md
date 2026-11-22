# Payer Budgets Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Give each payer a hard cap on call spend that no check can pass, and that never stops a check
which has already started.

**Architecture:** Pure cost rules live in `budget.py`. Holds and budgets are stored in their own documents,
never on the episode, so the engine's episode writes cannot clobber them. The engine holds money before a
check's first call. The watchdog settles closed checks.

**Spec:** `docs/superpowers/specs/2026-11-22-payer-budgets-design.md`

## Review Focus
- Many checks of one payer starting in the same minute: none may pass the cap, and contention must
  retry the event, never block the check.
- A check that started before the money ran out must still call every neighbour.
- A redelivered step must never hold money twice for one check.
- A check closed by the watchdog must have its money settled in the same run.
- A payer with no record, or a seasonal payer outside its season: no calls, and the family is told.

### Task 1: Cost rules (`budget.py`, `tests/test_budget.py`)
`line_price_usd`, `worst_case_usd`, `spent_usd` and `budget_caps`, plus the price table in settings.

### Task 2: Stores (`store.py`, `store_firestore.py`, `tests/test_store.py`)
Payers, holds, budgets and one-time claims. Run in memory and against Firestore.

### Task 3: Engine (`engine/app.py`, `engine/machine.py`, `engine/notify.py`, `tests/test_engine.py`, `tests/test_machine.py`)
- Hold money at the first call.
- A refusal blocks the check, and the family is told urgently.
- Staff are told once a day.
- A "busy" answer raises, so the event is delivered again.

### Task 4: Watchdog (`jobs/watchdog.py`, `tests/test_watchdog.py`)
`settle_budgets` runs after closing stalled checks.

### Task 5: Family page and admin (`web/family.py`, `family.html`, `family.js`, `scripts/payers.py`)
- The invite code decides the payer.
- Staff see spend against each cap.
- The owner sets budgets with the script.
