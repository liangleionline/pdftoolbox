PDF 转换工具箱 —— 源码备份与更新指南
==================================================
当前版本：1.1.11
备份时间：2026-10-03

一、这份备份包含什么
--------------------------------------------------
  pdftoolbox/            完整源工程（可直接用于构建新版本）
    app/server.py        后端主程序（含压缩包转PDF、PDF整合等全部功能）
    app/web/index.html   前端页面
    app/web/style.css    前端样式
    app/web/app.js       前端逻辑
    app/ui/config        fnOS 应用入口配置
    app/ui/images/       应用图标
    cmd/                 生命周期脚本（install/main/uninstall/upgrade...）
    config/              权限与资源配置
    wizard/install       安装向导
    manifest             应用清单（版本号、名称、入口等）
    ICON.PNG / ICON_256.PNG
  pdftoolbox-1.1.1.fpk   最新可直接安装的应用包

二、下次如何更新（重要）
--------------------------------------------------
  源工程就保存在上述 pdftoolbox/ 目录里，下次直接在此基础上改即可，
  不要再从零重建。若本机目录丢失，可从本备份包解压恢复。

  修改代码后重新打包的命令：
    cd pdftoolbox
    rm -rf app/__pycache__ app/tmp/* app.tgz
    tar -czf app.tgz -C app .          # 内层：应用代码
    cd ../
    tar -czf pdftoolbox-<版本号>.fpk -C pdftoolbox \
         manifest ICON.PNG ICON_256.PNG cmd config wizard app.tgz
    # 外层包含 manifest/图标/cmd/config/wizard/app.tgz，缺 app.tgz 会报"解压app.tgz失败"

  改版本号时同时修改 manifest 中的 version= 与 changelog= 两行。

三、fnOS 应用结构要点（踩坑记录）
--------------------------------------------------
  .fpk 是"双层 tar.gz"：
    外层 = manifest + ICON.PNG + ICON_256.PNG + cmd/ + config/ + wizard/ + app.tgz
    内层 app.tgz = 在 app/ 目录内 tar -czf app.tgz -C app . 生成
  千万不要把 app/ 目录直接放进外层，否则安装时报"解压 app.tgz 失败"。
  服务端口 5860；应用入口在 app/ui/config，key 需带 appname 前缀（pdftoolbox.main）。

四、已知需用户系统提供的工具（左下角"外部依赖"）
--------------------------------------------------
  libreoffice  (doc/xls/ppt 转 PDF)   sudo apt install -y libreoffice
  ebook-convert(EPUB/MOBI 转 PDF)     sudo apt install -y calibre
  pdftoppm     (PDF 转图片)           sudo apt install -y poppler-utils
  7z           (rar/7z 解压)          sudo apt install -y p7zip-full
  PIL + PyMuPDF 为可选项，缺失时自动回退到纯 Python 实现。

五、零依赖 WebP 解码（1.1.2 新增）
--------------------------------------------------
  app/webpcodec.py：纯标准库实现。优先 ctypes 调用系统自带 libwebp（几乎所有
  Linux 自带，浏览器/ffmpeg 都依赖它），回退 ffmpeg / ImageMagick。因此 WebP 转
  PDF 无需 pip 安装 Pillow。

六、PDF 体积优化（1.1.6 新增）
--------------------------------------------------
  问题：WebP 是有损格式，旧流程解码为原始 RGB 再用 zlib 压缩嵌入 PDF（FlateDecode），
  会膨胀约 8 倍（用户 4.7MB 压缩包曾产出 48MB PDF）。
  修复：app/jpegcodec.py 把 RGB 编码为 JPEG（/DCTDecode）嵌入，体积降到与源图相当。
  jpegcodec.encode_jpeg 优先系统 ffmpeg / ImageMagick convert（快、质量好），
  均不可用时回退纯 Python baseline JPEG 编码器 encode_jpeg_pure（零依赖兜底）。
  实测：3 张共 4.49MB 的 WebP → 3.40MB PDF（0.76x，此前约 8x）。
  纯 Python 编码器要点：标准 T.81 量化表与 Huffman 表、MCU 交错（每 MCU 2x2 Y + Cb + Cr）、
  Y 块网格必须与 MCU 对齐（边缘填充），否则熵流错位、解码全灰/饱和。

七、下载兜底（1.1.5 新增）
--------------------------------------------------
  转换结果统一保存到 DATA_DIR/downloads/ 并返回下载链接 + 服务器路径，适配飞牛 App
  无法自动触发下载的场景（提示"已开始下载"但无文件时，可复制链接或到路径取文件）。

八、自定义输出目录（1.1.7 新增）
--------------------------------------------------
  - 应用内新增「设置」面板（侧栏底部 ⚙️ 设置）：可填写 NAS 上的绝对路径作为转换结果
    输出目录，写入 DATA_DIR/settings.json 持久化，启动时自动加载。
  - 输出目录优先级：用户配置且可写 > 默认 DATA_DIR/downloads（兜底）。
  - 转换结果卡片不再显示/复制"下载链接"（飞牛 App 下地址解析易歧义），只显示保存目录。
  - 新增 API：GET /api/settings、POST /api/settings（body {"output_dir": "..."}，空串恢复默认）。
  - manifest desc 已提示：安装后请在应用「设置」中指定输出目录。

九、性能优化（1.1.8 新增）
--------------------------------------------------
  背景：弱机（NAS）转换耗时过长，前端 fetch 一直挂着，飞牛网关/代理层对长连接设超时，
  超时后直接断开 → 前端报 "Failed to fetch"（并非前端设了超时）。
  修复：
  1) 异步任务机制：所有耗时转换（图片转PDF / PDF转图片 / Office转PDF / 电子书转PDF /
     压缩包转PDF / PDF整合）改为 POST 立即返回 task_id（server.py 的 _submit_task 起后台
     线程），前端轮询 GET /api/task/<id> 取结果。连接不再长时间挂起，彻底消除 Failed to fetch，
     界面还能看到"处理中"状态。
  2) 大图缩放上限：WebP/图片转 PDF 时，超过 4096 长边的图在系统命令（ffmpeg/convert）
     编码阶段等比缩小（jpegcodec.encode_jpeg 新增 max_dim 参数，返回缩放后尺寸用于 PDF 页面）。
     实测 6000x4000 → 4096x2731，处理耗时与 PDF 体积双降。
  3) 保留系统命令优先：ffmpeg 编码 4000x3000 JPEG 仅 0.7s；纯 Python 编码器仅作无系统命令
     时的兜底（配合异步任务避免超时）。
  验证：6000x4000 WebP 异步转换约 3s 完成，PDF 页面 MediaBox 4096x2731（缩放生效）。

十、压缩包分析提速 + 设置面板可写目录（1.1.9 新增）
--------------------------------------------------
  1) 压缩包分析提速：旧 classify_archive 调 extract_archive 把整个压缩包完整解压到磁盘再
     扫描（弱机磁盘 I/O 慢）。现改为只列压缩包条目（list_archive_entries）：zip 用标准库
     读中央目录（BytesIO，不落地），rar/7z 用 `7z l -slt`（unrar 兜底 `unrar lb`），
     全程不解压。实测 60 文件 10MB zip 分析 0.00s。转换（convert）仍会真解压一次。
  2) 设置面板「可用目录」：新增 GET /api/writable-dirs（_scan_writable_dirs 探测容器内
     可写/已授权挂载的目录，扫 /vol1、/share、/media、/mnt 两层 + 常见 fnOS 位置 + 默认
     目录，避免弱机全盘扫描）。前端 loadWritableDirs 渲染列表，点选即填入输出目录输入框。

十一、修复「可用目录」漏列深层授权文件夹（1.1.10）
--------------------------------------------------
  问题：用户额外授权的深层文件夹（如 /vol1/1000/Docker/pdftoolbox，第 4 层）没出现在
  设置列表。旧 _scan_writable_dirs 只扫根目录前两层。
  修复：改为优先读 /proc/mounts 拿全部挂载点（fnOS 授权文件夹以 bind 挂载出现在容器内，
  挂载点即授权路径，不受深度限制），过滤系统伪挂载（/proc /sys /dev /run /etc /usr 等）；
  再对 /vol1 /share /media /mnt /data /srv 做 4 层深扫兜底 + 常见 fnOS 位置。
  验证：4 层目录（/mnt/a/b/c/d）可被正确列出，系统挂载被过滤。

十二、移除设置功能 + 图标核查（1.1.11）
--------------------------------------------------
  1) 应要求移除「设置」面板及输出目录配置：后端删除 OUT_DIR/_SETTINGS_FILE/_load_settings/
     _save_settings/_out_dir/_scan_writable_dirs/_handle_settings 及 /api/settings、
     /api/writable-dirs 路由；_save_download 固定落盘 DATA_DIR/downloads。前端删除设置按钮、
     panel-settings、PAGE_META.settings、loadSettings/saveDir/resetDir/loadWritableDirs 及
     相关样式。manifest desc 移除设置提示。
  2) 图标核查：根 ICON.PNG(64)、ICON_256.PNG(256) 与 app/ui/images/icon-64.png、icon-256.png
     均存在且为有效 PNG，ui/config 正确引用 images/icon-{0}.png。若飞牛应用列表图标仍不显示，
     多为 fnOS 图标缓存未刷新，建议卸载重装或重启 NAS 刷新缓存。
