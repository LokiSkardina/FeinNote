# FeinNote

**Local speech transcription, AI-assisted text refinement, and note management in one Windows desktop app.**

FeinNote is a portable application for turning recordings and audio files into editable notes using **OpenAI Whisper** or **Faster Whisper**. Transcription runs locally; optional AI post-processing can improve readability using a local server or a compatible cloud provider.

Record a thought, import a conversation, or queue multiple files—then review, organize, copy, and export the results without juggling separate tools.

![FeinNote main window](docs/screenshots/main.png)

## Features at a glance

- **Local transcription** with OpenAI Whisper and Faster Whisper; model, language, and CPU/GPU controls.
- **Microphone recording and audio import**, including batch transcription of multiple files.
- **Optional AI refinement** through OpenAI-compatible endpoints, including OpenRouter and local servers such as LM Studio.
- **Original and refined text** available within the same note when refinement succeeds.
- **Notes and folders** with editing, autosave, folder customization, and retention/cleanup tools.
- **Exports** to TXT, DOCX, PDF, SRT, and VTT.
- **Model memory controls**, system-tray actions, and light/dark themes.

## 1. Local transcription

FeinNote uses Whisper-based speech recognition on your computer. Choose between the original **OpenAI Whisper** engine and **Faster Whisper**, select an available model and spoken language, and configure whether processing uses the CPU or a supported NVIDIA CUDA GPU.

You can record directly from a microphone or import an existing audio file. Common audio formats are supported, subject to the installed audio-decoding components. Models are stored locally; first-time model setup may require a download.

The **Transcription** settings cover engine, model, device, and language. Expand **Advanced Parameters** for decoding controls such as temperature, beam size, best-of sampling, conditioning on previous text, and an initial prompt. These settings affect recognition behavior and may need tuning for different recordings; there is no single best preset for every speaker or language.

| Transcription settings | Advanced parameters |
|:---:|:---:|
| ![Transcription engine, model, device and language](docs/screenshots/transcription.png) | ![Advanced transcription parameters](docs/screenshots/transcription_2.png) |

> **A note on accuracy:** Whisper can mishear names, numbers, or technical terms and occasionally generate words not present in the audio. Always verify important details against the original recording.

## 2. Notes and organization

Every completed transcription becomes a note in FeinNote. Open it from the sidebar to review or edit the text; changes are saved automatically. Use folders to separate work, personal recordings, interviews, and other material.

Folder appearance can be customized with colors and icons. **Retention Policies** and **Manual Cleanup** help manage older notes, while a configurable confirmation protects against accidental individual-note deletion.

**Right-click a note card** to open its context menu. From there you can open the note, copy its text, export it, move it to another folder, or delete it. The sidebar also provides quick actions for routine note management.

| Folders and appearance | Retention and cleanup |
|:---:|:---:|
| ![Note folders and appearance settings](docs/screenshots/notes.png) | ![Note retention and manual cleanup settings](docs/screenshots/notes_2.png) |

## 3. AI post-processing (Refine)

Whisper's transcription is often accurate enough to understand, but spontaneous speech can produce long, unpunctuated paragraphs. **Refine** optionally sends the transcribed *text* to an AI language model to improve formatting, punctuation, paragraph breaks, and dialogue structure.

FeinNote supports **OpenAI-compatible APIs**. You can configure a cloud provider such as **OpenRouter** or a local model server such as **LM Studio**, choose a model, and test the connection. Provider availability, authentication, costs, and model behavior depend on the service you select.

The post-processing settings also offer a configurable **temperature** and **prompt**, so you can choose whether the model should preserve conversational wording closely or apply more substantial formatting. An overly aggressive prompt may rewrite facts, so review refined output when accuracy matters.

| Provider and model setup | Refinement parameters |
|:---:|:---:|
| ![Post-processing provider and model settings](docs/screenshots/post-processing.png) | ![Post-processing prompt and temperature settings](docs/screenshots/post-processing_2.png) |

### Original vs. Refined

A successful Refine operation **does not create a second note**. FeinNote keeps the original Whisper transcription and the refined version associated with the same note. Use **Original / Refined** in the editor to compare them. The original is preserved for reference, while the refined text is the normal editable result.

**Privacy:** speech recognition runs locally. For Refine, the *transcribed text* is sent to the configured provider; the audio recording is not sent as part of that text-refinement request. If you use a cloud provider, its own privacy policy and data-handling terms apply. Leave Refine disabled to keep this workflow local.

## 4. Application settings and model memory

The **Application** section contains appearance and behavior options, including light/dark themes, tray behavior, and whether to retain original microphone recordings.

FeinNote also lets you control how long the transcription model stays in memory. This matters when a CUDA GPU has limited VRAM or when another application needs the same GPU.

- **Keep model loaded:** fastest for repeated transcriptions, but continues using RAM/VRAM.
- **Release before post-processing:** unload the transcription model when Refine is about to run; useful when a local language model needs GPU memory.
- **Release after transcription:** unload the model after each transcription, trading faster reuse for lower persistent memory usage.

You can also **load or unload the selected model manually** and check its current loading state. These options affect the transcription model, not the remote model hosted by a cloud API.

| Application preferences | Model memory management |
|:---:|:---:|
| ![Application appearance and behavior settings](docs/screenshots/application.png) | ![Model memory modes and manual loading controls](docs/screenshots/application_2.png) |

## 5. Batch transcription and export

Select **multiple audio files** in the import dialog to add them to a processing queue. FeinNote processes the recordings sequentially and creates a separate note for each one, so you do not need to import files one at a time. The queue provides progress and per-file status information. Optional Refine can also be used with batch-imported recordings.

Notes can be exported in the following formats:

| Format | Purpose |
|---|---|
| **TXT** | Plain-text transcript |
| **DOCX** | Editable document for word processors |
| **PDF** | Readable, shareable document |
| **SRT** | Subtitles with numbered cues and timestamps |
| **VTT** | WebVTT subtitles with timestamps |

SRT and VTT use **timestamps and segment text produced by Whisper**, not timestamps invented from an AI-rewritten transcript. Subtitle export therefore requires timing information; it may be unavailable for older notes that were saved without segments. Standard document exports use the note's final text.

## 6. Getting started

### Portable Windows build

1. Download the Windows portable archive from the repository's **Releases** page, when a release is available.
2. Extract the **entire archive** to a folder you can write to. Do not run the executable directly from inside the ZIP.
3. Launch `FeinNote.exe`.
4. In **Settings → Transcription**, select an engine, model, language, and device. Follow any model setup/download prompts.
5. Click the **microphone button** to record, or the **upload button** in the top-right corner to import one or more audio files.
6. Optionally configure **Post-processing**, enable **Refine**, and review the original and refined versions.
7. Organize the resulting notes and copy or export them as needed.

FeinNote is designed as a **portable, local-first** application: keep its extracted directory together, including the `_internal` folder and any data folders it creates. Back up your notes, recordings, and configuration before replacing or deleting a portable installation.

### Running from source (advanced)

FeinNote is a **Windows-oriented Python / PyQt6** project. The current development environment uses **Python 3.11.9**. Use Python **3.11** for the setup below.

From the project root, create and activate a virtual environment:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python main.py
```

**Important:** dependencies include platform- and CUDA-specific components. The pinned PyTorch build may require an appropriate wheel source and adjustments for your Python, CUDA, or CPU-only environment; the commands above are **not yet validated as a universal clean-machine installer**. Some audio formats and the original OpenAI Whisper engine also require FFmpeg. The FFmpeg/FFprobe executables used in local development are not committed to the source repository.

### Building a portable executable (advanced)

The project's Windows builds use **PyInstaller** in one-directory mode. Packaging needs the `assets` resources, Faster Whisper package data (including its VAD assets), and the included `pyi_rth_torch_first.py` runtime hook. Certain combinations of PyQt6, PyTorch, and PyInstaller may additionally require troubleshooting DLL/import order. A clean, reproducible packaging workflow is not yet guaranteed on every machine; using a tested portable release is recommended for ordinary users.

## 7. Privacy and limitations

- **Local by default:** microphone/audio transcription is performed by the selected local Whisper engine.
- **Refine is optional:** if enabled with a cloud endpoint, the recognized text leaves your computer and may incur provider charges.
- **Credentials:** the application uses Windows Credential Manager for supported API-key storage. Do not put real API keys into screenshots, sample configurations, or bug reports.
- **Models are not included in the source repository.** Model downloads and hardware requirements vary by engine and model.
- **Transcription is not guaranteed verbatim.** Check names, numbers, medical/legal details, and other consequential information against the audio.
- **Subtitle timing reflects original recognition segments.** It does not automatically realign rewritten text from Refine.
- **Hardware support and performance vary.** CPU transcription works more slowly than an appropriate GPU in many configurations, and GPU models can require substantial VRAM.

## 8. License and acknowledgements

FeinNote is licensed under the **GNU General Public License, version 3 only (GPL-3.0-only)**. See [`LICENSE`](LICENSE) for the full license text.

Third-party libraries and bundled resources retain their respective licenses:

- **[PyQt6](https://www.riverbankcomputing.com/software/pyqt/)** — used for the desktop interface; consult its applicable GPL/commercial licensing terms.
- **[OpenAI Whisper](https://github.com/openai/whisper)** and **[Faster Whisper](https://github.com/SYSTRAN/faster-whisper)** — speech-recognition engines.
- **[Lucide](https://lucide.dev/)** — interface and folder icons; its license is included alongside the icon resources.
- **[Inter](https://rsms.me/inter/)** — interface font, licensed under the SIL Open Font License 1.1. See [`assets/fonts/OFL.txt`](assets/fonts/OFL.txt).
- **FFmpeg** — external audio-decoding utility, subject to the licensing terms of the particular build being distributed.

---

*FeinNote is an independent open-source project. It is not affiliated with OpenAI, OpenRouter, DeepSeek, or the other services mentioned above.*
