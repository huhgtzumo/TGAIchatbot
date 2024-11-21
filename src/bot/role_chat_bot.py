import os
import sys
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Voice
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
import openai
from openai import AsyncOpenAI
from .role_manager import RoleManager
import aiohttp
import io
import json
import base64
from collections import deque
from datetime import datetime, timedelta
import asyncio

# 設置日誌
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.FileHandler('logs/bot.log'),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)

# 直接讀取配置文件
current_dir = os.path.dirname(os.path.abspath(__file__))
config_file = os.path.join(os.path.dirname(os.path.dirname(current_dir)), 'config', 'config.py')

# 使用 exec 執行配置文件
config_namespace = {}
try:
    with open(config_file, 'r') as f:
        exec(f.read(), config_namespace)
    logger.info("成功導入配置")
    
    # 從命名空間中獲取所需的變量
    BOT_TOKEN = config_namespace['BOT_TOKEN']
    OPENAI_API_KEY = config_namespace['OPENAI_API_KEY']
    ROLES = config_namespace['ROLES']
except Exception as e:
    logger.error(f"導入配置時出錯: {e}")
    logger.error(f"配置文件是否存在: {os.path.exists(config_file)}")
    sys.exit(1)

# 設置 OpenAI API
openai.api_key = OPENAI_API_KEY

# 在文件開頭添加常量
MAX_VOICE_SIZE = 20 * 1024 * 1024  # 20MB
MAX_PHOTO_SIZE = 10 * 1024 * 1024  # 10MB
MAX_RETRIES = 3

# 添加速率限制常量
class RateLimits:
    GPT35_RPM = 3500  # 每分鐘請求數
    GPT4_VISION_RPM = 50
    WHISPER_RPM = 50
    TTS_RPM = 50
    
    def __init__(self):
        # 為每個服務創建請求隊列
        self.gpt35_requests = deque(maxlen=self.GPT35_RPM)
        self.vision_requests = deque(maxlen=self.GPT4_VISION_RPM)
        self.whisper_requests = deque(maxlen=self.WHISPER_RPM)
        self.tts_requests = deque(maxlen=self.TTS_RPM)
    
    async def check_rate_limit(self, queue: deque, rpm: int) -> bool:
        """檢查是否超過速率限制"""
        now = datetime.now()
        # 清理超過1分鐘的請求
        while queue and (now - queue[0]) > timedelta(minutes=1):
            queue.popleft()
        
        if len(queue) >= rpm:
            return False
        
        queue.append(now)
        return True

class RoleChatBot:
    def __init__(self):
        self.role_manager = RoleManager()
        self.user_roles = {}
        self.client = AsyncOpenAI(api_key=OPENAI_API_KEY)
        self.rate_limits = RateLimits()
        logger.info("RoleChatBot 初始化完成")
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """處理 /start 命令，顯示角色選擇菜單"""
        keyboard = []
        for role_id, role in self.role_manager.roles.items():
            keyboard.append([
                InlineKeyboardButton(role['name'], callback_data=f"select_role_{role_id}")
            ])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            "請選擇想要聊天的角色：",
            reply_markup=reply_markup
        )
    
    async def finish(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """處理 /finish 命令"""
        user_id = update.effective_user.id
        if user_id in self.user_roles:
            role_id = self.user_roles[user_id]
            self.role_manager.clear_chat_history(user_id, role_id)
            del self.user_roles[user_id]
            await self.start(update, context)
        else:
            await update.message.reply_text("您還沒有開始任何對話。")
    
    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """處理文字消息"""
        user_id = update.effective_user.id
        if user_id not in self.user_roles:
            await update.message.reply_text("請先使用 /start 命令選擇一個角色進行對話。")
            return
        
        await self.process_message(update, context)
    
    async def handle_role_selection(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """處理角色選擇"""
        query = update.callback_query
        await query.answer()
        
        user_id = query.from_user.id
        role_id = query.data.replace("select_role_", "")
        
        # 更新用戶選擇的角色
        self.user_roles[user_id] = role_id
        role = self.role_manager.get_role(role_id)
        
        await query.message.edit_text(f"您已選擇與 {role['name']} 對話。\n請直接發送消息開始聊天，使用 /finish 結束對話。")

    async def handle_voice(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """處理語音消息"""
        # 檢查速率限制
        if not await self.rate_limits.check_rate_limit(self.rate_limits.whisper_requests, RateLimits.WHISPER_RPM):
            await update.message.reply_text("抱歉，語音識別服務當前請求過多，請稍後再試。")
            return
        
        if update.message.voice.file_size > MAX_VOICE_SIZE:
            await update.message.reply_text("語音文件太大，請發送小於 20MB 的語音。")
            return
        user_id = update.effective_user.id
        if user_id not in self.user_roles:
            await update.message.reply_text("請先使用 /start 命令選擇一個角色進行對話。")
            return

        try:
            # 獲取語音文件
            voice = update.message.voice
            voice_file = await context.bot.get_file(voice.file_id)
            
            # 下載語音文件
            voice_bytes = await voice_file.download_as_bytearray()
            
            # 使用 Whisper API 進行語音轉文字
            audio_file = io.BytesIO(voice_bytes)
            audio_file.name = "voice_message.ogg"
            
            transcript = await self.client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file
            )
            
            # 處理轉換後的文字消息
            text_message = transcript.text
            await self.process_message(update, context, text_message)

        except Exception as e:
            logger.error(f"處理語音消息時發生錯誤: {str(e)}")
            await update.message.reply_text("抱歉，處理您的語音消息時發生錯誤。")

    async def handle_photo(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """處理圖片消息"""
        # 檢查速率限制
        if not await self.rate_limits.check_rate_limit(self.rate_limits.vision_requests, RateLimits.GPT4_VISION_RPM):
            await update.message.reply_text("抱歉，圖片分析服務當前請求過多，請稍後再試。")
            return
        
        photo = update.message.photo[-1]
        if photo.file_size > MAX_PHOTO_SIZE:
            await update.message.reply_text("圖片太大，請發送小於 10MB 的圖片。")
            return
        user_id = update.effective_user.id
        if user_id not in self.user_roles:
            await update.message.reply_text("請先使用 /start 命令選擇一個角色進行對話。")
            return

        try:
            # 獲取圖片文件
            photo = update.message.photo[-1]  # 獲取最大尺寸的圖片
            photo_file = await context.bot.get_file(photo.file_id)
            
            # 下載圖片文件
            photo_bytes = await photo_file.download_as_bytearray()
            
            # 使用 GPT-4 Vision 分析圖片
            response = await self.client.chat.completions.create(
                model="gpt-4-vision-preview",
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{base64.b64encode(photo_bytes).decode('utf-8')}"
                                }
                            },
                            {
                                "type": "text",
                                "text": "請描述這張圖片的內容，並以角色的身份做出回應。"
                            }
                        ]
                    }
                ],
                max_tokens=300
            )
            
            # 處理圖片分析結果
            await self.process_message(update, context, "【分享了一張圖片】")
            
            # 生成回覆
            role_id = self.user_roles[user_id]
            role = self.role_manager.get_role(role_id)
            response_text = f"{role['name']}：{response.choices[0].message.content}"
            await update.message.reply_text(response_text)

        except Exception as e:
            logger.error(f"處理圖片消息時發生錯誤: {str(e)}")
            await update.message.reply_text("抱歉，處理您的圖片時發生錯誤。")

    async def send_voice_reply(self, update: Update, text: str):
        """發送語音回覆"""
        # 檢查速率限制
        if not await self.rate_limits.check_rate_limit(self.rate_limits.tts_requests, RateLimits.TTS_RPM):
            await update.message.reply_text("抱歉，語音合成服務當前請求過多，請稍後再試。")
            return
        
        try:
            # 使用 OpenAI TTS API 生成語音
            response = await self.client.audio.speech.create(
                model="tts-1",
                voice="alloy",
                input=text
            )
            
            # 將音頻數據保存為臨時文件
            audio_file = io.BytesIO(await response.read())
            
            # 發送語音消息
            await update.message.reply_voice(audio_file)

        except Exception as e:
            logger.error(f"生成語音回覆時發生錯誤: {str(e)}")
            await update.message.reply_text("抱歉，生成語音回覆時發生錯誤。")

    async def process_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE, message_text: str = None):
        """處理消息的核心邏輯"""
        # 檢查速率限制
        if not await self.rate_limits.check_rate_limit(self.rate_limits.gpt35_requests, RateLimits.GPT35_RPM):
            await update.message.reply_text("抱歉，聊天服務當前請求過多，請稍後再試。")
            return
        
        user_id = update.effective_user.id
        role_id = self.user_roles[user_id]
        role = self.role_manager.get_role(role_id)
        
        # 使用傳入的消息文本或原始消息文本
        text = message_text or update.message.text
        
        # 添加用戶消息到歷史記錄
        self.role_manager.add_chat_history(user_id, role_id, {
            "role": "user",
            "content": text
        })
        
        # 獲取聊天歷史
        chat_history = self.role_manager.get_chat_history(user_id, role_id)
        
        # 獲取格式化的提示詞
        prompt = self.role_manager.format_prompt(role_id, chat_history)
        
        try:
            # 調用 API
            response = await self.client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[
                    {"role": "system", "content": prompt},
                    *chat_history
                ],
                temperature=0.7,
                max_tokens=1000
            )
            
            # 獲取回應內容
            ai_message = response.choices[0].message.content
            
            # 添加 AI 回應到歷史記錄
            self.role_manager.add_chat_history(user_id, role_id, {
                "role": "assistant",
                "content": ai_message
            })
            
            # 在回覆前加上角色名稱
            formatted_message = f"{role['name']}：{ai_message}"
            
            # 發送文字回覆
            await update.message.reply_text(formatted_message)
            
            # 如果消息包含 [語音回覆] 標記，則同時發送語音回覆
            if "[語音回覆]" in text.upper():
                await self.send_voice_reply(update, ai_message)
            
        except Exception as e:
            logger.error(f"處理消息時發生錯誤: {str(e)}")
            await update.message.reply_text("抱歉，處理您的消息時發生錯誤。")

    def run(self):
        """運行機器人"""
        logger.info("開始初始化機器人...")
        try:
            application = Application.builder().token(BOT_TOKEN).build()
            logger.info("機器人實例創建成功")
            
            # 添加命令處理器
            application.add_handler(CommandHandler("start", self.start))
            application.add_handler(CommandHandler("finish", self.finish))
            application.add_handler(CallbackQueryHandler(self.handle_role_selection, pattern="^select_role_"))
            
            # 添加消息處理器
            application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))
            application.add_handler(MessageHandler(filters.VOICE, self.handle_voice))
            application.add_handler(MessageHandler(filters.PHOTO, self.handle_photo))
            
            # 添加錯誤處理器
            async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
                logger.error(f"更新信息: {update}")
                logger.error(f"錯誤信息: {context.error}")
                if update and update.effective_message:
                    await update.effective_message.reply_text(
                        "抱歉，處理您的請求時發生錯誤。請稍後重試。"
                    )
            
            application.add_error_handler(error_handler)
            logger.info("所有處理器添加完成")
            
            # 啟動機器人
            logger.info("機器人正在啟動...")
            application.run_polling(drop_pending_updates=True)
            
        except Exception as e:
            logger.error(f"啟動時發生錯誤: {e}")
            import traceback
            logger.error(traceback.format_exc())

    def validate_role_id(self, role_id: str) -> bool:
        """驗證角色ID是否有效"""
        return role_id in ["venti", "nahida", "zhongli", "furina"]

def main():
    """主函數"""
    bot = RoleChatBot()
    bot.run()

if __name__ == "__main__":
    main() 