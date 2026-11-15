## Voice input: Groq, about 1 second

- Fast mode now uses Groq first: speech recognition (Whisper large v3) takes 0.3-1 s even for a minute and a half of speech, and the text clean-up, spoken commands ("translate into English") and edits of selected text take 0.2-1 s. Gemini's free service turned out to slow down to 25-50 s at busy hours.
- Order of engines: Groq, then Gemini, then local Whisper with the LLM below. Nothing is lost if a service does not answer.
- "Tell him that...", "write to Masha that..." are kept as part of the dictated text and no longer taken as commands.
- A PC that uses "Claude on another computer" gets the Groq key from that computer, nothing to set up.
