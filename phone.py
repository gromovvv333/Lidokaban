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
