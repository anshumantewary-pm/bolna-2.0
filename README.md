# bolna-2.0 — real-time voice AI agent

A voice agent that answers calls, talks naturally, takes actions through tools, and hands off to a human when it should. Product requirements, system design and architecture: [docs/PRD.md](docs/PRD.md).

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
| Browser calls, server speech: Deepgram + ElevenLabs, echo cancellation (`voice-agent --web --speech server`) | Done, not yet tried with live keys |
| Browser calls, browser speech: Chrome/Edge recognition and voice, only a Claude key needed (`voice-agent --web --speech browser`) | Done |
| Real-browser end-to-end tests (headless Chromium, offline fakes) for both speech modes | Done |
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
| `src/voice_agent/web/browser_speech.py` | Browser speech mode: recognition results in, "speak this line" out, spoken acks |
| `src/voice_agent/web/static/index.html` | Call page: mic capture or Web Speech API, playback, interrupt, transcript, latency table |
| `src/voice_agent/providers/fake.py` | Stand-ins used by the text CLI and tests |
| `src/voice_agent/tracing.py` | Per-turn latency and token trace |

## Run it

Requires Python 3.11+.

```bash
pip install -e ".[dev]"
pytest -q                      # offline, no API calls
```

The end-to-end tests drive the real call page in headless Chromium (fake microphone, stubbed Web Speech API, offline fake speech and agent). They are skipped unless Playwright is installed:

```bash
pip install -e ".[dev,e2e]"
playwright install chromium
pytest -q -m e2e
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
voice-agent --web
```

Open http://localhost:8080 in **Chrome or Edge**, click **Start call**, allow the microphone, and talk. The page shows the transcript, the agent's state and a latency row per turn. Talk over the agent (or press **Interrupt**) to cut it off.

There are two speech modes. `--speech auto` (the default) uses server speech when all its keys are set, and browser speech otherwise.

| | Browser speech (`--speech browser`) | Server speech (`--speech server`) |
| --- | --- | --- |
| Needs | Claude credentials only | Claude + `DEEPGRAM_API_KEY`, `ELEVENLABS_API_KEY`, `ELEVENLABS_VOICE_ID` |
| Listening | The browser's speech recognition (Chrome/Edge send audio to their own speech service) | Deepgram `nova-3`, streamed from the server |
| Voice | The browser's built-in voice | ElevenLabs `eleven_flash_v2_5` |
| On the socket | Text only | 16 kHz PCM both ways |
| Talking over the agent | With headphones: tick "I'm wearing headphones". On speakers the page ignores the mic while the agent talks (so it doesn't hear itself); use **Interrupt** | Works on speakers (browser echo cancellation) |
| Use it for | Trying the agent end to end in a minute; demos | Production-quality voice and latency measurement |

In browser speech mode the "first audio" latency column is when the server asked the browser to speak; the browser's voice usually starts a few tens of ms later. After a barge-in the agent is told which *sentences* the caller heard (server speech tracks it per word).

The page and the server talk over a WebSocket (protocol in `web/server.py` and `web/browser_speech.py`). The server binds to `127.0.0.1` by default and has no login, so don't expose it publicly. Browsers only allow the microphone on `localhost` or HTTPS. A WebRTC media server replaces this transport for production and telephony.

### Voice mode (local mic, no browser)

Talk to it through your microphone. **Use headphones**: with speakers the mic hears the agent and it interrupts itself (echo cancellation comes with the WebRTC step).

```bash
pip install -e ".[mic]"
# set ANTHROPIC_API_KEY, DEEPGRAM_API_KEY, ELEVENLABS_API_KEY, ELEVENLABS_VOICE_ID
voice-agent --voice
```

Speak normally; when you pause (~300 ms) the turn ends and the agent answers. Talk over it to interrupt. Each turn prints its latency trace.

Settings are environment variables; see [.env.example](.env.example). Defaults: `claude-opus-5-5`, effort `low`, fast mode off, refusal fallbacks on, Deepgram `nova-3`, ElevenLabs `eleven_flash_v2_5`.

## Next steps

1. Run browser calls with live keys (server speech) and record latency per stage against the 800 ms p50 budget (Gate 1).
2. Phone channel (telephony provider media stream, 8 kHz), human handoff, recording.
3. Eval suite with simulated callers (Phase 1 pilot).
4. WebRTC media server (LiveKit) for production networks.
