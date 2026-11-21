# bolna-2.0: real-time voice AI agent

A prototype voice agent that holds a natural spoken conversation and takes actions through tools, with human handoff and phone calls on the roadmap. It is built for customer-support and scheduling use cases where callers should hear the first word of a reply within 800 ms of finishing their sentence. Product requirements, system design and architecture: [docs/PRD.md](docs/PRD.md).

> This is an independent project. It is not a fork of, and does not use code from, the open-source Bolna voice-AI framework.

## Features

- Streaming pipeline: speech-to-text, LLM agent loop with tools, then text-to-speech, with the first sentence spoken while the rest is still being generated
- Barge-in: the caller can interrupt; audio stops and the next turn knows what the caller actually heard
- Filler line ("One moment...") when a tool is slow, plus hard per-tool timeouts
- Tool gateway with schema validation and errors returned to the model; sample `lookup_order` tool on mock data
- Per-turn latency trace (time to first text, first sentence, first audio) and token counts
- Three ways to try it: text CLI, local microphone voice mode, and browser calls with echo cancellation

## Tech stack

- Python 3.11+, packaged with setuptools (`pyproject.toml`)
- [Anthropic Python SDK](https://pypi.org/project/anthropic/) (Claude Messages API) for the agent loop
- Deepgram live speech-to-text (`nova-3`) over WebSocket
- ElevenLabs streaming text-to-speech (`eleven_flash_v2_5`)
- `httpx`, `websockets`; optional `sounddevice` for local audio
- `pytest` with GitHub Actions CI ([.github/workflows/tests.yml](.github/workflows/tests.yml))

## Status: Phase 0 (prototype)

| Piece | State |
| --- | --- |
| Session core: turn state machine, streaming speech, barge-in, filler line for slow tools | Done |
| Claude agent loop: streaming, tool calls, prompt caching, refusal fallback, interruption-safe history | Done |
| Tool gateway: schema validation, timeouts, errors returned to the model | Done |
| `lookup_order` read-only tool (mock data) | Done |
| Per-turn latency trace (time to first text, first sentence, first audio) | Done |
| Text-mode CLI for trying the agent without audio | Done |
| Deepgram streaming speech-to-text (`nova-3`) + turn detection and barge-in | Done |
| ElevenLabs streaming text-to-speech (`eleven_flash_v2_5`) with word timing | Done |
| Voice mode on your own computer (`voice-agent --voice`) | Done, not yet tried with live keys |
| Browser calls with echo cancellation (`voice-agent --web`) | Done, not yet tried with live keys |
| Phone calls | Next |

## How a turn flows

```text
caller text ──► ClaudeAgent.run_turn ──► text deltas ──► SentenceChunker ──► queue
                     │   ▲                                                    │
                     ▼   │ tool results                                       ▼
                 ToolRegistry                               TextToSpeech ──► AudioOutput
```

The agent stream and the speaker run at the same time, so the first sentence is spoken while Claude is still writing the rest. A new caller utterance cancels both, stops audio, and the next turn tells Claude what the caller actually heard.

| Module | What it does |
| --- | --- |
| `src/voice_agent/session.py` | One call's session actor: state machine, speaker task, barge-in, filler |
| `src/voice_agent/agent/claude_agent.py` | Claude Messages API loop with tools; keeps history valid when interrupted |
| `src/voice_agent/agent/prompts.py` | Spoken-style system prompt, filler and refusal lines |
| `src/voice_agent/chunker.py` | Cuts streaming text into speakable sentences or clauses |
| `src/voice_agent/tools/` | Tool gateway and the `lookup_order` tool |
| `src/voice_agent/providers/base.py` | Interfaces for STT, TTS and audio output (vendor adapters plug in here) |
| `src/voice_agent/providers/deepgram.py` | Deepgram live STT over WebSocket |
| `src/voice_agent/providers/elevenlabs.py` | ElevenLabs streaming TTS; tracks which words each audio chunk covers |
| `src/voice_agent/providers/local_audio.py` | Microphone and speaker (16 kHz PCM) for voice mode |
| `src/voice_agent/turns.py` | End-of-turn and barge-in detection from STT events |
| `src/voice_agent/voice.py` | Voice mode: mic → Deepgram → Claude → ElevenLabs → speaker |
| `src/voice_agent/web/server.py` | Browser calls: serves the page, one session per WebSocket, real-time audio pacing |
| `src/voice_agent/web/static/index.html` | Call page: mic capture with echo cancellation, playback, transcript, latency table |
| `src/voice_agent/providers/fake.py` | Stand-ins used by the text CLI and tests |
| `src/voice_agent/tracing.py` | Per-turn latency and token trace |

## Getting started

Requires Python 3.11+.

```bash
pip install -e ".[dev]"
pytest -q                      # offline, no API calls
```

Talk to it in text mode (uses the Claude API, so it needs credentials: `ANTHROPIC_API_KEY`, or run `ant auth login` once):

```bash
voice-agent
```

Illustrative session (Claude's wording varies; your lines have no prefix):

```text
Hi, where's my order?
agent> Sure, I can help with that. What's your order ID?
  [turn 1 | llm_first_text ... ms | first_sentence ... ms | first_audio ... ms | done ... ms | tokens ...]
A1001
agent> One moment... Your order A one zero zero one has shipped with UPS ...
```

Type while the agent is still "talking" to interrupt it. Order IDs in the mock data: A1001 (shipped), A1002 (processing), A1003 (delivered).

### Browser calls (recommended)

```bash
# set ANTHROPIC_API_KEY, DEEPGRAM_API_KEY, ELEVENLABS_API_KEY, ELEVENLABS_VOICE_ID
voice-agent --web
```

Open http://localhost:8080 in Chrome or Edge, click **Start call**, allow the microphone, and talk. The browser's echo cancellation means speakers work. The page shows the transcript, the agent's state, and a latency row per turn (first text, first sentence, first audio, in ms after you stop talking).

The page and the server talk over a WebSocket: 16 kHz 16-bit PCM both ways plus JSON events. The server binds to `127.0.0.1` by default; it has no login, so don't expose it publicly. A WebRTC media server replaces this transport for production and telephony.

### Voice mode (local mic, no browser)

Talk to it through your microphone. **Use headphones**: with speakers the mic hears the agent and it interrupts itself (echo cancellation comes with the WebRTC step).

```bash
pip install -e ".[mic]"
# set ANTHROPIC_API_KEY, DEEPGRAM_API_KEY, ELEVENLABS_API_KEY, ELEVENLABS_VOICE_ID
voice-agent --voice
```

Speak normally; when you pause (~300 ms) the turn ends and the agent answers. Talk over it to interrupt. Each turn prints its latency trace.

Settings are environment variables; see [.env.example](.env.example). Defaults: `claude-opus-5-5`, effort `low`, fast mode off, refusal fallbacks on, Deepgram `nova-3`, ElevenLabs `eleven_flash_v2_5`.

### Environment variables

Copy [.env.example](.env.example) to `.env` and fill in your own values. Variable names:

| Variable | Purpose |
| --- | --- |
| `ANTHROPIC_API_KEY` | LLM credentials (or run `ant auth login` once) |
| `VOICE_AGENT_MODEL`, `VOICE_AGENT_EFFORT`, `VOICE_AGENT_FAST`, `VOICE_AGENT_FALLBACKS` | Model and speed settings |
| `DEEPGRAM_API_KEY`, `DEEPGRAM_MODEL` | Speech-to-text |
| `ELEVENLABS_API_KEY`, `ELEVENLABS_VOICE_ID`, `ELEVENLABS_MODEL` | Text-to-speech |
| `VOICE_AGENT_FILLER_AFTER_S`, `VOICE_AGENT_TOOL_TIMEOUT_S` | Tool filler delay and timeout (seconds) |

## Project structure

```text
.
├── docs/PRD.md              # requirements, system design, architecture
├── src/voice_agent/
│   ├── agent/               # agent loop, prompts, events
│   ├── providers/           # STT, TTS and audio adapters (Deepgram, ElevenLabs, local, fakes)
│   ├── tools/               # tool gateway and lookup_order
│   ├── web/                 # browser-call server and static call page
│   ├── session.py           # per-call session actor
│   ├── turns.py, chunker.py, voice.py, tracing.py, config.py, cli.py
├── tests/                   # offline pytest suite
├── .env.example
└── pyproject.toml
```

## Roadmap

1. Run browser calls with live keys and record latency per stage against the 800 ms p50 budget (Gate 1).
2. Phone channel (telephony provider media stream, 8 kHz), human handoff, recording.
3. Eval suite with simulated callers (Phase 1 pilot).
4. WebRTC media server (LiveKit) for production networks.

## License

[Apache License 2.0](LICENSE)

---

Anshuman Tewary · [GitHub](https://github.com/anshumantewary-pm) · [LinkedIn](https://www.linkedin.com/in/tewary-anshuman/)
