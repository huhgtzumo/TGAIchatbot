from datetime import datetime, timedelta
from config.config import ROLES, HISTORY_EXPIRY_HOURS, MAX_HISTORY_LENGTH
import logging

logger = logging.getLogger(__name__)

class RoleManager:
    def __init__(self):
        self.roles = ROLES
        self.chat_history = {}
        self.history_expiry = timedelta(hours=HISTORY_EXPIRY_HOURS)
        self.max_history_length = MAX_HISTORY_LENGTH
    
    def format_prompt(self, role_id: str, chat_history: list) -> str:
        """格式化角色提示詞"""
        role = self.get_role(role_id)
        if not role:
            return ""
        
        # 組合完整提示詞
        base_prompt = role['prompt']
        formatted_history = "\n".join([
            f"{'用戶' if msg['role'] == 'user' else '你'}: {msg['content']}"
            for msg in chat_history[-5:]  # 只使用最近5條對話
        ])
        
        return f"{base_prompt}\n\n最近的對話記錄：\n{formatted_history}"
    
    def get_role(self, role_id: str):
        """獲取角色信息"""
        return self.roles.get(role_id)
    
    def add_chat_history(self, user_id: int, role_id: str, message: dict):
        """添加聊天記錄"""
        if user_id not in self.chat_history:
            self.chat_history[user_id] = {}
        
        if role_id not in self.chat_history[user_id]:
            self.chat_history[user_id][role_id] = []
        
        message['timestamp'] = datetime.now()
        history = self.chat_history[user_id][role_id]
        history.append(message)
        
        # 限制歷史記錄長度
        if len(history) > self.max_history_length:
            history.pop(0)
        
        self._clean_old_history(user_id, role_id)
    
    def get_chat_history(self, user_id: int, role_id: str):
        """獲取聊天歷史"""
        if user_id in self.chat_history and role_id in self.chat_history[user_id]:
            self._clean_old_history(user_id, role_id)
            return [
                {"role": msg["role"], "content": msg["content"]}
                for msg in self.chat_history[user_id][role_id]
            ]
        return []
    
    def clear_chat_history(self, user_id: int, role_id: str = None):
        """清除聊天歷史"""
        if role_id:
            if user_id in self.chat_history:
                self.chat_history[user_id].pop(role_id, None)
                logger.info(f"已清除用戶 {user_id} 的 {role_id} 角色歷史記錄")
                # 如果用戶沒有其他角色的歷史記錄，清理用戶記錄
                if not self.chat_history[user_id]:
                    self.chat_history.pop(user_id, None)
        else:
            # 清除用戶的所有歷史記錄
            self.chat_history.pop(user_id, None)
            logger.info(f"已清除用戶 {user_id} 的所有歷史記錄")
    
    def _clean_old_history(self, user_id: int, role_id: str):
        """清理過期的聊天記錄"""
        if user_id not in self.chat_history or role_id not in self.chat_history[user_id]:
            return
        
        current_time = datetime.now()
        history = self.chat_history[user_id][role_id]
        
        # 過濾掉過期的消息
        valid_messages = [
            msg for msg in history
            if current_time - msg['timestamp'] < self.history_expiry
        ]
        
        # 如果有消息被清理，記錄日誌
        if len(valid_messages) < len(history):
            logger.info(f"用戶 {user_id} 的 {role_id} 角清理了 {len(history) - len(valid_messages)} 條過期消息")
        
        self.chat_history[user_id][role_id] = valid_messages
        
        # 如果該角色的歷史記錄為空，清理整個角色記錄
        if not valid_messages:
            self.chat_history[user_id].pop(role_id, None)
            logger.info(f"用戶 {user_id} 的 {role_id} 角色歷史記錄已清空")
        
        # 如果用戶沒有任何角色的歷史記錄，清理用戶記錄
        if not self.chat_history[user_id]:
            self.chat_history.pop(user_id, None)
            logger.info(f"用戶 {user_id} 的所有歷史記錄已清空")
    
    def get_available_roles(self):
        """獲取所有可用角色列表"""
        return {
            role_id: {
                "name": role["name"],
                "description": role["description"]
            }
            for role_id, role in self.roles.items()
        }