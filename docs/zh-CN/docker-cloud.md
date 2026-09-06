# Docker Compose 云游戏部署

该部署方式在 Linux 容器中打开[云·鸣潮](https://mc.kurogames.com/cloud/)，通过持久化浏览器会话恢复登录，并提供常驻的浏览器管理页面。管理页面复用项目和 ok-script 的任务元数据，可配置一次性任务、后台任务、角色代码与计划任务；任务执行完毕后直接关闭云游戏浏览器窗口。

> 此功能仅用于网页云游戏，不会把 Windows 本地游戏客户端装入容器。自动化工具存在账号风险，使用前请阅读项目免责声明。

## 已验证范围

当前实现已在 Docker、Xvfb 和 noVNC 环境中完成真实端到端验证，包括：

- 首次手动登录并持久化浏览器会话；
- 恢复登录、选择普通队列、等待并进入游戏；
- 以固定的 1280×720 游戏视口执行完整每日任务；
- 指南页导航、挑战、战斗、体力奖励、活跃度奖励、邮件、先约电台和周常检查；
- 任务完成或异常后直接关闭浏览器，不操作游戏内退出按钮；
- 通过统一计划任务管理每日与其他周期任务，以及登录会话失效时发送中文 SMTP 邮件；
- 常驻 Web 管理页面中的任务配置、角色代码和计划任务配置。

验证码、账号验证、安全验证和其他敏感页面始终需要人工处理。

## 部署要求

- Docker Engine 与 Docker Compose v2，或启用了 Linux 容器的 Docker Desktop；
- `linux/amd64`（x86-64）运行环境；
- 能访问云游戏官网、Google Chrome 软件源和 Python 依赖源；
- 至少允许容器使用 1 GiB 共享内存，默认由 `CLOUD_SHM_SIZE=1gb` 配置；这不是容器总内存需求，当前尚未测得可靠的最低 CPU、RAM 和磁盘规格；
- 本机 TCP 端口 `17880` 可用，用于管理页面；
- 本机 TCP 端口 `15980` 可用，用于首次登录和 noVNC 排障；
- 首次登录时有可访问 `http://127.0.0.1:15980/vnc.html` 的浏览器。

若使用 Docker Desktop，请启用 Linux 容器并允许它随系统启动。若 Docker Engine 直接运行在 WSL 中，命令应在对应 WSL shell 内执行，并且必须保证 WSL 发行版和 Docker daemon 持续运行。`restart: unless-stopped` 只能在 Docker daemon 存活或重新启动后恢复容器，不能阻止整个 WSL 发行版退出。

## Compose 文件

- `compose.yaml`：常驻管理服务、浏览器、持久卷、计划任务和 noVNC。
- `compose.smtp.yaml`：可选 SMTP secret 挂载；启用邮件时与主文件叠加。
- `.env.cloud.example`：可提交的配置模板。
- `.env.cloud`：本机实际配置，已被 Git 忽略。
- `secrets/smtp_password`：SMTP 授权码或密码，已被 Git 和 Docker 构建上下文忽略。

浏览器登录态、Cookie、localStorage、任务配置和调度状态保存在 Compose 逻辑卷 `cloud-data` 的 `/data/cloud` 下，不会写入镜像。Docker 实际卷名通常带有 Compose 项目前缀；移动或重命名仓库、修改 `--project-name` 可能创建新卷，使程序表现为登录态丢失。

## 1. 创建本地配置

在仓库根目录执行：

```powershell
Copy-Item -LiteralPath '.env.cloud.example' -Destination '.env.cloud'
```

新部署保持自动模式：

```dotenv
CLOUD_COMMAND=auto
CLOUD_RESTART_POLICY=unless-stopped
CLOUD_ENABLE_NOVNC=1
CLOUD_NOVNC_PORT=15980
CLOUD_NOVNC_INTERNAL_PORT=15980
```

## 2. 首次手动登录

```powershell
docker compose --env-file .env.cloud up --build
```

打开 <http://127.0.0.1:15980/vnc.html>，在容器浏览器中完成人工登录。自动模式在没有有效注册标记时进入注册流程；程序识别到已认证页面后会保存标记并正常退出，`unless-stopped` 随即重启容器并自动切换到常驻管理页面。完整浏览器 profile 会继续保存在 `cloud-data` 卷中，无需修改环境变量。

持久化的不只是 Cookie，因此不要只复制 Cookie 文件代替首次初始化。

## 3. 单次完整验证

保持 `.env.cloud`：

```dotenv
CLOUD_COMMAND=run-once
CLOUD_RESTART_POLICY=no
```

然后运行：

```powershell
docker compose --env-file .env.cloud up
```

程序会恢复登录、排队进入游戏、执行一次完整每日任务并关闭浏览器。成功时容器退出码为 `0`。

## 4. 启动常驻管理页面

编辑 `.env.cloud`：

```dotenv
CLOUD_COMMAND=auto
CLOUD_RESTART_POLICY=unless-stopped
CLOUD_WEB_HOST=0.0.0.0
CLOUD_WEB_PORT=8765
CLOUD_WEB_HOST_PORT=17880
CLOUD_NOVNC_PORT=15980
CLOUD_NOVNC_INTERNAL_PORT=15980
```

后台启动：

```powershell
docker compose --env-file .env.cloud up --build -d
```

启动后打开 <http://127.0.0.1:17880>。页面支持：

- 查看并修改一次性任务和后台任务的配置；
- 手动启动、暂停、恢复和停止任务；
- 配置项目的角色代码；
- 创建、编辑、启用、停用和删除计划任务；
- 查看运行状态和日志。

管理页面首次访问默认使用简体中文；用户在设置页手动选择的语言会保存在浏览器中，后续访问不会被默认值覆盖。需要改变首次访问默认语言时，可设置 `OK_WW_WEB_DEFAULT_LANGUAGE`。

首次启动会自动创建一个已启用的 `DailyTask` 计划，默认每天 `04:00` 按 `Asia/Shanghai` 时区执行，允许在计划时间后 120 分钟内补跑。它与其他任务使用同一套计划任务机制，可直接在页面的“计划任务”中修改或删除，不再由单独的 DailyTask 环境变量管理。

任务设置、角色代码和计划数据持久化到 `/data/cloud`。上游增加任务或配置项后，Web 页面会从注册表与任务元数据读取新内容，Docker 层无需复制一套任务表单，因此更容易同步上游更新。

首次登录完成后，如果不需要随时查看画面，可将 `CLOUD_ENABLE_NOVNC=0` 后重新创建容器；管理页面不受影响，需要排障时再临时开启。

旧的 `CLOUD_COMMAND=schedule`、`RUN_AT`、`TZ` 与 `MISSED_WINDOW_MINUTES` 仍保留用于兼容或诊断。仅在旧 `schedule` 模式下，调度器才直接读取这些环境变量并每天启动一个全新的 `run-once` 子进程。新部署应使用 `CLOUD_COMMAND=auto`，并在页面中统一管理所有计划。

## 5. 登录失效邮件

邮件通知仅在已保存的登录会话无法恢复时发送，不覆盖网页异常、排队超时或普通任务失败。

在 `.env.cloud` 中填写非敏感配置。465 隐式 SSL 示例：

```dotenv
OK_WW_SMTP_HOST=smtp.example.com
OK_WW_SMTP_PORT=465
OK_WW_SMTP_USERNAME=sender@example.com
OK_WW_SMTP_SENDER=sender@example.com
OK_WW_SMTP_RECIPIENT=owner@example.com
OK_WW_SMTP_USE_TLS=false
OK_WW_SMTP_USE_SSL=true
OK_WW_SMTP_PASSWORD_SOURCE=./secrets/smtp_password
```

如果服务商使用 587 STARTTLS，则设置：

```dotenv
OK_WW_SMTP_PORT=587
OK_WW_SMTP_USE_TLS=true
OK_WW_SMTP_USE_SSL=false
```

创建 `secrets/smtp_password`，文件中只放 SMTP 授权码或密码。Compose secret 在单机部署中是只读文件挂载，不会自动加密宿主机文件；请限制该文件的系统访问权限并排除在普通备份之外。启用邮件时使用两个 Compose 文件：

```powershell
docker compose --env-file .env.cloud `
  -f compose.yaml -f compose.smtp.yaml up --build -d
```

不要把密码直接写入 `.env.cloud`、Compose 文件、镜像或提交记录。

## 运维命令

未启用 SMTP 时：

```powershell
# 查看状态
docker compose --env-file .env.cloud ps

# 跟踪日志
docker compose --env-file .env.cloud logs -f cloud-runner

# 检查 Web 管理服务健康状态
Invoke-RestMethod -Uri 'http://127.0.0.1:17880/healthz'

# 应用配置或代码更新
docker compose --env-file .env.cloud up --build -d

# 停止并移除容器，保留登录数据卷
docker compose --env-file .env.cloud down
```

启用 SMTP 后，运维命令也应始终叠加相同的配置文件，例如：

```powershell
docker compose --env-file .env.cloud `
  -f compose.yaml -f compose.smtp.yaml logs -f cloud-runner
```

不要执行 `docker compose down -v`，否则 `cloud-data` 卷会被删除，需要重新登录并重新建立任务配置。

## 安全说明

- 管理页面和 noVNC 默认只绑定到 `127.0.0.1`。不要直接映射到公网；远程使用时应在前面增加带认证的 TLS 代理。
- `cloud-data` 包含登录会话和任务配置，应按敏感数据保护，不要提交或打包进镜像。
- 容器保持 `no-new-privileges`，Chrome 在容器中使用 `--no-sandbox`，同时通过应用模式隐藏浏览器工具栏和相关提示。
- 普通队列是默认值。只有明确接受可能产生的额外费用时，才把 `OK_WW_CLOUD_QUEUE` 改为 `fast`。
- Compose 在 `serve` 模式下每 30 秒请求 `/healthz`，并将 Docker `json-file` 日志限制为默认每个 10 MiB、最多 5 个文件。可通过 `CLOUD_LOG_MAX_SIZE` 和 `CLOUD_LOG_MAX_FILES` 调整。

远程 Linux 服务器首次登录时，不要公开 noVNC 端口。可以从本机建立 SSH 隧道：

```sh
ssh -L 17880:127.0.0.1:17880 -L 15980:127.0.0.1:15980 user@server
```

然后在本机访问 <http://127.0.0.1:17880> 管理页面，或访问 <http://127.0.0.1:15980/vnc.html> 查看 noVNC。

## 常见问题

### 容器运行但任务没有按时开始

检查 Docker daemon、WSL/Docker Desktop 是否持续运行，并在管理页面确认计划是否启用、时区和时间是否正确。计划数据保存在 `/data/cloud/schedules.json`。

### 登录已失效

将 `CLOUD_COMMAND` 临时改回 `enroll`、`CLOUD_RESTART_POLICY` 改为 `no`，重新打开 noVNC 完成人工登录，然后恢复 `serve`。

### 浏览器画面位置偏移

不要修改默认 `CLOUD_SCREEN=1280x720x24`。容器浏览器使用应用模式和固定 1280×720 视口，网页工具栏、翻译浮窗或恢复提示不应占用游戏区域。

开发和页面识别细节参见[云游戏开发与验证记录](../cloud-development.md)。
