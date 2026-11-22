# German Calls Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Calls in German, and an "automated call" notice in every call in every language.

**Architecture:** Call texts live in `scripts.py` as one render function and one closing table per language.
`Lang` (sr, en, de) is the call language. `PageLang` (sr, en) is the language of the family page and of
Telegram messages.

**Tech Stack:** Python 3.12, FastAPI, pytest, Google Cloud TTS (Chirp 3 HD), Gemini on Vertex AI.

**Spec:** `docs/superpowers/specs/2026-11-22-german-calls-design.md`

## Global Constraints
- Gemini prompts for sr and en stay byte-identical (measured on real audio on 17 Nov).
- Every call opens with the notice. Serbian: "Ovde Kuc-kuc, automatski poziv."
  English: "This is Kuc-kuc, an automated call." German: "Hier ist Kuc-kuc, ein automatischer Anruf."
- Every call text ends with its question ("?"), so that the answer is recorded.
- Never put real names or places in tests or code.

## Review Focus
- A German person with a man's name: every German pronoun follows the gender.
- A hazard with no temperature, in German: no "None", and no "{".
- A call stored before this change has no "de" anywhere. Its texts must still render.
- A user whose family-page language is "sr" must never get German Telegram texts, and a German person must
  not break those texts.

### Task 1: Notice in every call, welcome-call additions (sr, en)
Files: `src/kuckuc/scripts.py`, `tests/test_scripts.py`, `docs/uputstvo-porodica.md` if it quotes the lines.
- Test: `test_every_call_says_it_is_automated` covers every ScriptKind in every language. Update the
  verbatim tests to the new lines.
- Test: `test_welcome_call_says_computer_seven_days_and_emergency_number`.
- Change the hello lines, the welcome calls, the neighbour welcome ("deleted after seven days") and the
  neighbour follow-up (which now starts with the hello line).

### Task 2: German call texts and voice
Files: `models.py` (Lang + PageLang), `scripts.py`, `engine/app.py` (_AS_NEIGHBOR), `engine/notify.py`
(PageLang), `config.py` + `deps.py` (tts_voice_de, tts_rate_de), `deploy/deploy.sh` (env names),
`tests/test_scripts.py`.
- Tests: German verbatim for CHECK, HEADSUP cold, NEIGHBOR_ASK_SILENT for a man, INTRO_PERSON and
  NEIGHBOR_FOLLOWUP. Every script renders in de with and without a hazard. Closings and reasks in de.
- Test: `speakable(text, "de") == text`.

### Task 3: Gemini German examples
Files: `speech/interpret.py`, `tests/test_interpret.py`.
- Test: sha256 of `build_prompt(lang, q)` for sr and en, for every Question, equals the value before the
  change (computed first, on the old code).
- Test: the German yes/no prompt names "ja" and "nein", and says "German".

### Task 4: Family page choice
Files: `web/family.py` (the person's lang is Lang), `web/templates/family.html` (radio "Deutsch"),
`tests/test_family_api.py`.
- Test: a person enrolled with lang "de" gets an intro call with lang "de". The family-page language
  endpoint still refuses "de".

### Task 5: Whole suite, review, samples
- Run the whole suite and the private-words scan.
- Synthesise the German lines, plus the new Serbian and English lines, to MP3 for the user to hear.
- No release and no push without the user's yes.
