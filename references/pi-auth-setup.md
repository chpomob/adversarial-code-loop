# Pi Auth File Setup

`pi` stores API keys in `~/.pi/agent/auth.json`. Format:

```json
{
  "deepseek": { "type": "api_key", "key": "sk-your-key-here" },
  "openai":   { "type": "api_key", "key": "sk-your-key-here" }
}
```

File must have permissions `0600` (user read/write only). Auth file credentials take priority over environment variables.

## Provider-to-key mapping

| Provider | `auth.json` key | Environment variable |
|----------|----------------|---------------------|
| Anthropic | `anthropic` | `ANTHROPIC_API_KEY` |
| OpenAI | `openai` | `OPENAI_API_KEY` |
| DeepSeek | `deepseek` | `DEEPSEEK_API_KEY` |
| Google Gemini | `google` | `GEMINI_API_KEY` |

## Setup script

```python
import os, json

auth_dir = os.path.expanduser("~/.pi/agent")
os.makedirs(auth_dir, mode=0o700, exist_ok=True)

auth = {
    "deepseek": {
        "type": "api_key",
        "key": "sk-your-deepseek-key"
    }
}

with open(os.path.join(auth_dir, "auth.json"), "w") as f:
    json.dump(auth, f, indent=2)
os.chmod(os.path.join(auth_dir, "auth.json"), 0o600)
```

## Reading existing keys from Hermes

Hermes stores credentials in `~/.hermes/auth.json` (credential_pool) and `~/.hermes/.env` (plain env).
Pi reads the same keys from its own auth file — there is no shared credential store.
Create the pi auth file manually when switching from Hermes-native to pi-based CLI commands.

## Extracting the DeepSeek key from Hermes (automated)

Hermes masks credentials in terminal output (`***`) but the actual `.env` file contains the real key.
Read it with Python:

```python
key = None
with open("/home/chpo/.hermes/.env") as f:
    for line in f:
        if "DEEPSEEK_API_KEY" in line and "=" in line:
            val = line.strip().split("=", 1)[1]
            if val and val != "***":
                key = val
                break
```

Then write to `~/.pi/agent/auth.json` with `0600` permissions (see setup script above).
Validated on this host 2026-07-06: the key is 35 chars, starts with `sk-`.
