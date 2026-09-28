# 用户指南

## 支持范围

BiliFlow 支持单个视频或多 P 视频的 BV 号、AV 号、`bilibili.com/video/...` 链接和 b23.tv 短链，也支持索引某个 UP 主的全部公开投稿。合集、番剧、课程、直播和动态页面目前不在支持范围内。

可用画质取决于视频本身、账号权限和 Bilibili 返回的流。未登录时通常无法获得会员画质；选择的编码不可用时，应用会回退到实际可用的流。

## GUI 工作流

### 解析与下载

1. 在首页输入框粘贴 BV/AV 号、完整视频链接或 b23.tv 短链。
2. 点击“开始解析”，等待作品资料卡和可用规格更新。
3. 多 P 视频可选择某一分 P 或全部分 P，再选择画质与编码。需要时勾选“下载弹幕”或“下载字幕”。
4. 点击“加入下载队列”。队列表格会显示下载、合并、完成、失败或取消状态。
5. 失败任务可在操作列重试；取消会停止网络传输并终止正在运行的 FFmpeg。

批量下载入口接受每行一个链接或编号。解析成功的项目会加入队列；失败项目会单独显示原因，不影响其他任务。

### UP 主投稿索引

1. 打开侧栏或下载选项区的“UP 主索引”，输入 UID 或空间链接。
2. 点击“刷新索引”。应用会遍历投稿分页，并把完整响应保存为 UP 主目录中的 `index.json`。
3. 可按标题/BVID 搜索，默认全选，也可全不选或逐项取消。
4. 点击“所选视频加入队列”后才会开始解析和下载；每个多 P 视频会展开为全部分 P。

也可以导入之前保存的 `index.json`，无需重新获取列表。再次下载时，已有且非空的稳定路径 MP4 会被跳过，任务仍会补齐缺失的元信息、封面、评论、弹幕或字幕。默认不覆盖已有 sidecar。

### 登录

登录不是普通公开视频下载的必要条件。需要会员画质或账号专属内容时，可打开“登录 B 站账号”：

- 推荐手动填写浏览器中的 `SESSDATA` 值，并在应用内验证。
- 扫码登录依赖 Bilibili 当前接口，接口变化时可能暂时不可用。
- 登录凭据优先保存到系统凭据库，不会上传到项目维护者服务器。

不要把 SESSDATA 发送给他人，也不要粘贴到 Issue、日志或截图中。退出登录可在登录窗口清除凭据。

### 下载设置

- **输出目录**：默认是 `~/Downloads/bilibili`。
- **默认画质**：新解析任务的初始画质。
- **最大并发**：范围 1–8。网络或磁盘较慢时建议 1–3。
- **FFmpeg 路径**：留空时从系统 PATH 自动查找，也可以选择自定义可执行文件。
- **元信息/封面**：默认启用；可同时写入 MP4 标签和封面。
- **评论/弹幕/字幕**：控制新任务的默认勾选状态；评论默认关闭。

主题不使用单独开关，会跟随 Windows、macOS 或 Linux 桌面环境的日间/夜间设置实时切换。

## TUI 工作流

在支持交互式终端的环境中运行：

```bash
python -m bilibili_downloader tui
```

TUI 提供与桌面界面一致的核心流程，包括单个视频解析、批量导入、UP 主投稿索引、登录、下载设置、并发下载、取消、重试和文件选择。下载及 API 操作在后台 worker 中执行，日志写入各平台的 BiliFlow 日志目录，不会破坏全屏终端。

常用快捷键：

| 快捷键 | 操作 |
|---|---|
| `Ctrl+L` | 登录 |
| `Ctrl+B` | 批量任务 |
| `Ctrl+U` | UP 主投稿 |
| `Ctrl+,` | 设置 |
| `Ctrl+T` | 切换日间/夜间主题 |
| `F1` | 帮助 |
| `Ctrl+Q` | 退出 |

处理 UP 主索引时，TUI 会把已经解析的 `VideoInfo` 写入索引旁的 `index.resolvecache.json`。再次处理同一索引时会优先使用缓存，以减少 API 请求和风控风险；缓存缺失或损坏时会自动回退到正常解析。

## CLI

查看命令：

```bash
python -m bilibili_downloader --help
python -m bilibili_downloader download --help
```

只解析视频信息：

```bash
python -m bilibili_downloader test BV1GJ411x7h7
python -m bilibili_downloader test av170001
python -m bilibili_downloader test "https://b23.tv/example"
```

下载：

```bash
python -m bilibili_downloader download BV1GJ411x7h7 \
  --quality 80 \
  --output ./downloads \
  --danmaku \
  --subtitle
```

| 参数 | 说明 |
|---|---|
| `source` | BV/AV 号、完整视频 URL 或 b23.tv 短链 |
| `-q, --quality` | Bilibili 画质代码，默认 `80` |
| `-o, --output` | 输出目录；省略时读取应用设置 |
| `-d, --danmaku` | 下载弹幕并转换为 ASS |
| `-s, --subtitle` | 下载字幕并转换为 SRT |
| `-c, --codec` | 视频编码：`7` AVC、`12` HEVC、`13` AV1 |
| `-p, --page` | 分 P 序号或 `all`；默认 `1` |
| `--subtitle-language` | 首选字幕语言代码；默认 `zh-Hans` |
| `--metadata / --no-metadata` | 保存/不保存 Bilibili 原始视频元信息 JSON |
| `--cover / --no-cover` | 保存/不保存封面 JPEG |
| `--comments / --no-comments` | 保存全部顶层评论及嵌套回复的原始 JSON 页面 |
| `--embed-metadata / --no-embed-metadata` | 开关 MP4 标签写入 |
| `--embed-cover / --no-embed-cover` | 开关 MP4 封面写入 |
| `--refresh-sidecars` | 显式覆盖已有 sidecar；省略时只补缺失文件 |

抓取索引并可选下载：

```bash
# 仅刷新并保存 index.json
python -m bilibili_downloader creator 123456 --output ./downloads

# 刷新索引后下载全部投稿
python -m bilibili_downloader creator 123456 --download-all --comments

# 只下载指定投稿，可重复 --bvid
python -m bilibili_downloader creator 123456 \
  --bvid BV1xxxxxxxxx --bvid BV1yyyyyyyyy

# 复用已有索引
python -m bilibili_downloader download-index ./downloads/UP名称_123456/index.json --all
```

如果已有旧版本生成的 `info.json`，可离线重建 TUI 使用的解析缓存：

```bash
python -m bilibili_downloader rebuild-cache ./downloads/UP名称_123456
```

命令会扫描 UP 主目录直属的 `[BVID] .../info.json`，把有效记录写入 `index.resolvecache.json`，并报告跳过或损坏的文件。目录中必须存在 `index.json`。

GUI 抓取长索引时会在第 1 批后创建 `index.partial.json`，随后每 10 批原子更新一次。
再次刷新同一 UID 会自动从该文件记录的 OID 游标继续；完整抓取成功后写入
`index.json` 并移除检查点。索引位于设置中的“输出目录”，不在项目源码目录。

画质代码：

| 代码 | 画质 | 代码 | 画质 |
|---:|---|---:|---|
| 6 | 240P | 16 | 360P |
| 32 | 480P | 64 | 720P |
| 80 | 1080P | 112 | 1080P+ |
| 116 | 1080P60 | 120 | 4K |
| 125 | HDR | 126 | Dolby Vision |
| 127 | 8K | | |

## 文件与配置

| 数据 | 默认位置 |
|---|---|
| macOS 配置 | `~/Library/Application Support/BiliFlow/config.json` |
| Windows 配置 | `%APPDATA%\\BiliFlow\\config.json` |
| Linux 配置 | `$XDG_CONFIG_HOME/biliflow/config.json`，未设置时使用 `~/.config/biliflow/config.json` |
| 损坏配置备份 | 与配置同目录的 `config.json.bak` |
| 视频输出 | `~/Downloads/bilibili` |
| 登录凭据 | 系统凭据库，并在权限为 `0600` 的配置文件中保留混淆恢复副本 |

旧版 `~/.bilibili-downloader/config.json` 会在首次启动时复制到当前平台的原生配置目录，原文件会保留。

应用在输出目录的 `.biliflow-parts/` 中保存断点数据并支持 HTTP Range 续传。成功合并后会清理对应中间文件；取消、网络中断或异常退出后，下次以相同视频、分 P、画质和编码重试时会继续使用已有数据。该目录可能占用较大空间，确认不再续传后可手动删除。

批量下载时，视频和音频 CDN 传输仍使用设置中的并发数；`api.bilibili.com` 的元数据请求会全局串行，并在成功请求间随机等待 1–3 秒。遇到 `412/429/-352/-401` 风控后，所有任务共享 15/45/120 秒冷却并自动重新签名重试。

文件名会移除当前操作系统不允许的字符。多 P 视频会在标题后追加分 P 序号和名称，避免互相覆盖。

UP 主归档采用稳定 BVID/CID 路径。标题变化时会优先复用已存在的标识符目录：

```text
<UP名称>_<MID>/
  index.json
  index.resolvecache.json
  [BVID] 视频标题/
    info.json
    comments.json
    cover.jpg
    P01 [CID] 分P标题.mp4
    P01 [CID] 分P标题.danmaku.xml
    P01 [CID] 分P标题.danmaku.ass
    P01 [CID] 分P标题.<语言>.srt
```

评论归档按 0.5 秒节流请求，最多重试 5 次，并在分页过程中原子写入检查点。完整翻页通常需要登录；未登录时 Bilibili 可能只返回首批评论。sidecar 失败只会产生任务警告，不会删除已经完成的视频。

## 升级与校验

- 从旧版本升级时可以直接替换应用；配置和登录凭据保存在应用包之外，不会随替换被删除。
- 首次启动新版本前，建议保留配置文件和未完成下载目录的备份。不要跨设备公开复制包含凭据的配置。
- Release 提供 `SHA256SUMS.txt`。macOS/Linux 可运行 `shasum -a 256 <安装包>`，Windows PowerShell 可运行 `Get-FileHash <安装包> -Algorithm SHA256`，并与校验文件中的对应记录比较。
- 跨大版本升级后若界面或设置异常，先退出应用并按“文件与配置”中的路径备份、移走 `config.json`，再重新启动生成默认配置。

## 限制与合规

- Bilibili 接口和鉴权规则可能变化，扫码登录及部分画质可能因此失效。
- DRM、付费课程和平台未提供可下载流的内容无法下载。
- 本工具不会绕过账号权限。请仅下载你拥有权限的内容，并遵守法律、服务条款与版权要求。
