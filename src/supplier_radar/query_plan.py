from __future__ import annotations

from dataclasses import dataclass
import random
from typing import Iterable


@dataclass(frozen=True)
class PlannedQuery:
    branch: str
    query: str
    region: str = ""
    category: str = ""


DIRECT_TEMPLATES = (
    "{category} оптом производитель склад {region}",
    "{category} оптовая компания дистрибьютор {region}",
    "{category} производитель самовывоз со склада {region}",
)

BRANCH_TEMPLATES: dict[str, tuple[str, ...]] = {
    "B_brand_legal": (
        "{category} бренд производитель ООО {region}",
        "{category} официальный дистрибьютор склад {region}",
    ),
    "D_industrial": (
        "промышленный парк {region} арендаторы производители",
        "промзона {region} склад производство опт",
    ),
    "E_neighbors": (
        "{category} оптом склад {region} доставка Пушкино",
        "{category} производитель {region} отгрузка Пушкино",
    ),
    "F_catalogs": (
        "{category} опт прайс pdf {region}",
        "{category} опт каталог xls {region}",
    ),
    "G_directories": (
        "{category} оптовая компания {region} адрес телефон",
        "{category} производитель {region} контакты склад",
    ),
    "J_legal_okved": (
        "{category} ООО {region} производство ИНН",
        "ОКВЭД производство {category} {region}",
    ),
    "K_declarations": (
        "{category} декларация соответствия производитель {region}",
        "{category} сертификат изготовитель Московская область {region}",
    ),
    "L_trademarks": (
        "{category} товарный знак производитель {region}",
    ),
    "M_vacancies": (
        "{category} производство склад вакансия {region} компания",
        "упаковщик производство {category} вакансия {region}",
    ),
    "N_real_estate": (
        "склад {region} арендаторы производители {category}",
        "производственно складской комплекс {region} арендаторы",
    ),
    "O_associations": (
        "{category} выставка участники производитель Московская область",
        "ассоциация производителей {category} Московская область",
    ),
    "P_procurement": (
        "поставка {category} Пушкино ООО поставщик",
        "{category} поставщик закупка {region}",
    ),
    "Q_logistics": (
        "{category} \"отгрузка со склада\" {region}",
        "{category} \"самовывоз со склада\" {region}",
    ),
    "R_distribution": (
        "{category} контрактное производство {region}",
        "{category} официальный дилер дистрибьютор {region}",
    ),
}


def _dedupe(items: Iterable[PlannedQuery]) -> list[PlannedQuery]:
    seen: set[str] = set()
    out: list[PlannedQuery] = []
    for item in items:
        key = " ".join(item.query.lower().split())
        if key and key not in seen:
            seen.add(key)
            out.append(item)
    return out


def build_query_plan(config: dict, *, slot: int, limit: int) -> list[PlannedQuery]:
    regions = list(config.get("regions") or [])
    categories = list(config.get("categories") or [])
    addresses = list(config.get("address_clusters") or [])
    seeds = list(config.get("seed_entities") or [])

    priority: list[PlannedQuery] = []
    # Large runs should deepen coverage, not just rotate one direct wording.
    for direct_template in DIRECT_TEMPLATES:
        for region in regions:
            for category in categories:
                priority.append(
                    PlannedQuery(
                        branch="A_direct",
                        query=direct_template.format(category=category, region=region),
                        region=region,
                        category=category,
                    )
                )

    for address in addresses:
        priority.append(PlannedQuery("C_address_cluster", f'"{address}" склад опт производитель'))
        priority.append(PlannedQuery("C_address_cluster", f'"{address}" компания производство'))

    reverse_variants = ("склад опт", "производитель дистрибьютор", "ИНН адрес")
    for entity in seeds:
        for suffix in reverse_variants:
            priority.append(PlannedQuery("H_reverse_entity", f'"{entity}" {suffix}'))

    secondary: list[PlannedQuery] = []
    for branch, templates in BRANCH_TEMPLATES.items():
        for template in templates:
            for region in regions:
                if "{category}" in template:
                    for category in categories:
                        query = template.format(category=category, region=region)
                        secondary.append(PlannedQuery(branch, query, region, category))
                else:
                    query = template.format(region=region)
                    secondary.append(PlannedQuery(branch, query, region, ""))

    rng = random.Random(slot)
    rng.shuffle(secondary)
    return _dedupe([*priority, *secondary])[: max(0, limit)]
