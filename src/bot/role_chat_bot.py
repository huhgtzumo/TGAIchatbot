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
from pathlib import Path

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
    
    # 從命名空間中需的變量
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

# 在文件開頭添加語音配置
VOICE_SETTINGS = {
    "male_lover": "echo",     # 男性聲音
    "female_lover": "nova",   # 女性聲音
    "butler": "alloy"         # 專業聲音
}

# 在文件開頭添加新的常量
SUPPORTED_VOICE_FORMATS = ['.ogg', '.mp3', '.wav', '.m4a']
VOICE_SPEED = {
    "male_lover": 1.0,    # 正常速度
    "female_lover": 1.1,  # 稍快
    "butler": 0.9         # 稍慢，更穩重
}

# 在文件開頭添加新的常量
SUPPORTED_IMAGE_FORMATS = ['.jpg', '.jpeg', '.png', '.webp']
MAX_IMAGE_DIMENSION = 2048  # 最大圖片尺寸

# 添加速率限制常量
class RateLimits:
    GPT35_RPM = 3500  # 每分鐘請求數
    GPT4_VISION_RPM = 50
    WHISPER_RPM = 50
    TTS_RPM = 50
    
    def __init__(self):
        # 為每個服務創建請隊
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

# 更新錯誤消息
ERROR_MESSAGES = {
    "rate_limit": "抱歉，服務當前請求過多，請稍後再試。",
    "voice_too_large": "語音檔案太大，請發送小於 20MB 的語音。",
    "photo_too_large": "圖片太大，請發送小於 10MB 的圖片。",
    "no_role": "請先使用 /start 命令選擇一個角色。",
    "process_error": "抱歉，處理您的請求時發生錯誤。請稍後重試。",
    "voice_error": "抱歉，處理您的語音時發生錯誤。",
    "photo_error": "抱歉，處理您的圖片時發生錯誤。"
}

class RoleChatBot:
    def __init__(self):
        self.role_manager = RoleManager()
        self.user_roles = {}
        self.custom_names = {}
        self.voice_mode_users = set()  # 新增：追踪使用語音模式��用戶
        self.client = AsyncOpenAI(api_key=OPENAI_API_KEY)
        self.rate_limits = RateLimits()
        logger.info("RoleChatBot 初始化完成")
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """處理 /start 命令，顯示角色選擇菜單"""
        help_text = """
歡迎使用聊天機器人！

可用的命令：
/start - 開始對話並選擇角色
/finish - 結束當前對話
/rename - 修改虛擬戀人的稱呼（僅限虛擬戀人角色）

功能說明：
• 支持語音對話
• 支持圖片分享
• 支持語音回覆（當您發送語音消息或要求時）
• 保留最近50條對話記錄

請選擇想要聊天的角色：
"""
        keyboard = []
        for role_id, role in self.role_manager.roles.items():
            keyboard.append([
                InlineKeyboardButton(role['name'], callback_data=f"select_role_{role_id}")
            ])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(help_text, reply_markup=reply_markup)
    
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
        
        # 檢查是否在等待設置名稱
        if context.user_data.get('waiting_for_name', False):
            custom_name = update.message.text
            self.custom_names[user_id] = custom_name
            context.user_data['waiting_for_name'] = False
            await update.message.reply_text(f"好的，我明白了。從現在開始，您可以叫我 {custom_name}。\n請直接發送消息開始聊天，使用 /finish 結束對話。")
            return
        
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
        
        # 如果是虛擬戀人，詢問稱呼
        if role_id in ["male_lover", "female_lover"]:
            await query.message.edit_text("請告訴我，您想怎麼稱呼我呢？")
            context.user_data['waiting_for_name'] = True
            return
        
        # 如果是管家，直接使用默認名稱
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
            
            # 處理轉換後的文字消，並指定需要語音回覆
            text_message = transcript.text
            await self.process_message(update, context, text_message, voice_reply=True)

        except Exception as e:
            logger.error(f"處理語音消息時發生錯誤: {str(e)}")
            await update.message.reply_text("抱歉，處理您的語音消息時發生錯誤。")

    async def process_image(self, photo_bytes: bytes, role_id: str, caption: str = None) -> str:
        """根據角色處理圖片分析"""
        role_prompts = {
            "male_lover": "作為一個關心的男朋友，請描述這張圖片並給出溫柔的回應。",
            "female_lover": "作為一個可愛的女朋友，請描述這張圖片並給出甜美的回應。",
            "butler": "作為一個專業的管家，請分析這張圖片並給出得體的回應。"
        }
        
        # 將圖片描述加入到提示詞中
        base_prompt = role_prompts.get(role_id, "請描述這張圖片的內容，並以角色的身份做出回應。")
        prompt = f"{base_prompt}\n用戶的圖片描述：{caption}" if caption else base_prompt
        
        try:
            base64_image = base64.b64encode(photo_bytes).decode('utf-8')
            response = await self.client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": prompt
                            },
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{base64_image}"
                                }
                            }
                        ]
                    }
                ],
                max_tokens=300
            )
            return response.choices[0].message.content
        except Exception as e:
            logger.error(f"圖片分析失敗: {str(e)}")
            raise

    async def handle_photo(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """處理圖片消息"""
        try:
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

            photo_file = await context.bot.get_file(photo.file_id)
            photo_bytes = await photo_file.download_as_bytearray()
            
            # 分析圖片，傳入 caption
            role_id = self.user_roles[user_id]
            response_text = await self.process_image(
                photo_bytes, 
                role_id,
                caption=update.message.caption
            )
            
            # 發送回覆（只發送一次）
            custom_name = self.custom_names.get(user_id, self.role_manager.get_role(role_id)['name'])
            formatted_message = f"{custom_name}：{response_text}"
            await update.message.reply_text(formatted_message)
            
            # 將圖片回應添加到聊天歷史
            self.role_manager.add_chat_history(user_id, role_id, {
                "role": "user",
                "content": "【分享了一張圖片】" + (f"\n留言：{update.message.caption}" if update.message.caption else "")
            })
            self.role_manager.add_chat_history(user_id, role_id, {
                "role": "assistant",
                "content": response_text
            })

        except Exception as e:
            logger.error(f"處理圖片消息時發生錯誤: {str(e)}")
            await update.message.reply_text("抱歉，處理您的圖片時發生錯誤。")

    async def send_voice_reply(self, update: Update, text: str):
        """發送語音回覆"""
        try:
            user_id = update.effective_user.id
            role_id = self.user_roles[user_id]
            voice = VOICE_SETTINGS.get(role_id, "alloy")
            speed = VOICE_SPEED.get(role_id, 1.0)
            
            # 確保 temp 目錄存在
            temp_dir = Path("temp")
            temp_dir.mkdir(exist_ok=True)
            
            # 生成臨時文件路徑
            speech_file = temp_dir / f"speech_{user_id}_{datetime.now().timestamp()}.mp3"
            
            # 生成語音
            response = await self.client.audio.speech.create(
                model="tts-1",
                voice=voice,
                input=text,
                speed=speed,
                response_format="mp3"  # 指定回應格式
            )
            
            # 直接將響應內容寫入文件
            with open(speech_file, "wb") as file:
                file.write(response.content)
            
            # 發送語音文件
            with open(speech_file, "rb") as audio:
                await update.message.reply_voice(audio)
            
            # 刪除臨時文件
            speech_file.unlink(missing_ok=True)

        except Exception as e:
            logger.error(f"生成語音回覆時發生錯誤: {str(e)}")
            await update.message.reply_text("抱歉，生成語音回覆時發生錯誤。")

    async def process_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE, message_text: str = None, voice_reply: bool = False):
        """處理消息的核心邏輯"""
        user_id = update.effective_user.id
        text = message_text or update.message.text
        
        # 檢查是否需要語音回覆
        voice_keywords = ["用語音回答", "跟我說話", "用語音", "之後都用語音"]
        if any(keyword in text for keyword in voice_keywords):
            self.voice_mode_users.add(user_id)  # 將用戶加入語音模式
            await update.message.reply_text("好的，我之後會用語音和您交流。")
        
        # 決定是否使用語音回覆
        voice_reply = voice_reply or user_id in self.voice_mode_users
        
        # 檢查速率限制
        if not await self.rate_limits.check_rate_limit(self.rate_limits.gpt35_requests, RateLimits.GPT35_RPM):
            await update.message.reply_text("抱歉，聊天服務當前請求過多，請稍後再試。")
            return
        
        role_id = self.user_roles[user_id]
        role = self.role_manager.get_role(role_id)
        
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
            
            # 根據回覆類型選擇回覆方式
            if voice_reply:
                # 只發送語音回覆
                await self.send_voice_reply(update, ai_message)
            else:
                # 只發送文字回覆
                custom_name = self.custom_names.get(user_id, role['name'])
                formatted_message = f"{custom_name}：{ai_message}"
                await update.message.reply_text(formatted_message)
            
        except Exception as e:
            logger.error(f"處理消息時發生錯誤: {str(e)}")
            await update.message.reply_text("抱歉，處理您的消息時發生錯誤。")

    async def rename(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """處理 /rename 命令"""
        user_id = update.effective_user.id
        if user_id not in self.user_roles:
            await update.message.reply_text("請先使用 /start 命令選擇一個角色。")
            return
        
        role_id = self.user_roles[user_id]
        if role_id not in ["male_lover", "female_lover"]:
            await update.message.reply_text("只有虛擬戀人角色可以修改稱呼。")
            return
        
        context.user_data['waiting_for_name'] = True
        await update.message.reply_text("請告訴我，您想怎麼稱呼我呢？")

    async def retry_with_exponential_backoff(self, func, *args, **kwargs):
        """帶有指數退避的重試機制"""
        max_retries = 3
        base_delay = 1
        
        for attempt in range(max_retries):
            try:
                return await func(*args, **kwargs)
            except Exception as e:
                if attempt == max_retries - 1:
                    raise
                
                delay = base_delay * (2 ** attempt)
                logger.warning(f"嘗試失敗 {attempt + 1}/{max_retries}，等待 {delay} 秒後重試")
                await asyncio.sleep(delay)

    def run(self):
        """運行機器人"""
        logger.info("開始初始化機器人...")
        try:
            application = Application.builder().token(BOT_TOKEN).build()
            logger.info("機器人實例創建成功")
            
            # 添加命令處理器
            application.add_handler(CommandHandler("start", self.start))
            application.add_handler(CommandHandler("finish", self.finish))
            application.add_handler(CommandHandler("rename", self.rename))  # 添加 rename 命令處理器
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
        return role_id in ["male_lover", "female_lover", "butler"]

def main():
    """主函數"""
    bot = RoleChatBot()
    bot.run()

if __name__ == "__main__":
    main() 