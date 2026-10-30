# transmymodels

一个本地 OpenAI 兼容端点，背后挂多个上游。

导入任意数量的 OpenAI 兼容上游（BaseURL 加一个或多个 API Key）。transmymodels
会自动拉取每个上游的 `/v1/models`，把每个上游模型 id 映射成干净的小写路由名
（日期/构建后缀自动砍掉），然后统一暴露在 `http://127.0.0.1:<端口>/v1`，用一个
本地 API Key 保护。任何支持自定义 `base_url` 的 OpenAI SDK 都能直接接入。

```
你的应用  -->  http://127.0.0.1:8787/v1   (本地 key sk-tmm-...)
                   |
                   +-- deepseek-v4.1-flash     --> sophnet (Bearer sk-...)
                   +-- kimi-k3.1-pro          --> sophnet (加权 Key 池)
                   +-- glm-5.3                --> 另一个上游
                   +-- ...
```

## 特性

- 多个上游统一在一个本地 OpenAI 兼容端点后面。
- 自动发现：`add` 和 `refresh` 时自动拉取上游 `/v1/models`。
- 按上游做模型映射：上游 id -> 路由名。回车接受默认值（归一化小写，日期后缀砍掉），
  也可手输自定义别名。撞名自动加 `-2`、`-3`。
- 一个 BaseURL 支持多个 API Key，按权重随机落点。权重越高，落到该 Key 的请求越多。
- 本地 Key 自动生成；上游 Key 永远不暴露给客户端。
- 流式（SSE）原样透传。
- 每个请求都重读配置，`add` / `remove` / `refresh` 不用重启服务即生效。

## 安装

需要 Python 3.9 或更高版本。

```
cd transmymodels
pip install -e .
```

`transmymodels` 命令会装进 Python 的 `Scripts/` 目录，该目录一般已在 PATH 里。

## 快速开始

```
# (可选) 查看本地端点和 Key
transmymodels info

# 交互式导入一个上游
transmymodels add
#   Upstream nickname:        sophnet
#   Base URL:                 https://www.sophnet.com/api/open-apis/v1
#   API key(s):               pyrBBG...     (多个 Key 用逗号分隔)
#   weight for key #2:        3             (多于 1 个 Key 时才会问)
#   -> 自动拉取 /v1/models
#   DeepSeek-V4.1-Flash-1017 -> routed name [deepseek-v4.1-flash]:  <-- 回车接受

# 想加几个上游就重复几次
transmymodels add

# 列出全部
transmymodels list

# 启动本地代理
transmymodels serve
```

首次运行会自动生成一个随机本地 API Key 并打印。配置文件在
`~/.transmymodels/config.json`（Unix 下权限 600）。

## 在任意 OpenAI 客户端里使用

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8787/v1",
    api_key="sk-tmm-...",   # `transmymodels info` 打印的那个
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

curl 方式：

```
curl http://127.0.0.1:8787/v1/chat/completions ^
  -H "Authorization: Bearer sk-tmm-..." ^
  -H "Content-Type: application/json" ^
  -d "{\"model\":\"deepseek-v4.1-flash\",\"messages\":[{\"role\":\"user\",\"content\":\"hi\"}]}"
```

## 命令

| 命令 | 作用 |
|---|---|
| `transmymodels add` | 交互式导入新上游：昵称、BaseURL、一个或多个 API Key（可选权重），自动拉 `/v1/models`，接受或修改路由名。 |
| `transmymodels list` | 显示上游、Key 权重、模型数量和路由映射。 |
| `transmymodels refresh [name]` | 重新拉取所有上游的模型列表（或指定某个）。保留已有路由名；新模型用归一化默认名。 |
| `transmymodels remove <name>` | 删除一个上游及其所有路由模型。 |
| `transmymodels serve` | 启动本地 OpenAI 兼容代理。 |
| `transmymodels info` | 打印本地 Base URL 和 API Key。 |

## 模型名归一化

自动导入模型时，默认路由名按以下规则生成：

1. 整体转小写。
2. 砍掉所有看起来像日期或构建号的段：
   - 4 位 MMDD 日期，如 `1017`、`0813`、`0731`、`0902`。
   - 6 位 YYMMDD 日期，如 `251228`、`260628`。
   - 两个字母加两位数字的构建号，如 `cd01`、`ac03`、`cx01`。
3. 其他段保留（如 `global`、`preview`、`exp`、`vision`、`vl`、`27b`、`a10b`）。

示例：

| 上游 id | 路由名 |
|---|---|
| `DeepSeek-V4.1-Flash-1017` | `deepseek-v4.1-flash` |
| `DeepSeek-V4.1-Flash-1017-global` | `deepseek-v4.1-flash-global` |
| `doubao-seed-2-1-turbo-260628` | `doubao-seed-2-1-turbo` |
| `GLM-5.3-ac03` | `glm-5.3` |

如果两个上游 id 归一化后撞名，先到先得，后来的自动加 `-2`、`-3`。
`add` 时随时可以手输自定义别名覆盖默认值。

## 多 Key 与加权负载均衡

一个上游（一个 BaseURL）可以挂多个 API Key。每个请求按权重随机选一个 Key 发出。
权重为 3 的 Key 被选中的概率大约是权重为 1 的三倍。

`add` 时多个 Key 用逗号分隔输入：

```
API key(s): key-aaaa,key-bbbb,key-cccc
weight for key #2: 3
weight for key #3: 1
```

也可以直接编辑配置文件。

## 配置文件

`~/.transmymodels/config.json`：

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

旧版单字段 `api_key` 字符串配置仍兼容，会被当作权重 1 的单个 Key。

## 本地暴露的端点

- `GET  /v1/models` - 所有路由模型
- `POST /v1/chat/completions` - 对话（支持流式）
- `POST /v1/completions` - 旧式补全
- `POST /v1/embeddings` - 向量
- `POST /v1/responses` - OpenAI Responses API 透传
- `GET  /health` - 存活检查

全部要求 `Authorization: Bearer <本地 Key>`。

## Windows 开机自启

要让代理登录时在可见终端里启动，在启动文件夹（`shell:startup`）放一个快捷方式，
目标为：

```
cmd.exe /k "C:\path\to\transmymodels.exe serve"
```

## 许可证

MIT。
