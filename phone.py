import phonenumbers


def normalize_phone(raw_phone: str, country_code: str) -> str | None:
    """Приводит номер к международному формату (+код страны ...).

    Возвращает None, если номер невалиден или отсутствует.
    """
    if not raw_phone:
        return None

    try:
        parsed = phonenumbers.parse(raw_phone, country_code)
    except phonenumbers.NumberParseException:
        return None

    if not phonenumbers.is_valid_number(parsed):
        return None

    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


def is_mobile(e164: str) -> bool:
    """Мобильный ли номер (по нему можно писать в мессенджеры, городской — нет)."""
    try:
        parsed = phonenumbers.parse(e164, None)
    except phonenumbers.NumberParseException:
        return False
    return phonenumbers.number_type(parsed) in (
        phonenumbers.PhoneNumberType.MOBILE,
        phonenumbers.PhoneNumberType.FIXED_LINE_OR_MOBILE,
    )


def pick_mobile(raw_phones: list[str], country_code: str) -> str | None:
    """Из нескольких телефонов компании берёт первый валидный мобильный."""
    for raw in raw_phones:
        phone = normalize_phone(raw, country_code)
        if phone and is_mobile(phone):
            return phone
    return None
