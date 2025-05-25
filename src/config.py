import os
from typing import Optional

class Config:
    def __init__(self):
        self.bot_token = self._get_bot_token()
        self.webhook_url = os.getenv('WEBHOOK_URL')
        self.port = int(os.getenv('PORT', '8080'))
        
    def _get_bot_token(self) -> str:
        """Get bot token from environment variables."""
        token = os.getenv('BOT_TOKEN')
        if not token:
            raise ValueError(
                "BOT_TOKEN environment variable is required! "
                "Get your token from @BotFather on Telegram"
            )
        return token

    @property
    def is_webhook_mode(self) -> bool:
        """Check if webhook mode is enabled."""
        return bool(self.webhook_url)
