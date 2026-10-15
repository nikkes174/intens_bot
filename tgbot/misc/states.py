from aiogram.fsm.state import State, StatesGroup


class City(StatesGroup):
    choice = State()


class Toy(StatesGroup):
    choice = State()
