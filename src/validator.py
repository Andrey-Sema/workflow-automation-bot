import logging
from datetime import datetime
from pydantic import BaseModel, Field, field_validator, ValidationError, ConfigDict

logger = logging.getLogger(__name__)


class Deceased(BaseModel):
    fio: str = Field(default="НЕ УКАЗАНО")
    birth_date: str = Field(default="01.01.1920")
    death_date: str = Field(default="")
    burial_date: str = Field(default="")
    cemetery: str = Field(default="")


class Customer(BaseModel):
    fio: str = Field(default="")
    phone: str = Field(default="")


class Service(BaseModel):
    # Разрешаем создавать модель и по реальному имени переменной, и по алиасу
    model_config = ConfigDict(populate_by_name=True)

    name: str
    price: int
    quantity: int = Field(default=1)

    # Алиас позволяет Pydantic прочитать '1c_down_presses' из JSON
    c_down_presses: int = Field(default=0, alias="1c_down_presses")

    @field_validator('price', 'quantity')
    @classmethod
    def must_be_positive(cls, v):
        if v < 0:
            raise ValueError('Значение не может быть отрицательным')
        return v


class OrderData(BaseModel):
    deceased: Deceased
    customer: Customer
    services: list[Service] = Field(default_factory=list)
    goods: list[Service] = Field(default_factory=list)
    transport: list[Service] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    handwritten_total: int = Field(default=0)
    calculated_total: int = Field(default=0)


def validate_and_fix_order(order_data: dict) -> dict:
    logger.info("🛡️ Запуск Pydantic-валидации и контроля математики...")
    clean_data = None

    try:
        validated_order = OrderData(**order_data)
        # by_alias=True обязательно, чтобы в итоговом словаре ключ назывался '1c_down_presses'
        clean_data = validated_order.model_dump(by_alias=True)
    except ValidationError as e:
        logger.error("❌ Обнаружены ошибки структуры JSON. Запуск спасательной операции...")

        fixed_data = {
            'deceased': order_data.get('deceased', {}),
            'customer': order_data.get('customer', {}),
            'services': [],
            'goods': [],
            'transport': [],
            'warnings': order_data.get('warnings', []),
            'handwritten_total': order_data.get('handwritten_total', 0)
        }

        for cat in ['services', 'goods', 'transport']:
            for s in order_data.get(cat, []):
                try:
                    service = Service(**s)
                    fixed_data[cat].append(service.model_dump(by_alias=True))
                except ValidationError as se:
                    bad_name = s.get('name', 'НЕИЗВЕСТНО')
                    logger.warning(f"⚠️ Позиция '{bad_name}' вырезана: {se.errors()[0]['msg']}")
                    fixed_data['warnings'].append(f"Удалена некорректная позиция: {bad_name}")

        try:
            fallback = OrderData(**fixed_data)
            clean_data = fallback.model_dump(by_alias=True)
        except Exception as final_e:
            logger.error(f"❌ Фатальная ошибка спасения: {final_e}")
            clean_data = OrderData(deceased=Deceased(), customer=Customer()).model_dump(by_alias=True)

    # Фикс даты смерти
    if not clean_data["deceased"]["death_date"]:
        today_str = datetime.now().strftime("%d.%m.%Y")
        clean_data["deceased"]["death_date"] = today_str
        logger.warning(f"⚠️ Дата смерти пустая! Подставил сегодняшнюю: {today_str}")

    # Пересчитываем математику по всем трем массивам
    real_total = 0
    for cat in ['services', 'goods', 'transport']:
        real_total += sum(item["price"] * item["quantity"] for item in clean_data.get(cat, []))

    clean_data["calculated_total"] = real_total

    return clean_data