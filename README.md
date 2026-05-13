# Novel Bot

Telegram 小说转换机器人。发送 .txt 小说到 Telegram Bot，AI 自动分析章节结构、识别广告、去重清洗，调用 [kaf-cli](https://github.com/ystyle/kaf-cli) 转换为 epub/mobi/azw3 格式并返回，设置邮箱后可直接发送到Kindle

## 技术栈

| 层 | 技术 |
|---|---|
| Bot 框架 | [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot) v22 (ConversationHandler) |
| AI 需求分析 | DeepSeek Chat API |
| 电子书转换 | [kaf-cli](https://github.com/ystyle/kaf-cli) v1.3.15 ([repo](https://github.com/ystyle/kaf-cli)) |
| 广告模式匹配 | Aho-Corasick 自动机 ([pyahocorasick](https://github.com/WojciechMula/pyahocorasick)) |
| 持久化 | SQLite (多用户会话保持) |
| 邮件发送 | SMTP (QQ/163/Gmail) / Outlook Graph API (OAuth 2.0) |
| 运行环境 | Python 3.12+, Docker |

## 功能

- **智能扫描**: 自动识别章节格式、包裹字符、重复章节、广告内容
- **AI 分析**: 用户用自然语言描述需求，DeepSeek 解析为清洗配方（如"去===，转mobi"）
- **分级广告清理**: AC 自动机全文扫描 HTTP 链接/QQ 群/微信/推广用语等 7 类广告，L1-L3 分级决策
- **卷感知去重**: 中文/阿拉伯数字卷号识别，跨卷同号章节不误判为重复
- **邮件投递**: 转换后发送到用户邮箱，支持 SMTP 和 Outlook OAuth
- **可撤销清洗**: 删除广告后支持按行号或全局撤销

## 快速开始

### 1. 配置

复制 `config.example.yaml` 为 `config.yaml`，填写必要信息：

```yaml
telegram:
  token: "YOUR_BOT_TOKEN"

deepseek:
  api_key: "YOUR_DEEPSEEK_API_KEY"
  model: deepseek-chat
  base_url: https://api.deepseek.com

kaf_cli:
  path: ./bin/kaf-cli

# 邮件（可选）
mail:
  provider: smtp              # smtp | outlook
  smtp:
    smtp_server: smtp.qq.com
    smtp_port: 587
    sender: your@qq.com
    password: 授权码
```

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

### 3. 准备 kaf-cli

- **Windows**: 从 [kaf-cli Releases](https://github.com/ystyle/kaf-cli/releases) 下载 `kaf-cli_v*_windows_amd64.zip`，解压到任意目录，通过环境变量 `NOVEL_KAF_CLI` 指定路径
- **Linux / Docker**: 下载 `kaf-cli_v*_linux_amd64.zip`，放入 `bin/kaf-cli`

### 4. 运行

**本地**:

```powershell
# Windows
$env:NOVEL_KAF_CLI = "C:\path\to\kaf-cli.exe"
python -m bot.main
```

```bash
# Linux
export NOVEL_KAF_CLI=/path/to/kaf-cli
python -m bot.main
```

**Docker**:

```bash
docker-compose up -d
```

发布到飞牛OS等 NAS 时，需在 `docker-compose.yml` 中将 `config.yaml` 和 `data` 目录挂载为 volume。

## 使用说明

| 操作 | 说明 |
|---|---|
| 发送 .txt 文件 | Bot 自动扫描并展示结构预览（章节数、重复、广告等） |
| 回复需求文字 | AI 分析并执行转换，例如 `转epub`、`转mobi，作者一叶飘零`、`去除广告，转epub` |
| 再转个mobi | 转换完成后文件保留，可以继续换格式 |
| 发送到邮箱 | 转换后回复此文字即可发送（需先 `/setmail`） |

### 可用命令

| 命令 | 说明 |
|---|---|
| `/start` | 开始使用 |
| `/help` | 使用帮助 |
| `/cancel` | 取消当前操作 |
| `/done` | 完成并清除文件 |
| `/setmail you@mail.com` | 设置邮箱 |
| `/mail` | 发送最近转换的小说 |
| `/mymail` | 查看当前邮箱 |

### 交互示例

```
用户: [上传 我绑架了时间线.txt]
Bot:  📄 我绑架了时间线.txt | 828 章
      ⚠️ 34 处重复章节 | 包裹字符: ===..., ...===
      请描述你的处理需求...

用户: 转epub
Bot:  🔍 已缓存扫描: 828 章
      🤖 分析完成: 用户指定epub，存在===包裹字符默认清理
      🧹 去除包裹字符: 801 行
      🧹 去除重复章节: 32 个
      ⚙️ 转换 epub 中...
      ✅ 完成！生成 1 个文件
      [文件下载]

用户: 再转个mobi
Bot:  [生成 mobi]
```

## 邮件配置

### QQ 邮箱 (SMTP)

```yaml
mail:
  provider: smtp
  smtp:
    smtp_server: smtp.qq.com
    smtp_port: 587
    sender: your@qq.com
    password: YOUR_AUTH_CODE   # QQ邮箱设置→账户→POP3/SMTP→获取授权码
```

### 163 邮箱 (SMTP)

```yaml
mail:
  provider: smtp
  smtp:
    smtp_server: smtp.163.com
    smtp_port: 465
    sender: your@163.com
    password: YOUR_AUTH_CODE
```

### Outlook (OAuth 2.0)

2024 年 9 月起 Outlook 停用基础 SMTP AUTH，需使用 OAuth 2.0：

1. [Azure Portal](https://portal.azure.com) → 应用注册 → 新建
   - 支持的账户类型: "任何组织目录中的帐户和个人 Microsoft 帐户"
   - 重定向 URI: 移动和桌面应用程序 → `https://login.microsoftonline.com/common/oauth2/nativeclient`
   - 允许公共客户端流: 是
2. API 权限 → 添加 → Microsoft Graph → 委派权限 → `Mail.Send` + `offline_access`
3. 证书和密码 → 新建客户端密码

运行 `python setup_outlook.py` 获取 refresh_token，填入 `config.yaml`：

```yaml
mail:
  provider: outlook
  outlook:
    client_id: "..."
    tenant_id: "common"
    client_secret: "..."
    refresh_token: "..."
    sender: your@outlook.com
```

refresh_token 有效期 90 天，过期后重新运行 `setup_outlook.py` 即可。

## 项目结构

```
main/
├── bot/
│   ├── main.py              # 入口，ConversationHandler + SQLitePersistence
│   ├── handler.py           # Telegram 消息处理，ConversationHandler 状态机
│   ├── scanner.py           # 结构指纹扫描（章节/卷/广告/重复）
│   ├── ai.py                # DeepSeek API 需求解析
│   ├── pipeline.py          # 清洗 → kaf-cli 转换流程
│   ├── cleaner.py           # 文本清洗引擎（去包裹/去广告/去重）
│   ├── reporter.py          # 进度状态消息管理
│   ├── config.py            # YAML 配置加载
│   ├── ad_patterns.py       # 广告检测模式注册表（7类）
│   ├── patterns_engine.py   # Aho-Corasick 自动机 + 统计特征
│   ├── persistence.py       # SQLite 持久化（多用户）
│   ├── mailer.py            # 多 provider 邮件分发
│   └── mail_outlook.py      # Outlook OAuth + Graph API
├── bin/
│   └── kaf-cli              # 电子书转换二进制
├── config.yaml              # 配置文件
├── config.example.yaml      # 配置文件模板
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── setup_outlook.py         # Outlook OAuth 一次性配置脚本
```

## 环境变量

所有 `config.yaml` 中的敏感配置均支持环境变量覆盖：

| 变量 | 覆盖 |
|---|---|
| `NOVEL_BOT_TOKEN` | `telegram.token` |
| `NOVEL_DEEPSEEK_KEY` | `deepseek.api_key` |
| `NOVEL_DEEPSEEK_MODEL` | `deepseek.model` |
| `NOVEL_DEEPSEEK_BASE_URL` | `deepseek.base_url` |
| `NOVEL_KAF_CLI` | `kaf_cli.path` |

## 依赖

- [kaf-cli](https://github.com/ystyle/kaf-cli) — txt 转 epub/mobi/azw3 命令行工具，**木兰宽松许可证**

