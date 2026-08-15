from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message
from tgbot.config import load_config
from tgbot.filters.admin import AdminFilter
from tgbot.keyboards.inline import first_start_keyboard

admin_router = Router()
admin_router.message.filter(AdminFilter())
config = load_config()
ADMINS = config.tg_bot.admin_ids

@admin_router.message(CommandStart())
async def admin_start(message: Message):
    try:
        await message.delete()
    except Exception as e:
        print(f"⚠️ Ошибка удаления сообщения: {e}")
    await message.answer(
        f"""🏋️‍♂️Привет Сём,  так выглядит обложка твоего бота!🏋️‍♂️

    Значит твоя цель — превратить твои мечты о теле, здоровье и энергии в реальные достижения. 
    Мой бот построит не просто план, а целый стиль жизни, который будет работать на тебя.
    Все подробности во вкладке ℹ️Информация по разделамℹ️

    P.S. и еще больше полезного контента в каналах 👇🏻
    IG : @smngrsmv_fit
    TG : https://t.me/smngrsmv
    """,
        reply_markup=first_start_keyboard()
    )

def is_admin(user_id):
    return user_id in ADMINS
