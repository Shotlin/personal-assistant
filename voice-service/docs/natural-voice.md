# Making the voice sound human (Sarvam Bulbul v3) — research notes

Researched 2026-10-05 from docs.sarvam.ai (every page below was read in full or queried for its rules).
Used by `src/conversation.mts` (prompt), `src/delivery.mts` (tags -> pace/temperature), `src/sarvamTts.mts`.

## Why the first version sounded like "an AI voice"
1. **Text written for the eye.** The prompt banned fillers and gave one flat sentence. Sarvam's best-practices page says
   natural speech comes from the *text*: commas = short pause, `।`/`.` = medium, `!` = emphasis, `…` = hesitation (rarely),
   blank line = breath, and fillers/hesitations (`um, uh, hmm, basically…, I mean…`) for a conversational feel.
2. **English words in the wrong script.** Rule: *English words in English letters, Hindi in Devanagari* (`Sarvam AI में आपका…`,
   not `सरवम एआई…`). We let the model write `গ্রোসারি অ্যাপ`, `মিটিং`, `ড্যাশবোর্ড`: Bulbul pronounced them flat.
3. **One fixed delivery.** A single pace (0.88 is the doc's "meditation/EdTech narration" range) and one temperature for every
   sentence. Real people speed up when excited and slow down when thinking.
4. **Sani as the speaker's name.** Sani is the software; the voice is a character (Shubh).

## What Bulbul v3 really offers (and does not)
- Controls: `pace` 0.5–2.0, `temperature` 0.01–1.0, `speaker`, pronunciation dictionary (`dict_id`). **No SSML, no emotion tags,
  no pitch/loudness** (those are bulbul:v2 only; the API returns 400 on v3).
- Pace: 1.0 native/conversational, 0.8–0.9 relaxed/measured, 1.1 brisk, 1.2–1.5 energetic.
- Temperature: 0.6 balanced (agents), **0.7–0.8 expressive, warm, conversational (voice personas)**, 0.9–1.0 highly expressive/variable.
- Config can change **between sentences on one WebSocket** (verified: pace 0.8 -> 2.94 s, 1.2 -> 1.93 s for the same sentence).
- Streaming is capped at 24 kHz; a WhatsApp call carries 16 kHz, so 48 kHz REST adds nothing here.
- Voices (CER = critical pronunciation errors): mani 0.00, priya/ishita 0.13, roopa 0.26, **shubh 0.30**, pooja 0.30, ratan/rehan 0.33…
  Recommended by language — Hindi: shubh, ashutosh (m), priya, suhani (f). **Bengali: rehan (m), roopa, suhani (f).** English: ratan, ishita.
  `shubh` is "a safe starting point, not always the top performer". `varun` is a villain voice: never a default.
- Numbers over 4 digits need commas (`20,000`). End Hindi/Bengali sentences with `।`, English ones with `.`.

## What we built on top (the "[bracket]" idea)
The model starts each sentence with a tag such as `[warm] [excited] [playful] [surprised] [thinking] [soft] [calm] [firm]`.
Tags are never spoken. `delivery.mts` maps them to pace multiplier + temperature offset and `TtsStream.style()` sends a config
update before that sentence. Example: `[soft]` = pace ×0.90, temp −0.18; `[excited]` = pace ×1.12, temp +0.06.
Fillers and reactions ("হুম…", "अरे वाह!") are written by the model itself, at most one per reply.

## Turn-taking (Sarvam Pipecat production guide + Voice Agents "Conversation settings")
- Raise `silence_duration_ms` for speakers who pause mid-sentence; raise `min_speech_duration_ms` against coughs/echo.
- While the agent speaks, require ≥2 real words before yielding; "haan/achha" must not interrupt.
- Sarvam's own Voice Agents expose "Eagerness to respond: Patient / Normal / Eager" — our `patienceMs()` is the same idea.

## Not done yet / options
- Bengali speaker: try `rehan` (recommended for bn) against `shubh` — `npm run tune -- rehan`.
- Pronunciation dictionary for names (Sayan, Shantanu, Soumen, Shubham) if any is mispronounced.
- A filler played instantly from cache ("হুম…") to cover thinking time.

## Sources
- TTS best practices: https://docs.sarvam.ai/api/api-guides-tutorials/text-to-speech/best-practices
- Speaker voices & CER table: https://docs.sarvam.ai/api/api-guides-tutorials/text-to-speech/how-to/change-the-speaker-voice
- Voices (audio previews): https://docs.sarvam.ai/api/api-guides-tutorials/text-to-speech/voices
- TTS overview / WebSocket protocol: https://docs.sarvam.ai/api/api-guides-tutorials/text-to-speech/overview , .../streaming-api
- Official TTS agent skill: https://github.com/sarvamai/skills/tree/main/text-to-speech
- Pronunciation dictionary: https://docs.sarvam.ai/api/api-guides-tutorials/text-to-speech/pronunciation-dictionary
- Voice Agents prompt practices: https://docs.sarvam.ai/conversations/build/single-state-agents
- Voice Agents voice + conversation settings: https://docs.sarvam.ai/conversations/build/voice-language , .../conversation-settings
- Pipecat production guide (VAD, turn-taking, TTS latency): https://docs.sarvam.ai/api/integration/pipecat-production-guide
- Changelog (models are current: saaras:v4, sarvam-105b, bulbul:v3): https://docs.sarvam.ai/changelog
