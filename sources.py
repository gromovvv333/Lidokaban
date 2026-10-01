import scraper
import scraper_2gis


def run(source: str, query: str, max_leads: int, russian: bool = False, country_code: str = "",
        type_filter: str = ""):
    if source == "2gis":
        # код страны нужен для выбора домена (2gis.kz / 2gis.ru)
        return scraper_2gis.run(
            query, max_leads=max_leads, russian=russian, country_code=country_code,
            type_filter=type_filter,
        )
    return scraper.run(query, max_leads=max_leads, russian=russian)
