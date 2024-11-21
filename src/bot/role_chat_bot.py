import os
import sys
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
import openai
from openai import AsyncOpenAI
from .role_manager import RoleManager

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

class RoleChatBot:
    def __init__(self):
        self.role_manager = RoleManager()
        self.user_roles = {}
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
        """處理用戶消息"""
        user_id = update.effective_user.id
        if user_id not in self.user_roles:
            await update.message.reply_text("請先使用 /start 命令選擇一個角色進行對話。")
            return
        
        try:
            role_id = self.user_roles[user_id]
            role = self.role_manager.get_role(role_id)
            
            # 添加用戶消息到歷史記錄
            self.role_manager.add_chat_history(user_id, role_id, {
                "role": "user",
                "content": update.message.text
            })
            
            # 獲取聊天歷史
            chat_history = self.role_manager.get_chat_history(user_id, role_id)
            
            # 獲取格式化的提示詞
            prompt = self.role_manager.format_prompt(role_id, chat_history)
            
            # 初始化 client
            client = AsyncOpenAI(api_key=OPENAI_API_KEY)
            
            # 調用 API
            response = await client.chat.completions.create(
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
            
            await update.message.reply_text(formatted_message)
            
        except Exception as e:
            logging.error(f"處理消息時發生錯誤: {str(e)}")
            await update.message.reply_text("抱歉，處理您的消息時發生錯誤。")

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
            application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))
            
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