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
    """Валидация с частичным восстановлением."""
    logger.info("🛡️ Запуск Pydantic-валидации и контроля математики...")

    clean_data = None

    try:
        # Пробуем проглотить целиком
        validated_order = OrderData(**order_data)
        clean_data = validated_order.model_dump()
    except ValidationError as e:
        logger.error(f"❌ Обнаружены ошибки структуры JSON. Запуск спасательной операции...")

        # Спасаем то, что можем
        fixed_data = {
            'deceased': order_data.get('deceased', {}),
            'customer': order_data.get('customer', {}),
            'services': [],
            'warnings': order_data.get('warnings', [])
        }

        for s in order_data.get('services', []):
            try:
                service = Service(**s)
                fixed_data['services'].append(service.model_dump())
            except ValidationError as se:
                bad_name = s.get('name', 'НЕИЗВЕСТНО')
                logger.warning(f"⚠️ Услуга '{bad_name}' вырезана: {se.errors()[0]['msg']}")
                fixed_data['warnings'].append(f"Удалена некорректная услуга: {bad_name}")

        try:
            fallback = OrderData(**fixed_data)
            clean_data = fallback.model_dump()
        except Exception as final_e:
            logger.error(f"❌ Фатальная ошибка спасения: {final_e}")
            clean_data = OrderData(deceased=Deceased(), customer=Customer(), services=[]).model_dump()

    # Фикс даты смерти
    if not clean_data["deceased"]["death_date"]:
        today_str = datetime.now().strftime("%d.%m.%Y")
        clean_data["deceased"]["death_date"] = today_str
        logger.warning(f"⚠️ Дата смерти пустая! Подставил сегодняшнюю: {today_str}")

    # Математический контроль
    real_total = sum(svc["price"] * svc["quantity"] for svc in clean_data["services"])
    agent_total = clean_data.get("calculated_total", 0)

    if real_total != agent_total:
        logger.warning(f"⚠️ Математика пересчитана. Было: {agent_total}, Стало: {real_total}")
        clean_data["calculated_total"] = real_total
    else:
        logger.info(f"✅ Математика сошлась: {real_total} грн.")

    return clean_data