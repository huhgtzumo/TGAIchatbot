# 🤖 RoleChatBot - 多功能虛擬角色聊天機器人

一個基於 Telegram 的智能聊天機器人，支持多角色扮演、語音對話和圖片互動。

## ✨ 功能特點

### 🎭 角色系統
- 支持多個預設角色：
  - 👨 虛擬戀人(男) - 溫柔體貼的男性戀人
  - 👩 虛擬戀人(女) - 可愛活潑的女性戀人
  - 👔 管家 - 專業盡責的管家
- 可自定義角色稱呼（虛擬戀人專屬）
- 實時角色切換

### 💬 智能對話
- 支持文字對話
- 保留最近 50 條對話記錄
- 自動清理過期對話
- 智能語境理解

### 🎤 語音功能
- 支持語音輸入
- 語音回覆功能
- 可設置持續語音模式
- 根據角色使用不同的語音

### 📷 圖片互動
- 支持圖片分享和分析
- 智能識別圖片內容
- 根據角色個性回應
- 支持圖片描述功能

## 🛠️ 指令列表
- `/start` - 開始對話並選擇角色
- `/finish` - 結束當前對話
- `/rename` - 修改虛擬戀人的稱呼（僅限虛擬戀人角色）

## 🚀 安裝部署

### 環境要求
- Python 3.9+
- Docker（推薦）
- FFmpeg（用於語音處理）

### Docker 部署
1. 克隆倉庫