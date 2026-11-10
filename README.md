# PDF 转换工具箱 (PDF Toolbox)

[![License](https://img.shields.io/github/license/liangleionline/pdftoolbox?color=blue&logo=gnu)](https://github.com/liangleionline/pdftoolbox/blob/main/LICENSE)
[![Release](https://img.shields.io/github/v/release/liangleionline/pdftoolbox?color=2ea44f)](https://github.com/liangleionline/pdftoolbox/releases)
[![Downloads](https://img.shields.io/github/downloads/liangleionline/pdftoolbox/total?color=brightgreen&logo=github)](https://github.com/liangleionline/pdftoolbox/releases)
[![Stars](https://img.shields.io/github/stars/liangleionline/pdftoolbox?color=yellow&logo=github)](https://github.com/liangleionline/pdftoolbox/stargazers)
[![Platform](https://img.shields.io/badge/platform-fnOS%20%7C%20Linux-lightgrey)](https://www.fnnas.com/)
[![Free](https://img.shields.io/badge/price-100%25%20Free-brightgreen)](https://github.com/liangleionline/pdftoolbox)

一款运行在 **飞牛 fnOS NAS** 上的在线 PDF 转换工具。

**本软件完全免费，没有任何收费内容，无广告、无内购、无会员。**

## ✨ 功能一览（v1.1.11）

### 📷 图片
- **图片转 PDF**：多张 JPG / PNG / WebP 图片合并为一个 PDF
  - 支持**按文件名自动排序**、**手动拖拽排序**、**从队列删除单个文件**
  - 原生支持 **WebP**，零额外依赖即可转换
- **PDF 转图片**：将 PDF 每一页导出为 PNG / JPEG，打包为 ZIP

### 📄 文档
- **Word 转 PDF**（.doc / .docx）
- **Excel 转 PDF**（.xls / .xlsx）
- **PPT 转 PDF**（.ppt / .pptx）
- **TXT 转 PDF**（纯 Python 实现，零依赖）

### 📚 电子书
- **电子书转 PDF**：EPUB / MOBI / TXT 等格式

### 🗜 压缩包
- **压缩包转 PDF**：支持 ZIP / RAR / 7Z
  - 图片包：单文件直接转换；多文件可弹窗选择「合并为一个 PDF」或「每个文件单独生成 PDF（打包 zip）」，合并时按文件名顺序生成
  - 文档包：同上两种方式
  - 混合内容：提示先将内容整理为同一类型后再上传
- **PDF 整合**：多个 PDF 按排序（文件名 / 手动拖拽）整合为一个 PDF

## ⚡ 特性与优化
- **零依赖编解码**：WebP / JPEG 通过系统库与子进程实现，无需 pip 安装 Pillow 等额外依赖
- **PDF 体积优化**：有损图片采用 JPEG（/DCTDecode）编码嵌入，体积与源图相当
- **异步任务机制**：转换在后台执行、前端轮询进度，弱机长时间转换不再被网关掐断（消除 Failed to fetch）
- **大图自动缩放**：超过 4096 长边的图片自动等比缩小，显著降低处理耗时与 PDF 体积
- **压缩包秒级分析**：分析压缩包仅列条目、不解压，秒级返回
- **外部依赖检测**：面板一键检测所需命令，未安装项提供**复制安装命令**
- **移动端适配**：抽屉导航 + 汉堡按钮，手机也能顺畅使用

## 📦 安装
1. 前往 **Releases** 下载最新版安装包 `pdftoolbox-<版本>.fpk`
2. 进入飞牛 fnOS → 应用中心 → 手动安装，选择该 `.fpk` 文件
3. 安装后无需额外配置，转换结果自动保存到应用数据目录的 `downloads` 文件夹

## 📁 输出目录
转换结果统一保存在**应用数据目录的 `downloads` 文件夹**（可在飞牛「应用文件 / 文件管理」中进入该应用目录查看并取用）。无需在应用内额外设置输出路径。

## 🛠 外部依赖
多数功能零依赖即可工作。以下为可选的外部命令，缺失时可在应用「外部依赖」面板一键复制安装命令：
- LibreOffice —— Word / Excel / PPT 转 PDF
- Calibre `ebook-convert` —— 电子书转 PDF
- `pdftoppm` / `mutool` —— PDF 转图片
- `p7zip`（`7z`）—— RAR / 7Z 压缩包
- `unrar` —— RAR 压缩包（可选）

## 📝 版本更新

| 版本 | 主要变更 |
|---|---|
| **v1.1.11** | 移除「设置」面板及输出目录配置，转换结果固定保存到应用数据目录 `downloads`；修复桌面图标（确保 `ui/images` 下 64/256 图标完整打包） |
| v1.1.10 | 修复「可用目录」漏列深层授权文件夹（改为读取挂载点，不受目录深度限制） |
| v1.1.9 | 压缩包分析改为只列条目、不解压（分析秒级返回）；新增可写目录点选填入 |
| v1.1.8 | 转换改为异步任务 + 前端轮询（解决弱机 Failed to fetch）；超过 4096 长边的大图自动缩放 |
| v1.1.7 | 新增「设置」输出目录；转换结果改为仅显示保存目录（该功能已于 v1.1.11 移除） |
| v1.1.6 | PDF 体积优化：WebP 转 PDF 改用 JPEG（/DCTDecode）嵌入，体积与源图相当 |

## 📜 许可证
[GPL-3.0](./LICENSE)

本软件完全免费。采用 GPL-3.0 协议授权：你可以自由使用、修改与分发，但**修改后的衍生作品必须同样以 GPL-3.0 开源**，并保留原作者署名，不得闭源商用。
