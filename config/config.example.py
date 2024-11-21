# Telegram Bot Token
BOT_TOKEN = "your_bot_token_here"

# OpenAI API Key
OPENAI_API_KEY = "your_openai_api_key_here"

# 角色配置
ROLES = {
    "male_lover": {
        "name": "虛擬戀人(男)",
        "prompt": "你現在扮演的是溫柔體貼的男性戀人..."
    },
    "female_lover": {
        "name": "虛擬戀人(女)",
        "prompt": "你現在扮演的是可愛活潑的女性戀人..."
    },
    "butler": {
        "name": "管家",
        "prompt": "你現在扮演的是專業盡責的管家..."
    }
}

# 聊天歷史設置
HISTORY_EXPIRY_HOURS = 24
MAX_HISTORY_LENGTH = 50
