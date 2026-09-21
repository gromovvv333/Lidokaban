import scraper
import scraper_2gis

SOURCES = {"google": scraper.run, "2gis": scraper_2gis.run}


def run(source: str, query: str, max_leads: int):
    return SOURCES[source](query, max_leads=max_leads)
