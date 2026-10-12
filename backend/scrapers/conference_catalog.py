"""Conference registry shared by scraper dispatch, API validation and UI metadata.

Add a venue here to reuse an existing proceedings source and expose it in the UI.
New proceedings formats require a corresponding adapter in conference_papers.py.
"""
from datetime import datetime
from enum import Enum

CONFERENCE_REGISTRY = {
    'iclr': {'name': 'ICLR', 'source': 'virtual'},
    'icml': {'name': 'ICML', 'source': 'virtual'},
    'neurips': {'name': 'NeurIPS', 'source': 'virtual'},
    'aaai': {'name': 'AAAI', 'source': 'aaai'},
    'ijcai': {'name': 'IJCAI', 'source': 'ijcai'},
    'cvpr': {'name': 'CVPR', 'source': 'cvf'},
    'iccv': {'name': 'ICCV', 'source': 'cvf', 'year_parity': 1},
    'eccv': {'name': 'ECCV', 'source': 'ecva', 'year_parity': 0},
    'acl': {'name': 'ACL', 'source': 'anthology'},
    'emnlp': {'name': 'EMNLP', 'source': 'anthology'},
    'naacl': {'name': 'NAACL', 'source': 'anthology'},
    'aistats': {'name': 'AISTATS', 'source': 'pmlr'},
    'uai': {'name': 'UAI', 'source': 'pmlr'},
    'colt': {'name': 'COLT', 'source': 'pmlr'},
    'corl': {'name': 'CoRL', 'source': 'pmlr'},
}
CONFERENCES = {key: entry['name'] for key, entry in CONFERENCE_REGISTRY.items()}
ConferenceVenue = Enum('ConferenceVenue', {'all': 'all', **{key: key for key in CONFERENCES}}, type=str)


def held_in_year(venue: str, year: int) -> bool:
    parity = CONFERENCE_REGISTRY[venue].get('year_parity')
    return parity is None or year % 2 == parity


def conference_catalog() -> list[dict]:
    return [
        {'id': key, 'name': name,
         'years': [year for year in range(datetime.now().year, 2019, -1) if held_in_year(key, year)]}
        for key, name in CONFERENCES.items()
    ]
