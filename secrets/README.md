Create `gemini_api_key.txt` in this directory containing only your own Gemini key.
The file is ignored by Git and excluded from the Docker build context.
Docker Compose mounts it as a file; this is not an encrypted secret vault.
Protect this folder with your OS account permissions. Never commit or share it.
Do not paste your key in chat, .env, screenshots, or issue reports.
