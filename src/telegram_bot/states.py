from aiogram.fsm.state import State, StatesGroup


class OrderFlow(StatesGroup):
    collecting = State()          # receiving photos/PDFs for the current order
    choosing_addresses = State()  # waiting for the "how many extra addresses" button
    confirming = State()          # summary shown, waiting for ✅/❌
