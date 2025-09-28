import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Telegram Bot Configuration
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')

# AccessTrade API Configuration
ACCESSTRADE_TOKEN = os.getenv('ACCESSTRADE_TOKEN')

# Bot Instance ID
BOT_INSTANCE_ID = os.getenv('BOT_INSTANCE_ID', 'default')

# Validate required tokens
if not TELEGRAM_BOT_TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN is required! Please set it in .env file or environment variables.")

if not ACCESSTRADE_TOKEN:
    raise ValueError("ACCESSTRADE_TOKEN is required! Please set it in .env file or environment variables.")
