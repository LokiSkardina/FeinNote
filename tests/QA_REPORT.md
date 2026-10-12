# Extended QA report — 2026-09-25

## Fix follow-up

All five original failing tests were reproduced before production changes and
passed afterward without weakening or skipping their assertions. Two narrow
tests were added: direct destruction without the shutdown hook, and failed
English-only model selection preserving Russian without recursive load signals.

Current suite: **77 total, 76 passed, 0 failed, 1 optional smoke skipped**.
The existing local Turbo smoke was run separately again and passed without
downloads or snapshot-file modification. The original QA results below are
retained as historical evidence, not the current application status.

Production changes are limited to `app_window.py`,
`controllers/app_controller.py`, and `widgets/settings_dialog.py`:

- Copy uses one restartable window-owned timer and restores a canonical icon
  using the current theme, rather than capturing another click's check icon.
- Saved feedback uses the same timer ownership pattern. Explicit exit stops
  both timers; direct Qt destruction destroys them with the window. There are
  no detached feedback lambdas or exception-swallowing lifetime workarounds.
- The existing selection-reverted signal now restores the entire Settings
  cascade from committed controller state with selector signals blocked.
- Controller settings include saved models for every engine with validated
  defaults. Settings initializes and restores that map instead of forgetting
  inactive engines. Combo population does not emit a load request.
- English-only language enforcement happens after a successful model load,
  keeping a failed candidate from changing the prior language. The rollback
  snapshot also covers language if saving the successful selection fails.

No layout, post-processing UX, dependencies, user data, packaging or commits
were changed. Manual packaged-EXE checks for tray exit, visual feedback and
actual model failure remain advisable; automated Qt tests do not replace them.

## Scope and results

Research-only automated QA. No production/UI changes, user config/notes/recordings
access, model downloads, dependency changes, credential-store operations, commits,
or builds were performed. Existing uncommitted changes were preserved.

| Run | Tests | Passed | Failed | Skipped |
| --- | ---: | ---: | ---: | ---: |
| Existing suite before additions | 50 | 50 | 0 | 0 |
| Full suite after adding 25 tests | 75 | 69 | 5 | 1 |
| Optional Turbo smoke, separate opt-in run | 1 | 1 | 0 | 0 |

The smoke test is one of the 75 tests, not a 76th distinct test. It is skipped in
the ordinary suite to avoid loading a real model on every test run. The five
regressions remain ordinary failures, not expected failures or skips.

From the project root, with the project virtual environment active:

```powershell
python -B -m unittest discover -s tests -v
python -B -m unittest tests.test_extended_qa -v
```

For one regression, append its method name to
`tests.test_extended_qa.ExtendedQATests` in the unittest command.

## Confirmed regressions

### QA-1: Copy check icon remains after overlapping clicks — low

Test: `test_copy_rapid_second_click_restores_original_icon`.

Reproduction: open a note, click the editor Copy button, click again after 100 ms,
then allow 2400 ms for both feedback callbacks. Clipboard text remains correct,
but the icon does not return to its original rendered image. The normal test of
three clicks separated by complete 2300 ms feedback cycles passes.

Cause: `app_window.py:1199` captures the button's current icon on every click.
The second click captures the check icon; its later callback restores that check.

### QA-2: Copy callback accesses a destroyed button — medium

Test: `test_copy_feedback_after_window_destruction`.

Reproduction: copy a nonempty note, request explicit exit, destroy the completed
window, and keep the Qt event loop alive for 2300 ms. The unowned single-shot
callback at `app_window.py:1203` raises:
`wrapped C/C++ object of type QPushButton has been deleted`.

This proves an unsafe widget-lifetime callback, not a demonstrated crash in the
packaged application's normal quit path. Tests intentionally keep QApplication
alive while replacing/destroying windows. No data loss was observed.

### QA-3: Saved feedback accesses a destroyed label — medium

Test: `test_autosave_feedback_after_window_destruction`.

Reproduction: open a note, edit it, flush autosave, check that Saved feedback is
visible, exit/destroy the window before 1500 ms elapses, and continue processing
Qt events for 1750 ms. The callback at `app_window.py:739` raises:
`wrapped C/C++ object of type QLabel has been deleted`.

The note is saved; the failure concerns feedback lifetime. The same caveat about
the real packaged quit/event-loop sequence applies as in QA-2.

### QA-4: Failed model load restores config but not the engine selector — medium

Test: `test_repeated_model_load_failure_restores_entire_selection`.

Reproduction: start with OpenAI Whisper/tiny/Auto, open Settings, select Faster
Whisper, and make the fake engine's load_model raise RuntimeError. Wait for the
worker to finish. Config remains OpenAI Whisper/tiny/Auto, but the selector shows
Faster Whisper/tiny/Auto. Controls are enabled again; this is stale selection,
not a permanently disabled UI. The mismatch repeats on three attempts (the test
explicitly restores the UI between attempts to reproduce each independently).

Cause: the controller rolls back config, but `app_window.py:872` only restores
the model text, not the entire engine/model/device cascade.

### QA-5: Previously selected inactive-engine model is forgotten — medium

Test: `test_persisted_inactive_engine_model_survives_new_settings`.

Reproduction: persist Faster Whisper model `small`, leave OpenAI Whisper active,
construct fresh Settings (as after app restart), then switch to Faster Whisper.
Expected: `small`. Actual: first catalog entry `tiny.en` is selected and loaded
by the fake engine instead. This can replace the saved engine-specific choice.

Cause: `widgets/settings_dialog.py:321` initializes only the active engine's
entry in `_selected_models`; `_populate_models` falls back to the first item
for an inactive engine despite its persisted model in ConfigManager.

## Coverage and boundaries

| Area | Automated evidence |
| --- | --- |
| Main window / Settings | Real widgets, 20 modal open/close cycles, five tabs per cycle, theme changes and disk reload |
| Copy | Clipboard Unicode, pixel comparison of copy/check icons, three completed timeout cycles, rapid clicks, destruction |
| Recording | Real state machine with fake recorder; recording/pause/resume/cancel, stop-to-processing, timer/cancel visibility, status, enabled controls |
| Advanced | Zero/minimum step/intermediate/maximum temperature; Beam/Best limits and dependencies; condition flag; empty/Russian/long/Unicode prompt; disk/new-dialog reload; reset; 40 rapid edits |
| Engine/model/device | Fake inference and cache/device availability; distinct catalogs, both engine directions, CPU/GPU/Auto, Russian, .en restriction, three failures, inactive-engine persistence |
| Post-processing | Real loopback GET /models and POST /chat/completions; 401/404/500; invalid JSON; refused connection; socket timeout; malformed URL/empty fields; Unicode; UI temperature 0/2; Refine validation |
| Network/UI | Qt heartbeat during delayed HTTP; success/failure/success requests restore Test button; threaded pipeline sends only transcript text |
| Notes | Selected-folder creation, GUI editor/autosave, search through controller, move/delete/reload; Unicode/80-character names; empty/81-character rejection; duplicate names; color/icon alias persistence |
| Cleanup | Existing disabled/count/age-boundary tests; GUI current-folder/all/age scopes; No preserves notes; Yes deletes only fixtures; deleting folder moves notes to Inbox |
| Application/tray | Existing fake-tray tests: minimize/close preferences, Open/double-click restore, Start/Pause/Resume/Stop/Cancel, Copy latest, Exit, shutdown waits; audio retention/import ownership |
| Stress | 20 Settings cycles / 100 tab clicks, 40 advanced changes, 8 folder create/delete cycles, repeated copy, 3 model failures, 3 HTTP connection tests |

The existing unittest stack is retained. Qt QTest operates on real widgets;
there is no coordinate-based desktop automation or new dependency. Some fields
are set via their public widget APIs and production signals. Repository and
confirmation paths also use direct methods where a native popup is unnecessary.
This is not an assertion that every scenario was exercised exclusively by mouse
or keyboard or that layout has been visually validated.

All fixture files are under project-local TemporaryDirectory instances. The
recorder and inference engines are mocked except the separate Turbo smoke.
Credentials use the existing in-memory fake. HTTP servers bind only 127.0.0.1
on ephemeral ports, with proxy-free HTTP openers. There are no cloud requests.

The timeout test uses a real delayed socket response and a test-only opener
override of 50 ms; it additionally verifies production requested 60 seconds.
It does not wait the full production timeout. Refine availability checks valid
configuration, not whether a server is reachable or a model can generate text.

Unhandled Qt exceptions are captured and asserted as failures, avoiding process
abortion. Older fixture callbacks are drained separately before the new class;
dedicated destruction tests reproduce lifetime errors without cross-test timing.
Normal teardown drains feedback while widgets remain alive. No production
testability changes were required.

## Optional real-model smoke

One separate run passed with the existing local snapshot:
`models/faster-whisper/models--mobiuslabsgmbh--faster-whisper-large-v3-turbo/snapshots/0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf`.

It used the production FasterWhisperEngine with a literal local snapshot path,
offline environment flags and one second of temporary generated silence. It
verified the result contract/duration and unchanged snapshot-file sizes and
modification times. This tests local loading/decoding/VAD, not speech accuracy.

```powershell
$env:FEINNOTE_QA_TURBO_SMOKE = '1'
python -B -m unittest tests.test_extended_qa.ExtendedQATests.test_optional_local_turbo_smoke -v
Remove-Item Env:FEINNOTE_QA_TURBO_SMOKE
```

## Remaining manual EXE smoke

- Native Windows tray registration/menu appearance and real minimize/restore.
- Actual microphone/device loss, driver behavior, recording quality and CUDA
  transcription of speech under hardware load.
- Windows Credential Manager integration (deliberately not accessed).
- Existing packaged EXE resource/DLL resolution, relocation and data paths; no
  new build was created.
- DPI scaling, theme contrast, clipped labels, popup positioning, tooltip timing
  under real pointer interaction, focus and accessibility. Offscreen tests do
  not establish visual quality.
- Actual external compatible servers/authentication/model generation, deliberately
  excluded. Local fake HTTP coverage is not compatibility certification.
- Exit immediately after Copy/autosave in a packaged process, to determine the
  practical impact of QA-2/QA-3 when the real event loop exits immediately.

## UX observations (no UI changes)

- Duplicate folder display names are accepted. IDs remain distinct, but users
  may find the resulting selector entries ambiguous.
- Folder names have an 80-character limit; boundary validation is working.
- Connection success validates /models only, not generation. The current success
  message explicitly communicates this distinction.
- A malformed unsaved provider form leaves the previous valid saved configuration
  active. Tests of invalid Refine availability start from an unconfigured state;
  an unsaved draft is not the same as invalid persisted settings.

One exploratory stress run encountered WinError 5 during atomic config replace;
the condition did not recur in subsequent complete stress runs. Its cause is
unconfirmed and it is not counted as a confirmed application regression.
