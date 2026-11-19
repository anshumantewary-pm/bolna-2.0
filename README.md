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
| Real speech-to-text and text-to-speech adapters | Next, after the vendor bake-off (PRD section 11) |
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
| `src/voice_agent/providers/fake.py` | Stand-ins used by the CLI and tests |
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

Settings are environment variables; see [.env.example](.env.example). Defaults: `claude-opus-5-5`, effort `low`, fast mode off, refusal fallbacks on.

## Next steps

1. Pick STT and TTS vendors (2-week bake-off on latency, accuracy, price) and implement `SpeechToText` / `TextToSpeech` adapters.
2. Browser audio: WebRTC media server, VAD and turn detection, real `AudioOutput`.
3. Measure latency per stage against the 800 ms p50 budget (Gate 1).
4. Phone channel, human handoff, recording, eval suite (Phase 1 pilot).
