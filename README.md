# transmymodels

One local OpenAI-compatible endpoint, many upstreams.

Import any number of OpenAI-compatible upstreams (BaseURL plus one or more
API keys). transmymodels auto-fetches each upstream's `/v1/models`, maps every
upstream model id to a clean lowercase routed name (date/build suffixes
stripped), and exposes them all behind a single `http://127.0.0.1:<port>/v1`
URL protected by its own local API key. Any OpenAI SDK that accepts a custom
`base_url` works against it unchanged.

```
your app  -->  http://127.0.0.1:8787/v1   (local key sk-tmm-...)
                     |
                     +-- deepseek-v4.1-flash          --> sophnet (Bearer sk-...)
                     +-- kimi-k3.1-pro               --> sophnet (weighted key pool)
                     +-- glm-5.3                      --> another upstream
                     +-- ...
```

## Features

- Multiple upstreams behind one local OpenAI-compatible endpoint.
- Auto-discovery: fetches `/v1/models` from each upstream on `add` and `refresh`.
- Per-upstream model mapping: upstream id -> routed name. Press Enter to accept
  the default (normalized lowercase, date/build suffixes stripped), or type a
  custom alias. Collisions get an automatic `-2`, `-3`, ... suffix.
- Multiple API keys per BaseURL with weighted random load balancing. Higher
  weight means more traffic lands on that key.
- Local key is auto-generated; upstream keys are never exposed to clients.
- Streaming (SSE) passes through unchanged.
- Config reloaded on every request, so `add` / `remove` / `refresh` take effect
  without restarting the server.

## Install

Requires Python 3.9 or newer.

```
cd transmymodels
pip install -e .
```

The `transmymodels` command lands in your Python `Scripts/` directory, which
should already be on your PATH.

## Quickstart

```
# (optional) show where the local endpoint and key live
transmymodels info

# import an upstream (interactive)
transmymodels add
#   Upstream nickname:        sophnet
#   Base URL:                 https://www.sophnet.com/api/open-apis/v1
#   API key(s):               pyrBBG...     (comma-separated for multiple keys)
#   weight for key #2:        3             (only asked when >1 key)
#   -> auto-fetches /v1/models
#   DeepSeek-V4.1-Flash-1017 -> routed name [deepseek-v4.1-flash]:  <-- Enter to accept

# repeat for as many upstreams as you want
transmymodels add

# list everything
transmymodels list

# start the local proxy
transmymodels serve
```

On first run a random local API key is generated and printed. Config lives at
`~/.transmymodels/config.json` (chmod 600 on Unix).

## Use it from any OpenAI client

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8787/v1",
    api_key="sk-tmm-...",   # the key printed by `transmymodels info`
)

print([m.id for m in client.models.list().data])

resp = client.chat.completions.create(
    model="deepseek-v4.1-flash",
    messages=[{"role": "user", "content": "hello"}],
    stream=True,
)
for chunk in resp:
    print(chunk.choices[0].delta.content or "", end="")
```

Or with curl:

```
curl http://127.0.0.1:8787/v1/chat/completions \
  -H "Authorization: Bearer sk-tmm-..." \
  -H "Content-Type: application/json" \
  -d '{"model":"deepseek-v4.1-flash","messages":[{"role":"user","content":"hi"}]}'
```

## Commands

| Command | What it does |
|---|---|
| `transmymodels add` | Interactively import a new upstream: nickname, BaseURL, one or more API keys (with optional weights), auto-fetch `/v1/models`, accept or override routed names. |
| `transmymodels list` | Show upstreams, key weights, model counts, and routed mappings. |
| `transmymodels refresh [name]` | Re-fetch model lists from all upstreams (or one by name). Preserves existing routed names; new models get normalized defaults. |
| `transmymodels remove <name>` | Delete an upstream and all its routed models. |
| `transmymodels serve` | Start the local OpenAI-compatible proxy. |
| `transmymodels info` | Print local Base URL and API key. |

## Model name normalization

When a model id is auto-imported, its default routed name is derived as:

1. Lowercase the whole id.
2. Drop every segment that looks like a date or build tag:
   - 4-digit MMDD dates, e.g. `1017`, `0813`, `0731`, `0902`.
   - 6-digit YYMMDD dates, e.g. `251228`, `260628`.
   - Build tags of two letters plus two digits, e.g. `cd01`, `ac03`, `cx01`.
3. Keep everything else (e.g. `global`, `preview`, `exp`, `vision`, `vl`,
   `27b`, `a10b`).

Examples:

| Upstream id | Routed name |
|---|---|
| `DeepSeek-V4.1-Flash-1017` | `deepseek-v4.1-flash` |
| `DeepSeek-V4.1-Flash-1017-global` | `deepseek-v4.1-flash-global` |
| `doubao-seed-2-1-turbo-260628` | `doubao-seed-2-1-turbo` |
| `GLM-5.3-ac03` | `glm-5.3` |

If two upstream ids normalize to the same routed name, the first one wins and
the later one gets `-2`, `-3`, and so on. You can always type a custom alias
during `add` to override the default.

## Multiple keys and weighted load balancing

A single upstream (one BaseURL) can hold many API keys. Each request picks one
key at random, weighted by its configured weight. A key with weight 3 is roughly
three times as likely to be chosen as a key with weight 1.

During `add`, enter multiple keys comma-separated:

```
API key(s): key-aaaa,key-bbbb,key-cccc
weight for key #2: 3
weight for key #3: 1
```

You can also edit the config file directly.

### Key circuit breaker

If a chosen key receives a 401 or 403 from the upstream, that key is marked
disabled and the same request is immediately retried on another enabled key
(automatic failover). Rate limits (429) and upstream outages (5xx) do not
disable a key.

A background task re-enables all disabled keys every hour so they are retried
automatically. The `/health` endpoint reports the current disabled key count:

```
GET /health
{"ok": true, "upstreams": 1, "disabled_keys": 0}
```

## Config file

`~/.transmymodels/config.json`:

```json
{
  "local": {
    "host": "127.0.0.1",
    "port": 8787,
    "api_key": "sk-tmm-..."
  },
  "upstreams": [
    {
      "name": "sophnet",
      "base_url": "https://www.sophnet.com/api/open-apis/v1",
      "api_keys": [
        { "key": "pyrBBG...", "weight": 1 },
        { "key": "other-key...", "weight": 3 }
      ],
      "models": [
        { "upstream_model": "DeepSeek-V4.1-Flash-1017", "routed_name": "deepseek-v4.1-flash" }
      ]
    }
  ]
}
```

Older configs that used a single `api_key` string are still accepted and
treated as a single key with weight 1.

## Endpoints exposed locally

- `GET  /v1/models` - every routed model
- `POST /v1/chat/completions` - chat (streaming supported)
- `POST /v1/completions` - legacy completions
- `POST /v1/embeddings` - embeddings
- `POST /v1/responses` - OpenAI Responses API passthrough
- `GET  /health` - liveness

All require `Authorization: Bearer <local-key>`.

## Boot autostart (Windows)

To have the proxy launch in a visible terminal on login, drop a shortcut into
the Startup folder (`shell:startup`) that runs:

```
cmd.exe /k "C:\path\to\transmymodels.exe serve"
```

## License

MIT.
