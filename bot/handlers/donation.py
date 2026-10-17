from aiogram import Router, F
from aiogram.types import Message, PreCheckoutQuery, LabeledPrice
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext

from bot import storage

router = Router(name="donation_router")


@router.message(F.text == "⭐ Поддержать")
@router.message(Command("donate"))
async def handle_donate_button(message: Message, state: FSMContext) -> None:
    await state.clear()
    title = "Поддержка проекта"
    description = "Ваше пожертвование поможет поддерживать работу и развитие бота НТЭК."
    payload = "donation-stars"
    currency = "XTR"
    prices = [LabeledPrice(label="Звезды Telegram", amount=20)]

    try:
        await message.bot.send_invoice(
            chat_id=message.chat.id,
            title=title,
            description=description,
            payload=payload,
            currency=currency,
            prices=prices,
            provider_token="",  # Для валюты XTR (Telegram Stars) токен провайдера пустой
        )
    except Exception as e:
        print(f"Ошибка выставления счета Telegram Stars: {e}")
        await message.answer("❌ В данный момент не удалось сформировать счет. Попробуйте позже.")


@router.pre_checkout_query()
async def process_pre_checkout_query(pre_checkout: PreCheckoutQuery) -> None:
    await pre_checkout.answer(ok=True)


@router.message(F.successful_payment)
async def process_successful_payment(message: Message) -> None:
    user_id = message.from_user.id
    stars_amount = message.successful_payment.total_amount
    await message.answer(f"🎉 Спасибо огромное за поддержку проекта в размере {stars_amount} 🌟!")

    # Уведомление администраторам
    display_name = storage.user_names_data.get(str(user_id), f"ID {user_id}")
    for admin_id in storage.admin_ids:
        try:
            await message.bot.send_message(
                chat_id=int(admin_id),
                text=f"💰 Получен донат: <b>{stars_amount} 🌟</b> от пользователя {display_name} (<code>{user_id}</code>)",
            )
        except Exception:
            pass
