import os

from dotenv import load_dotenv

load_dotenv()

DB_URL = os.getenv('DB')

TOKEN = os.getenv('BOT_TOKEN')

YOOKASSA_SHOP_ID = os.getenv('YOOKASSA_SHOP_ID')

YOOKASSA_SECRET = os.getenv('YOOKASSA_SECRET_KEY')

PAYMENT_AMOUNT = os.getenv('PAYMENT_AMOUNT')
