import asyncio
import uuid
import os
from typing import Optional

from aiogram import Bot
from yookassa import Payment, Configuration
from dotenv import load_dotenv

# Загружаем .env
load_dotenv()

# Конфигурация Yookassa
YOOKASSA_SHOP_ID = os.getenv("YOOKASSA_SHOP_ID")
YOOKASSA_SECRET_KEY = os.getenv("YOOKASSA_SECRET_KEY")
Configuration.account_id = YOOKASSA_SHOP_ID
Configuration.secret_key = YOOKASSA_SECRET_KEY

RETURN_URL = "https://t.me/PROJECT_XTgBot"

# --- .env chat_id управление ---

ENV_PATH = ".env"
ENV_KEY = "CHANNEL_CHAT_ID"

def save_chat_id_to_env(chat_id: int):
    try:
        if os.path.exists(ENV_PATH):
            with open(ENV_PATH, "r") as f:
                lines = f.readlines()
        else:
            lines = []

        for i, line in enumerate(lines):
            if line.startswith(f"{ENV_KEY}="):
                lines[i] = f"{ENV_KEY}={chat_id}\n"
                break
        else:
            lines.append(f"{ENV_KEY}={chat_id}\n")

        with open(ENV_PATH, "w") as f:
            f.writelines(lines)

        print("✅ chat_id сохранён в .env")
    except Exception as e:
        print(f"❌ Не удалось сохранить chat_id в .env: {e}")

async def get_channel_chat_id(bot: Bot) -> Optional[int]:
    env_chat_id = os.getenv(ENV_KEY)
    if env_chat_id:
        try:
            chat_id = int(env_chat_id)
            print(f"📦 chat_id получен из .env: {chat_id}")
            return chat_id
        except ValueError:
            print("⚠️ Некорректный формат CHANNEL_CHAT_ID в .env")

    try:
        chat = await bot.get_chat("@iprojectXekb")  # Замените на ваш username канала
        chat_id = chat.id
        save_chat_id_to_env(chat_id)
        print(f"📡 chat_id получен через API: {chat_id}")
        return chat_id
    except Exception as e:
        print(f"❌ Ошибка при получении chat_id: {e}")
        return None

# --- Платёж ---

def create_payment(user_id: int, months: int = 1):
    payment_id = str(uuid.uuid4())
    amount = 1  # RUB
    description = f"Покупка подписки пользователя {user_id}"

    payment = Payment.create({
        "amount": {
            "value": f"{amount:.2f}",
            "currency": "RUB"
        },
        "confirmation": {
            "type": "redirect",
            "return_url": RETURN_URL
        },
        "capture": True,
        "description": description,
        "metadata": {
            "user_id": str(user_id),
            "months": str(months)
        },
        "receipt": {
            "customer": {
                "full_name": f"{user_id}",
                "email": f"user{user_id}@yourvpn.com"
            },
            "items": [
                {
                    "description": f"Подписка на {months} мес.",
                    "quantity": "1.00",
                    "amount": {
                        "value": f"{amount:.2f}",
                        "currency": "RUB"
                    },
                    "vat_code": 1,
                    "payment_mode": "full_payment",
                    "payment_subject": "service"
                }
            ]
        }
    })

    return payment.id, payment.confirmation.confirmation_url

def check_payment_status(payment_id):
    try:
        payment = Payment.find_one(payment_id)
        return payment.status, payment.metadata
    except Exception as e:
        print(f"❌ Ошибка при проверке платежа: {e}")
        return None, None

# --- Генерация ссылки и цикл проверки ---

async def generate_one_time_invite(bot: Bot, user_id: int):
    try:
        chat_id = await get_channel_chat_id(bot)
        if not chat_id:
            return None

        invite_link_obj = await bot.create_chat_invite_link(
            chat_id=chat_id,
            member_limit=1,
        )
        return invite_link_obj.invite_link
    except Exception as e:
        print(f"❌ Ошибка при генерации ссылки: {e}")
        return None

async def check_payment_loop(payment_id, user_id, username, bot: Bot, payment_message_id: int):
    for _ in range(10):
        await asyncio.sleep(30)
        status, metadata = check_payment_status(payment_id)

        if status == "succeeded":
            print(f"✅ Оплата от {user_id} прошла успешно")

            invite_link = await generate_one_time_invite(bot, user_id)
            if invite_link:
                await bot.send_message(
                    user_id,
                    (
                        "👥 Доступ к эксклюзивному каналу открыт!\n\n"
                        f"👉 Для перехода в канал, нажмите на ссылку: {invite_link}"
                    )
                )

            try:
                await bot.delete_message(
                    chat_id=user_id,
                    message_id=payment_message_id
                )
            except Exception as e:
                print(f"⚠️ Ошибка при удалении сообщения с оплатой: {e}")

            return

        elif status == "canceled":
            await bot.send_message(user_id, "❌ Оплата была отменена.")
            return

    await bot.send_message(user_id, "⏳ Оплата не завершена за 5 минут. Попробуйте позже.")



