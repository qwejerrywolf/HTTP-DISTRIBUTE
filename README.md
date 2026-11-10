# Windows HTTP 文件分发与管理系统

面向 Windows 11 / NTFS 的 Python 文件分发服务。单入口 `python main.py` 启动两个独立 FastAPI 应用：公网端只接受授权下载/目录浏览，管理端默认仅回环访问，也可绑定 Tailscale IP。前端为原生 HTML/CSS/JavaScript，生产运行不需要 Node、Docker、Redis 或外部数据库。

**文件夹仅允许在线浏览和逐个下载内部文件，不提供整体下载、批量下载、ZIP 或自动打包。**

## 1. 安装与首次启动

安装官方 64 位 Python 3.12 或更新版本。本项目实际验证环境为 Python 3.12.14、Windows、NTFS。建议为服务使用专门的普通 Windows 用户，仅授予所需分发目录权限。

在 PowerShell 中执行：

```powershell
cd F:\Programs\HTTPDISTRIBUTE
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item config.example.json config.json
.\.venv\Scripts\python.exe main.py
```

也可以先激活虚拟环境，再执行唯一启动入口：

```powershell
.\.venv\Scripts\Activate.ps1
python main.py
```

若 PowerShell 不允许激活脚本，直接使用虚拟环境内的 `python.exe`，不必关闭系统执行策略。

第一次启动必须在交互终端输入管理员用户名及两次密码，密码至少 12 个字符，输入不回显；没有默认密码，没有公网或浏览器初始化入口。数据库仅保存 Argon2id 密码哈希。后续启动不会重复初始化。不要将管理员密码写入命令行、配置文件或环境变量。

- 管理界面：[http://127.0.0.1:18081](http://127.0.0.1:18081)
- 默认下载监听：`0.0.0.0:18080`
- 公网端根路径 `/` 返回 404，这是预期行为，下载必须持有分享 URL。
- `Ctrl+C` 或 `Ctrl+Break` 发起优雅关闭，给予下载最多 30 秒完成，取消后台文件任务并清理暂存文件。

源文件请放在独立目录，例如 `D:\Delivery`。**程序目录及数据目录受到保护，不能作为分享内容或文件操作目标**。不用以管理员身份运行主程序。

## 2. 配置

复制 `config.example.json` 为 `config.json` 后修改。配置格式为 JSON；`.env.example` 仅解释环境变量，不会自动加载 `.env`。

| 配置 | 默认值 | 说明 |
|---|---|---|
| `download_host` | `0.0.0.0` | 下载监听；IPv6 可设为 `::`，双栈行为取决于 Windows 网络栈 |
| `download_port` | `18080` | 下载端口 |
| `admin_host` | `127.0.0.1` | 默认仅允许回环或 Tailscale 地址；开启下方开关后可填写通配/局域网/本机公网 IP |
| `admin_port` | `18081` | 管理端口，必须与下载端口不同 |
| `allow_public_admin` | `false` | 设为 JSON 布尔值 `true`，允许管理服务绑定其他本机 IP 或 `0.0.0.0` / `::`；不取消登录或 CSRF |
| `public_url` | `http://localhost:18080` | 买家下载根地址，部署后必须改成买家可访问地址 |
| `cookie_secure` | `false` | 本机 HTTP 为 false；管理端经 HTTPS 访问时设为 true |
| `data_dir` | `data` | 数据目录，相对当前工作目录；服务部署建议使用绝对路径 |
| `max_downloads` | `64` | 文件请求并发上限，超限返回 503 + Retry-After |
| `session_hours` | `12` | Session 固定有效小时数，最大 168 |
| `history_days` | `90` | 请求及已完成文件任务保留天数，每小时清理 |

`HTTPD_CONFIG` 可指定配置文件绝对路径，`HTTPD_DATA` 可覆盖数据目录。这两个环境变量不包含凭据。

管理界面“系统设置”可立即修改公网下载根地址。该值保存在数据库，优先于配置文件中的 `public_url`；已有令牌保持不变，控制台按新域名生成 URL。监听、Cookie、并发等参数修改后需重启。

也可直接在“下载链接”页顶部修改**复制链接默认前缀**，或在创建成功窗口点“修改默认前缀”。支持域名、IPv4、`[IPv6]` 及端口；省略协议时使用 `http://`，HTTPS 需明确填写。所有复制按钮读取最新前缀，保存后重启仍保留，不改变分享令牌或本机监听地址。文件管理器、链接列表和买家目录页提供文本、代码、图片、音频、视频、PDF、Office 文档及压缩文件分类图标，未知格式使用普通文件图标。

默认不开放公网管理。若需要管理服务同时接受局域网、Tailscale 或公网接口连接，可在实际 `config.json` 中设置：

```json
{
  "admin_host": "0.0.0.0",
  "admin_port": 18081,
  "allow_public_admin": true
}
```

将这些字段合并到现有配置，保留其他设置，然后重启 `python main.py`。`0.0.0.0` 表示监听所有本机 IPv4 网卡，浏览器使用 `http://服务器的局域网或Tailscale-IP:18081/`，不要把 `0.0.0.0` 当作远端地址。IPv6 监听可设为 `::`。填写具体 IP 时该地址必须属于服务器本机。

该开关只改变允许的监听地址，不修改 NAT 转发、Windows 防火墙或 Tailscale 策略，也不会自动重启服务。HTTP 访问保持 `cookie_secure=false`；经 HTTPS 管理时设为 `true`。关闭开关时需要同时将 `admin_host` 改回回环或 Tailscale IP。两个端口仍拥有独立路由表，下载端不会因此出现管理 API。

## 3. Tailscale 管理

### 推荐：Tailscale Serve + HTTPS

保持 `admin_host=127.0.0.1`，将 `cookie_secure` 改为 `true`，重启服务。在已登录 tailnet 的主机执行：

```powershell
tailscale serve --bg http://127.0.0.1:18081
tailscale serve status
```

使用命令输出的 `https://机器名.tailnet.ts.net` 地址管理；Secure Cookie 启用后应使用该 HTTPS 地址。Tailscale Serve 只面向 tailnet，**不要对管理端使用 Funnel**。在 Tailscale ACL/grants 中只允许管理员设备访问此服务。应用自身的用户名密码验证仍然必需。

参考：[Tailscale Serve 官方文档](https://tailscale.com/docs/reference/tailscale-cli/serve)。

### 直接绑定 Tailscale IP

```powershell
tailscale ip -4
```

将输出地址（例如 `100.80.90.100`）填入 `admin_host`，然后通过 `http://100.80.90.100:18081` 访问。HTTP 模式下 `cookie_secure=false`；传输由 Tailscale 隧道加密。如果需要监听 `0.0.0.0` 或普通局域网地址，必须同时设置 `allow_public_admin=true`。100.64.0.0/10 和 Tailscale IPv6 范围只是绑定地址校验，访问控制仍由 tailnet 策略和 Windows 防火墙负责。

## 4. 公网 HTTPS、路由器与防火墙

建议 DNS 指向家庭公网 IP，由 Caddy 在本机终止 TLS 并反向代理下载端。不要代理管理端。可参考 `deploy/Caddyfile.example`：

```caddyfile
download.example.com {
    reverse_proxy 127.0.0.1:18080
}
```

该部署可将 `download_host` 改为 `127.0.0.1`，只对外开放 80/443，并在系统设置填写 `https://download.example.com`。如果使用非标准 HTTPS 端口，公网 URL 也要包含该端口。代理不要启用文件压缩转换、整响应缓冲、令牌 URL 访问日志或短响应超时；保留 Range、If-Range、ETag、Last-Modified 等头。Caddy 示例未启用 access log。

参考：[Caddy reverse_proxy 官方文档](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy)。

Windows 防火墙规则示例（仅配置防火墙这一步需要管理员 PowerShell，主服务不需要）：

```powershell
# 直接公开下载端时使用；反代模式无需开放 18080。
New-NetFirewallRule -DisplayName "HTTP 文件分发" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 18080

# Caddy HTTPS 模式按实际安装路径限制程序更佳。
New-NetFirewallRule -DisplayName "文件分发 HTTPS" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 80,443
```

不要同时照抄两种部署方案；选择实际使用的端口。在路由器只转发下载所需端口，绝不转发 18081。IPv6 不依赖 IPv4 NAT 转发，也必须检查入站防火墙。若运营商使用 CGNAT，需要公网接入方案，本项目不能自动穿透 CGNAT。

应用故意不信任 `X-Forwarded-For`：直接连接显示真实远端 IP，经反代则显示代理 IP，避免公网伪造头污染监控。此版本未实现可信代理 IP 配置。

## 5. 文件管理器

登录后点击磁盘卡片或左侧树进入目录。树可展开子目录并按需加载；双击文件夹进入，路径栏支持粘贴，面包屑和向上按钮支持返回。列表可按名称/类型/大小/时间排序、搜索当前目录，每页最多 100 项。

- 单击选择，Ctrl 多选，Shift 范围选择。
- 右键可打开目录、复制、剪切、粘贴、重命名、新建目录、复制路径、查看属性和创建分享。
- 复制/剪切状态由网页维护。进入目标目录后点击粘贴。
- 后台文件任务按序执行，分块复制，显示字节进度，支持取消排队或复制阶段的任务。
- 文件任务执行期间仍可浏览、查看监控和撤销授权；创建新分享会快速提示磁盘操作忙，完成/取消任务后重试，避免创建授权与移动提交发生竞争。
- 目录属性可提交后台大小统计，结果显示在任务列表。
- 冲突可选择取消、自动重命名、覆盖同名普通文件；不覆盖目录，以避免隐式删除整个目录。
- 同卷移动采用文件系统重命名；跨卷先复制至目标侧独立暂存，再提交目标，最后移除源对象。取消发生在提交前；已经进入提交/清理阶段的操作不会回滚。
- 上传：打开目标目录后点“上传文件”，可一次选一个或多个文件，逐个上传并显示每个文件的进度/结果；可取消当前和剩余上传。同名可选择跳过、自动重命名或覆盖普通文件，关联分享的文件不允许覆盖。数据流式写入暂存文件，完整接收后提交；断线清理暂存，不支持上传断点续传。单个浏览器按顺序上传，服务最多同时接收 4 个文件。
- 删除：勾选文件/文件夹（支持本页全选、Ctrl/Shift 多选）后点“删除所选”，或使用右键删除。确认窗口显示每个选中对象的完整路径、包含的文件/目录数量和大小。**这是永久删除，不进入回收站**，目录内所有内容都会删除。取消窗口不执行删除。
- 删除确认绑定当前 Session，10 分钟有效、一次性使用。执行前再次校验整棵目录快照、受保护目录和关联分享。内容变化会拒绝执行。任务开始后“停止剩余”只停止尚未删除的对象，已删除内容不能恢复；失败/停止时显示已删除项数。单次最多 500 个选中对象、递归最多 100000 项。

**链接一致性策略：**移动、重命名或覆盖如果影响任一现有分享（含文件夹下的文件、已过期/已禁用链接）会被阻止，提示关联链接 ID。先在链接管理中删除关联授权，再执行操作并创建新链接。第一版不自动重写已有分享路径，也不静默让旧链接指向替换对象。

程序拒绝 `..`、UNC/设备路径、ADS、符号链接、Junction、重解析点和 Windows 非规范名称。文件权限以运行进程的 Windows 用户为准；不可访问的目录返回可读错误，浏览列表隐藏无权限及受保护条目。OneDrive 占位文件等重解析点也会被拒绝；需要使用普通本地副本。

目录内部 `.httpd-*` 为任务暂存保留前缀，不在买家页面展示或下载。服务正常取消时清理暂存；断电重启后将未完成任务标为 interrupted，**不会自动删除未知残留目录或重放移动**。管理员检查任务源/目标及 `.httpd-*` 内容后手动处理。

## 6. 创建与管理分享

选中单个文件或目录，点击“创建下载链接”。窗口显示完整本地路径、类型、文件大小，支持显示名称、备注、1/6/12/24 小时、3/7/30 天以及自定义小时或具体本地过期日期。数据库以 UTC Unix 时间存储，管理员页面按浏览器时区显示。

令牌为 32 随机字节编码出的 43 字符 URL-safe 字符串。URL 是持有即授权的凭据，同一对象可生成多个独立链接。不要发布到公共搜索引擎或日志。

文件链接直接下载；文件夹链接打开只读动态目录，可浏览子文件夹、查看元数据、复制和下载单个文件。文件夹分享会自动包含其有效期内新增的普通文件，请明确理解动态目录授权。

下载链接页提供搜索/筛选/排序、状态统计、完整路径查看、复制 URL、编辑显示名称与备注、修改过期时间、续期、启用/禁用、删除、访问记录，以及确认后批量禁用/删除/续期。编辑过期时间保留原 URL。删除仅撤销授权，不删除磁盘内容。

每次新下载/Range 请求都会检查授权。过期、禁用、删除或源对象身份变化后拒绝新请求；已建立连接可继续，优雅关闭宽限期除外。外部程序替换同名文件后旧文件授权拒绝使用，需删掉旧授权后重建；普通原地修改会产生新 ETag。

## 7. HTTP 断点续传行为

复用锁定版本的 [Starlette FileResponse](https://starlette.dev/responses/) 进行单区间解析/传输，遵循 [RFC 9110](https://www.rfc-editor.org/rfc/rfc9110.html) 的 Range/If-Range 语义，并加入 Windows 句柄与版本校验。

| 请求 | 行为 |
|---|---|
| 普通 GET | 200 完整流式响应 |
| HEAD | 完整文件元数据，无正文，忽略 Range |
| `bytes=start-end` / `bytes=start-` / `bytes=-suffix` | 206，准确 Content-Range / Content-Length |
| 不满足的范围、零后缀、空文件范围 | 416，`Content-Range: bytes */长度` |
| 非法单范围格式 | 400，不返回错误片段 |
| 多区间或超过 256 字节的 Range | 忽略 Range，返回完整 200 |
| If-Range 精确强 ETag 匹配 | 执行 Range |
| If-Range 不匹配/弱 ETag | 完整 200，避免错误拼接 |
| If-Range HTTP 日期 | 精确匹配且时间足够旧、无恢复时间戳迹象才使用范围；否则保守返回 200 |

IDM/aria2 等多连接工具通过多个独立单范围请求并发工作，不要求服务器实现 multipart 多范围。

Windows 文件打开时允许其他读者，但拒绝写入和删除共享。元数据和数据来自同一个已验证句柄；关闭连接后及时释放。ETag 使用卷/文件 ID、创建身份、长度、高精度修改时间和 NTFS ChangeTime，避免只依赖文件名、大小或可恢复的 mtime；不预计算数百 GB 文件的完整哈希。显式 UTC Last-Modified 与 UTF-8 Content-Disposition 支持中文及特殊名称。

每次读取 256 KiB，文件大小不决定下载内存占用。文件复制每块 1 MiB，单队列执行。大目录的排序需在后端枚举元数据，内存与目录条目数有关；浏览器只接收当前页。

### curl / aria2 手动验收

在管理端创建真实文件链接，将下方 URL 替换为实际地址：

```powershell
$url = 'https://download.example.com/d/替换为实际令牌'
curl.exe -I $url
curl.exe -v -H "Range: bytes=0-1023" -o first.bin $url

# 开始下载，等待一部分完成后 Ctrl+C，再用 -C - 续传。
curl.exe -L -o buyer.bin $url
curl.exe -L -C - -o buyer.bin $url

# 多连接单区间请求。
aria2c.exe -c -x 8 -s 8 -k 4M -o buyer.bin $url

# 在本机对比源/目标 SHA256（这是手动验收，不是服务预扫描）。
Get-FileHash 'D:\Delivery\original.bin' -Algorithm SHA256
Get-FileHash '.\buyer.bin' -Algorithm SHA256
```

分享有效期要覆盖实际下载及可能恢复的时间。文件变化后下载工具应根据新的 ETag 重新下载，不能把不同版本手动拼接。

## 8. 监控、日志与数据库

监控显示活跃连接、每连接/总速度、IP、文件名、开始时间、已发送字节、独立活跃授权数、历史请求与累计发送量。统计以 `await send(...)` 成功返回的数据块为基础，**不把 Content-Length 当成已传输量**。这表示数据已交给服务器传输层，不能证明客户端已落盘或每字节均被 TCP 确认。连接、请求、授权均单独计数，不将 IP 当作唯一买家。

本版本历史“请求数”指已进入文件下载处理的 GET/HEAD（含范围错误和文件不可读），不包含浏览目录、无效令牌和并发拒绝；目录浏览会更新链接最近访问时间。活跃下载统计保留在内存，结束时一次性写库，异常断电可能丢失未结束连接的累计量。

数据目录包含：

- `files.db`、`files.db-wal`、`files.db-shm`：SQLite WAL 数据库，15 秒 busy timeout，事务写入。
- `server.log`：轮转 5 MiB × 6 个文件（当前文件和 5 份备份）。不记录密码、Session、完整令牌或请求 URL。
- `instance.lock`：操作系统进程锁，避免相同数据库同时启动两个主程序。崩溃后锁由系统释放。
- `backup-*.db`：手动生成的在线一致性快照，不自动清理备份。

数据库有 `admins / sessions / objects / links / requests / operations / settings` 表，`PRAGMA user_version=1`；拒绝打开更高版本数据库，未来升级需增加版本迁移。

在“系统设置”点“创建数据库备份”，使用 SQLite backup API，无需暂停下载。**不要在运行中只复制 files.db 而忽略 WAL。**备份包含令牌和管理员哈希，用 Windows ACL 保护，并复制到独立离线存储。恢复时停止服务，将备份作为数据目录的 `files.db`，移走旧数据库及对应 WAL/SHM 后再启动；不要混用不同时间点的数据库文件。数据目录和备份不会通过下载端暴露。

服务不开放日志文件直接下载；控制台通过经认证的 API 查看下载历史和文件操作错误。绑定失败或关键服务退出会记录明确日志并关闭另一服务。默认禁用 Uvicorn 访问日志和可能带令牌的错误堆栈。

## 9. 长期运行和开机启动

先交互完成管理员初始化、配置 Tailscale、确认测试通过，再设置计划任务。提供 `deploy/register-task.ps1` 作为**可审核的可选脚本**，执行后会登记当前普通用户登录时启动的计划任务，工作目录为项目目录。它不会为你创建管理员，也不保存应用密码。

```powershell
.\deploy\register-task.ps1 -ProjectPath 'F:\Programs\HTTPDISTRIBUTE'
```

可在 Windows“任务计划程序”中查看、手动启动、停止或删除 `HTTPFileDelivery`。如需要登录前开机运行，在任务界面改成“启动时”，选择有源文件访问权限的专门用户，并按 Windows 要求安全保存账户凭据；不要为了读取文件直接改成 SYSTEM 或最高权限。

建议关闭休眠、保证磁盘持续可用，配置任务失败后重启。脚本设置不限运行时长和失败重试。程序只能保证进程内双服务监控，断电、系统更新和尾网掉线需由系统运维处理。不要同时手动启动与计划任务使用同一个数据目录。

## 10. 自动化测试与交付验证

```powershell
.\.venv\Scripts\python.exe -m pytest -q

# 明确提供另一个物理卷上的可写目录，启用真实跨卷测试。
$env:HTTPD_TEST_SECOND_DRIVE = 'D:\'
.\.venv\Scripts\python.exe -m pytest -q
Remove-Item Env:HTTPD_TEST_SECOND_DRIVE

# 实际启动 main.py 的双端口验证，临时账号与数据自动清理。
.\.venv\Scripts\python.exe scripts\verify_main.py
```

`scripts/verify_main.py` 使用独立动态临时端口，不占用正式实例的 18080/18081。可选的 `scripts/verify_browser.cjs` 是开发用 Playwright 脚本；环境变量 `HTTPD_NODE` 指向 Node，`HTTPD_PLAYWRIGHT` 指向 Playwright 模块时，验证脚本会附加运行无界面 Chrome。生产环境不需要这些组件。

测试覆盖正常 GET/HEAD、范围边界、If-Range、ETag、恢复 mtime 的版本变化、中文名称、101 GB 稀疏文件、多连接拼接、真实 TCP 中断续传、断线句柄释放、限流与管理响应、令牌生命周期、目录分享、编码越界、Windows Junction、鉴权/CSRF、Session 轮换、登录限制、文件任务、链接一致性、取消清理和数据库备份。

实际验证结果及未验证项目见 [验证记录](VERIFICATION.md)。测试代码不包含正式管理员密码；测试凭据仅用于隔离临时数据库。

## 11. 已知边界

- 未实现文件夹整体下载、目录覆盖合并、批量打包、多用户管理员、买家账号或配额系统；其中前三项是有意的安全/产品边界。
- 文件操作阶段不应由外部程序同时修改相关目录。跨卷移动在提交前检查文件身份/大小/mtime，发生改变会保留源并停止；提交后移除源失败会报告失败，可能保留两份完整副本，应人工核对。
- Windows 是目标平台；非 Windows 的回退文件打开方式没有 Windows 拒绝写入共享锁，不应将本项目的 Windows 部署保证套用到其他平台。
- 目录浏览做逐级重解析点检查、授权边界和对象身份验证，文件最终句柄再次校验。不是对拥有同一 Windows 用户权限、能恶意并发修改 NTFS 元数据的本地攻击者的沙箱。应使用专门服务账号及目录 ACL。
- 不校验文件业务内容、不自动病毒扫描，不做源文件完整 SHA256 预扫描。极端文件系统行为、FAT/exFAT 时间精度和网络盘不在 NTFS 保证范围内。
- 不限带宽、不公平分配买家速率；64 个连接的默认上限可调。大量目录条目/链接的元数据排序在内存完成，不适合百万级目录/授权规模。
- 无 TLS 内置证书管理；HTTPS 通过 Tailscale Serve 或可选 Caddy。未配置互联网反代、路由器、防火墙、DNS 或 Tailscale 账户。
- 公网下载页面不会包含本地绝对路径；本地路径仅在经过身份验证的管理界面和数据库显示。

## 项目结构

```text
main.py                       单入口、双服务监督、信号关闭
config.py                     配置和管理绑定地址校验
config.example.json           配置示例
requirements.txt              锁定直接依赖版本
app/
  admin.py                    独立管理应用及认证 API
  auth.py                     Argon2id / Session / CSRF / 登录限制
  context.py                  共享服务与锁
  database.py                 SQLite 事务、schema 版本、备份
  download.py                 独立下载应用、Range 响应、买家目录页
  filesystem.py               Windows 路径、磁盘枚举、安全读取句柄
  instance.py                 单实例进程锁
  links.py                    授权及状态管理、变更保护
  monitor.py                  活跃下载与历史统计
  operations.py               后台复制/移动/取消/暂存提交
  file_changes.py             流式上传、删除预览/一次性确认/后台删除
  static/                     原生管理界面
tests/                        协议、安全、文件操作、真实 TCP 测试
scripts/                      实际入口和可选浏览器验证
deploy/                       Caddy 与 Windows 计划任务示例
data/                         首次运行自动创建，不提交版本库
```
