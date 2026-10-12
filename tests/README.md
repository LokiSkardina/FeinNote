# Application, post-processing and folders verification

Run from the project root with the virtual environment active:

```powershell
.\.venv\Scripts\Activate.ps1
python -B -m unittest discover -s tests -v
```

The suite uses project-local temporary directories, fake credentials and fake
network providers. It does not load a transcription model, record audio, call
a cloud service, modify the user's notes, or write Windows credentials. PyTorch
is imported before Qt, matching the application's Windows DLL startup order.

## Storage compatibility

- The existing JSON repository remains the only note storage mechanism.
- `notes/folders.json` contains versioned folder metadata. Inbox is permanent.
- New notes live under `notes/<folder-id>/`; folder names are display values,
  never filesystem paths. IDs remain unchanged after rename.
- Existing flat `notes/*.json` notes remain in place and are read as Inbox.
- `full_text` remains the displayed/editor text for compatibility. `raw_text`
  stores the original transcription; `created_at`, `folder_id`, `segments`, and
  `schema_version` are added to new notes. Legacy defaults are supplied on read.
- Physical location is authoritative for folder membership, including recovery
  from an interrupted metadata update/move. Moves use collision-free filenames.
- Editing a legacy note preserves its previous text as `raw_text`.
- Deleting a folder moves readable notes to Inbox first. Unexpected/unreadable
  files prevent folder deletion rather than being discarded.
- No startup migration rewrites or removes legacy history.

## Pipeline and retention

Whisper/Faster Whisper remain responsible for audio decoding. The controller
saves raw transcription first, then optionally sends only that text and the
editing prompt to the configured provider in a Qt worker. A successful response
updates `full_text`; failures leave the already-saved original intact.

Connection tests issue GET `/models`, not a completion. They establish server
accessibility/authentication, not model generation readiness. Generation issues
POST `/chat/completions`, with a 60-second timeout, no automatic retry, and no
redirect following. Cloud URLs require HTTPS. Both provider types use the same
adapter interface. Keys are kept in Windows Credential Manager under FeinNote
targets and excluded from JSON configuration and error details.

Retention defaults to disabled. Background workers calculate eligible paths;
the controller deletes them in small Qt timer batches and refreshes the sidebar.
Unknown/invalid creation dates are preserved. Manual deletion operates on the
exact snapshot shown in its confirmation. Folder changes during automatic
planning invalidate outdated plans. Automatic cleanup runs at startup and after
new note creation, not immediately when editing a retention rule.

## Manual QA checklist

1. Start the app with existing history. Confirm old notes appear in Inbox.
2. Click Refine with no provider. It should remain off and open Post-processing
   with a configuration message.
3. Start an LM Studio server. Select Local server, enter its base URL including
   `/v1`, model ID, optional key, temperature and prompt; save and test.
4. Enable Refine, transcribe a short recording/file, and observe Transcribing,
   Refining, Ready. Check `raw_text` and `full_text` in the saved JSON.
5. Stop the server and repeat. An error should appear and the raw note should
   still be usable. Restart the app to check settings and Refine persistence.
6. Optionally configure a cloud-compatible endpoint and masked key. Connection
   testing does not generate text. Any real cloud generation is user-initiated
   and may incur the provider's usual charges.
7. Create a folder, rename it, change its color/icon, and restart the app.
   Select it in the sidebar and create a note. Verify the destination and filter.
8. Right-click a note and move it to another folder. Delete a user folder after
   confirmation; its notes should appear in Inbox. Inbox cannot be deleted.
9. In a disposable folder, enable Keep latest 2 and create three notes. Check
   that the oldest disappears without affecting another folder.
10. Test age retention with disposable old notes. Verify the strict older-than
    boundary and that disabled cleanup preserves notes.
11. Test all three manual cleanup actions with disposable notes. Cancel first,
    then confirm. Check counts, scope, sidebar, and clearing of a deleted open note.
12. Verify both transcription engines, CPU/GPU selection, recording/pause/stop,
    and light/dark settings appearance on actual hardware.
13. Restart after changing each Application toggle and verify persistence.
    Check minimize and X with tray settings both off and on. Double-click the
    tray icon, use Open FeinNote, and repeat; only one icon should exist.
14. Use tray Exit while recording and while transcribing. The window must not
    hide again; exit waits for active work and capture resources to finish.
15. Record twice with Keep recording audio on; verify distinct WAV files and
    relative audio_path references. Turn it off and verify successful recordings
    leave no temporary WAV. Import a file and confirm its source is untouched.
16. Check Application and Post-processing at Windows display scaling settings;
    scroll to the prompt reset control. Check both existing themes.
17. Test an actual packaged build from a different working directory and after
    moving the application folder. Confirm data stays beside the executable.

## Application behavior and portable data

Application preferences default to false and are saved under `application` in
config.json. If no system tray is available, X performs controlled exit instead
of hiding an inaccessible window. Explicit Exit always bypasses close-to-tray.
Shutdown stops capture, flushes autosave, and waits for active jobs; it does not
forcefully terminate threads or interrupt native model calls.

Configuration, notes, models, logs and recordings use the application data root:
the source project directory or packaged executable directory, not the working
directory. Bundled resources are resolved separately.

Microphone sessions start in `recordings/.temporary/` with unique filenames.
With audio retention enabled, successful transcription moves audio to a unique
`recordings/<folder-id>/recording_<timestamp>_<random>.wav` and saves its relative path in the
note. New notes use schema version 3; legacy notes receive nullable `audio_path`
on read without a bulk migration. Imported audio is never moved or deleted.
With retention disabled, temporary audio is deleted only after raw note saving
succeeds. Failed or interrupted sessions preserve audio for manual recovery.
Deleting a note or applying note retention does not delete retained recordings;
these can accumulate and currently require manual housekeeping.

## Settings, icons and tray verification

Settings and the icon picker share typography/control rules in `utils/themes.py`.
The main sidebar uses the same scrollbar helper. Numeric fields retain native
text and keyboard input, but hide the old native arrow buttons. Settings wheel
events over closed combo/spin controls scroll the surrounding page. Dropdowns
draw theme-aware Lucide chevrons. No palette replacement or npm runtime is added.

`assets/icons/lucide/` contains official release 1.47.0 SVGs and its full LICENSE.
Include this directory (including LICENSE) when bundling the application.
`utils/icon_manager.py` resolves resource paths, replaces currentColor in memory,
and bounds SVG/pixmap/icon caches. Unknown names fall back to folder; old folder
aliases remain readable. The picker filters filenames and asks for decorations
on demand, rather than rasterizing the entire catalog at startup.

New retained microphone audio uses stable folder IDs (including inbox). Renaming
a folder does not change its audio paths. Legacy flat paths are not migrated;
moving or deleting a note does not relocate or delete retained audio.

Tray actions use the same controller as the main microphone button. Copy last
note searches across folders using creation time and reads full_text at click
time, including successful refinement. Opening the tray menu refreshes action
availability. Busy, stopping, cancelling and shutdown states block a new capture.

### Additional manual QA

1. At Windows 100%, 150% and 200% scaling, open all five Settings pages in both
   themes. Scroll to Reset prompt and the final manual-cleanup controls. Expand
   Advanced Parameters; type numbers and test Up/Down and mouse-wheel input.
2. Search the folder picker for gamepad, book, code and music. Choose an icon,
   save/restart, and check its colored appearance in the sidebar. Test Cancel and
   an empty search result without changing the saved icon.
3. With the window hidden, Start recording from the tray, Pause, Resume and Stop.
   Verify one transcription and optional Refine. Repeat with Cancel: no new note
   and no leftover cancelled WAV. Try Start while a model/transcription is busy.
4. Copy last note with no notes, with notes in another folder, after Refine, and
   after deleting the newest note. Confirm the latest surviving full_text is used.
5. Retain recordings in Inbox and a custom folder; inspect relative audio_path.
   Rename/move a note's folder and confirm its original retained path still exists.
6. Recheck Open, double-click restore, close/minimize-to-tray, and explicit Exit
   while recording or transcribing. Test native Windows tray and microphone;
   automated tests replace the operating-system tray, input and inference engine.

## Settings interaction safety

`widgets/settings_help.py` holds all help text and descriptions for exposed model
names. `SettingsInteractionManager` uses one 2000 ms timer per Settings dialog,
suppresses native immediate tooltips, and cancels on leave, click, scroll, hide or
deactivation. Standard QToolTip rendering supports the existing theme. The same
root-scoped filter forwards closed combo/spin wheel events to their scroll area;
popup list scrolling, direct typing and keyboard arrows remain native.

Settings model changes consult the existing registry immediately before loading.
Missing weights require Download confirmation (Cancel is the default). Cancel
restores the current engine/model/device controls and leaves configuration and
language unchanged. Controller requests also reject unconfirmed missing models.
Startup loading of the persisted selection is unchanged. There is no safe
cooperative download cancellation in the current engine contract: Exit still
waits for an explicitly started load/download; no native thread termination is used.

Advanced Parameters explain which of Beam Size / Best Of is unused while
preserving both stored values. Configuration warnings continue to be logged only,
never replacing the main runtime status.

### Interaction manual QA

1. Hover tabs, labels, enabled/disabled inputs and toggles: help should appear
   after about two seconds. Leave early, move between fields, scroll or close
   Settings: stale help must not appear or remain visible.
2. Scroll over model/provider/folder selectors and numeric fields: only the page
   should move. Open a dropdown and click an option; test numeric typing and keys.
3. Choose an uncached model for each engine, then Cancel: no download, config or
   language change. Repeat and choose Download only if you intend to acquire it.
   Verify a cached model switches without confirmation.
4. Set distinct Beam Size / Best Of values; alternate Temperature 0 and >0, close
   and reopen Settings. Verify values survive and inactive-field hints match.
5. Transcribe with Temperature >0: the main status should show normal runtime
   progress, not the beam-size warning. Check Exit during an intentional load.

## Limits

Native Windows credential read/write, real microphone/GPU execution, LM Studio,
and paid cloud generation are not exercised by automated tests. The Settings
pages retain the existing window dimensions and scroll when necessary. Very
large history rendering and manual cleanup previews still use the existing
synchronous repository reads. Native non-compatible API adapters and Whisper
advanced-parameter redesign remain outside this iteration.

## Restricted sandbox execution

Activate the project `.venv` before all commands. The standard complete command
remains `python -m unittest discover -s tests -q`.
If the environment forbids local IPC or loopback connections, use the explicitly
labelled subset `python -m tests.run_offline`. It prints all nine excluded real
integration tests; it does not count them as passed or change their assertions.
The optional real-model smoke test remains opt-in and skipped by default.
See `test_results/sandbox_diagnosis.md` and `test_results/sandbox_probe.py` for
operation-level evidence. Do not weaken Windows security to run these tests.
