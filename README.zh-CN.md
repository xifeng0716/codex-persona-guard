# Persona Guard

[English](README.md) | 简体中文

Persona Guard 是一个小型本地服务和 Codex `UserPromptSubmit` Hook。它使用轻量级 DeepSeek 检测器判断下一条回复是否容易退回通用助手姿态。只有检测结果为 `HIT` 时才会追加已配置的提醒；`WATCH` 和 `HOT` 本身都不会注入提醒。

## 运行要求

- Python 3.10 或更高版本，仅使用标准库
- 本地已安装 Codex、已启用 Hook，并可使用 `/hooks`
- 启动服务的环境中存在 DeepSeek API Key

运行和测试均不依赖第三方 Python 软件包。

## 配置 DeepSeek

通用且推荐的环境变量是 `DEEPSEEK_API_KEY`：

```sh
export DEEPSEEK_API_KEY='your-key'
```

为了兼容已有的 GMEM 环境，Persona Guard 也接受 `GMEM_DEEPSEEK_API_KEY`。后端会优先读取它，再回退到 `DEEPSEEK_API_KEY`。Key 只从启动进程继承的环境变量中读取，不会写入仓库、数据库或日志。

`.env.example` 仅作为模板。`scripts/run-server` 不会自动加载 dotenv 文件，因此请在启动服务的同一个 Shell 中导出环境变量。

## 安装 Codex Hook

在仓库目录中运行：

```sh
./scripts/install-hook
```

安装器会把独立 Hook Client 复制到 `$HOME/.codex/persona-guard/hook_client.py`，并向 `$HOME/.codex/hooks.json` 安全合并一个 command handler。已有 Hook 会被保留。修改现有 Hook 配置前，安装器会先创建 `$HOME/.codex/hooks.json.persona-guard.bak`；如果该名称已存在，则创建带时间戳的同级备份。安装器可重复运行，不会重复添加处理器。

安装后请在 Codex 中通过 `/hooks` 检查并信任新命令。安装器不会替你修改 Codex 的信任状态。

## 启动服务并绑定目标

在已导出 API Key 的 Shell 中手动启动服务：

```sh
./scripts/run-server
```

服务只监听 `127.0.0.1:43821`。使用 Codex 时请保持它运行。

新线程或工作区的第一条消息只用于发现元数据：服务会记录 `session_id`、规范化后的 `cwd` 和最后出现时间；未绑定目标的消息正文不会持久化，也不会发送给 DeepSeek。

打开本地面板 `http://127.0.0.1:43821`，绑定已发现的线程或精确工作区，然后发送下一条消息。保护会从保存绑定后的下一轮开始。精确线程绑定优先于匹配的工作区绑定。

每个绑定都可以使用独立的 HIT Reminder，全局 Detector Policy 也可以在面板中修改。因此，项目内置的关系场景默认规则只是起点，并不限制你使用其他人格。

## 隐私、数据与备份

运行数据保存在本地 SQLite：

```text
$XDG_STATE_HOME/persona-guard/guard.db
```

如果未设置 `XDG_STATE_HOME`，则使用：

```text
$HOME/.local/state/persona-guard/guard.db
```

服务只监听本机地址，状态目录也仅允许当前用户访问。只有已启用且已绑定的目标会生成校准记录。记录包含检测器历史、当前提示词、Policy 快照、判断或错误、状态迁移和绑定快照，供本地校准使用。未绑定或已关闭目标的消息不会被记录，也不会发送给 DeepSeek。不再需要时，可以在面板中清除记录。

移动或删除数据库前，请先停止服务，并同时复制数据库及其 SQLite 边车文件（`-wal` 和 `-shm`）。Hook 安装器还会保留前文所述的 Hook 配置备份。

## Fail-soft 行为

已安装的 Hook Client 从标准输入读取一个 JSON 对象，并在 7 秒总预算内交给本地服务；Codex command timeout 为 8 秒。输入格式错误、服务未运行、超时、本地或 HTTP 错误、响应 JSON 非法时，Client 都会以状态码 0 静默退出，让 Codex 正常继续。

有效的服务 JSON 响应会原样转发。DeepSeek 检测失败时同样不会注入提醒，也不会改变现有 Guard State。单次 DeepSeek 请求的 timeout 为 6 秒，且不会重试。

## 测试

运行完整测试：

```sh
python3 -m unittest discover -s tests -v
```

安装器测试使用隔离的临时 `HOME`，不会写入真实用户目录。

## 卸载

移除 Persona Guard command 和已复制的 Client：

```sh
./scripts/uninstall-hook
```

卸载器只移除 Persona Guard 自己的 handler、Client 和相关字节码，保留所有无关 Hook，并且可以重复运行。它不会删除本地 SQLite 校准数据库。如需彻底删除数据，请在完成必要备份后，按照前文路径自行删除。

## 许可证

Persona Guard 使用 [MIT License](LICENSE) 发布。
