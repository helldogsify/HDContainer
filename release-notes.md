## Voice input: no resident local Whisper

- When fast mode has a Groq or Gemini key, the local Whisper server no longer starts with HDContainer and no longer stays in memory (it held about 1 GB of RAM). It starts only if both cloud services fail, for that one phrase, and unloads after a minute of idle time.
- Without a cloud key nothing changes: "Keep the model loaded" works as before.
