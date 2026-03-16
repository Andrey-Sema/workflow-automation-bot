import logging
from datetime import datetime
from pydantic import BaseModel, Field, field_validator, ValidationError

logger = logging.getLogger(__name__)


# --- PYDANTIC СХЕМЫ ---
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
    name: str
    price: int
    quantity: int = Field(default=1)

    @field_validator('price', 'quantity')
    def must_be_positive(cls, v):
        if v < 0:
            raise ValueError('Значение не может быть отрицательным')
        return v


class OrderData(BaseModel):
    deceased: Deceased
    customer: Customer
    services: list[Service]
    warnings: list[str] = []
    calculated_total: int = 0


# --- БИЗНЕС-ЛОГИКА ВАЛИДАЦИИ ---
def validate_and_fix_order(order_data: dict) -> dict:
    logger.info("🛡️ Запуск Pydantic-валидации и контроля математики...")

    # 1. Жесткая валидация структуры (Pydantic)
    try:
        validated_order = OrderData(**order_data)
        clean_data = validated_order.model_dump()  # Превращаем обратно в чистый dict
    except ValidationError as e:
        logger.error(f"❌ КРИТИЧЕСКАЯ ОШИБКА СТРУКТУРЫ JSON! LLM сошла с ума:\n{e}")
        # Генерируем пустой, но валидный скелет данных, чтобы ничего не ебнулось дальше
        fallback_order = OrderData(
            deceased=Deceased(),
            customer=Customer(),
            services=[]
        )
        return fallback_order.model_dump()

    # 2. Фикс даты смерти
    if not clean_data["deceased"]["death_date"]:
        today_str = datetime.now().strftime("%d.%m.%Y")
        clean_data["deceased"]["death_date"] = today_str
        logger.warning(f"⚠️ Дата смерти пустая! Подставил сегодняшнюю: {today_str}")

    # 3. Математический контроль
    real_total = sum(svc["price"] * svc["quantity"] for svc in clean_data["services"])
    agent_total = clean_data.get("calculated_total", 0)

    if real_total != agent_total:
        logger.warning(f"⚠️ ВНИМАНИЕ! Математика пересчитана. Было: {agent_total}, Стало: {real_total}")
        clean_data["calculated_total"] = real_total
    else:
        logger.info(f"✅ Математика сошлась: {real_total} грн.")

    return clean_data