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
| Browser audio over WebRTC, then phone | Next |

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
| `src/voice_agent/providers/fake.py` | Stand-ins used by the text CLI and tests |
| `src/voice_agent/tracing.py` | Per-turn latency and token trace |

## Run it

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

### Voice mode

Talk to it through your microphone. **Use headphones**: with speakers the mic hears the agent and it interrupts itself (echo cancellation comes with the WebRTC step).

```bash
pip install -e ".[mic]"
# set ANTHROPIC_API_KEY, DEEPGRAM_API_KEY, ELEVENLABS_API_KEY, ELEVENLABS_VOICE_ID
voice-agent --voice
```

Speak normally; when you pause (~300 ms) the turn ends and the agent answers. Talk over it to interrupt. Each turn prints its latency trace.

Settings are environment variables; see [.env.example](.env.example). Defaults: `claude-opus-5-5`, effort `low`, fast mode off, refusal fallbacks on, Deepgram `nova-3`, ElevenLabs `eleven_flash_v2_5`.

## Next steps

1. Run voice mode with live keys and record latency per stage against the 800 ms p50 budget (Gate 1).
2. Browser audio: WebRTC media server with echo cancellation, replacing the local mic/speaker.
3. Phone channel (telephony provider media stream, 8 kHz), human handoff, recording.
4. Eval suite with simulated callers (Phase 1 pilot).
