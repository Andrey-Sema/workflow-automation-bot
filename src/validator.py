import logging
from datetime import datetime
from pydantic import BaseModel, Field, field_validator, ValidationError, ConfigDict
from src.utils import fix_temporal_hallucinations

logger = logging.getLogger(__name__)


class Deceased(BaseModel):
    fio: str = Field(default="НЕ УКАЗАНО")
    birth_date: str = Field(default="01.01.1920")
    death_date: str = Field(default="")
    burial_date: str = Field(default="")
    cemetery: str = Field(default="")

    # Магія Pydantic: автоматично пропускаємо дати через наш щит
    @field_validator('death_date', 'burial_date')
    def clean_date_format(cls, v: str) -> str:
        return fix_temporal_hallucinations(v)


class Customer(BaseModel):
    fio: str = Field(default="")
    phone: str = Field(default="")


class Service(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str
    price: int
    quantity: int = Field(default=1)
    unit_price_for_1c: int = Field(default=0)

    search_key: str = Field(default="", alias="1c_search_key")
    c_down_presses: int = Field(default=0, alias="1c_down_presses")

    @field_validator('price', 'quantity')
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
    logger.info("🛡️ Запуск Pydantic-валидации...")

    try:
        validated_order = OrderData(**order_data)
        clean_data = validated_order.model_dump(by_alias=True)
    except ValidationError as e:
        logger.error(f"❌ Ошибка структуры JSON: {e}")

        fixed_data = {
            'deceased': order_data.get('deceased', {}),
            'customer': order_data.get('customer', {}),
            'services': [], 'goods': [], 'transport': [],
            'warnings': order_data.get('warnings', []),
            'handwritten_total': order_data.get('handwritten_total', 0)
        }

        # Спасаем Deceased, чтобы применился fix_temporal_hallucinations
        try:
            fixed_data['deceased'] = Deceased(**fixed_data['deceased']).model_dump()
        except (ValidationError, TypeError):
            pass

        for cat in ['services', 'goods', 'transport']:
            for s in order_data.get(cat, []):
                try:
                    fixed_data[cat].append(Service(**s).model_dump(by_alias=True))
                except (ValidationError, TypeError):
                    continue

        clean_data = OrderData(**fixed_data).model_dump(by_alias=True)

    # Фикс даты смерти по умолчанию
    if not clean_data["deceased"]["death_date"]:
        clean_data["deceased"]["death_date"] = datetime.now().strftime("%d.%m.%Y")

    # --- ФИНАЛЬНАЯ МАТЕМАТИКА ---
    real_total = 0
    for cat in ['services', 'goods', 'transport']:
        real_total += sum(item.get("price", 0) for item in clean_data.get(cat, []))

    clean_data["calculated_total"] = real_total
    return clean_data