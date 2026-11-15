## Voice input: fast mode (Gemini)

- Dictation is several times faster: Gemini hears the recording and returns the finished text in about 1.5 seconds (it was 8-15 seconds with Whisper plus Claude).
- Spoken commands ("translate into English", "make it more formal") and edits of selected text still go to Claude, now with Gemini's exact transcript.
- If Gemini does not answer (no internet, quota), the local Whisper and the LLM work as before.
- A PC that uses "Claude on another computer" gets the Gemini key from that computer, nothing to set up.
- Settings -> Voice input -> Speech recognition: "Fast mode: Gemini" switch, key and model. The voice goes to Google in this mode.
