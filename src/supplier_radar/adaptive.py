from __future__ import annotations

from collections import Counter
import re

STOP={"производитель","производство","оптом","оптовый","пушкино","москва","московская","область","купить","цена","каталог","официальный","сайт","компания","товары","продукция"}
WORD=re.compile(r"[а-яёa-z][а-яёa-z-]{3,}",re.I)


def discovery_seeds(history: dict[str,dict], limit: int=20) -> list[str]:
    counter=Counter()
    strong=sorted(history.values(),key=lambda x:int(x.get("best_score") or 0),reverse=True)[:200]
    for row in strong:
        if int(row.get("best_score") or 0)<50: continue
        text=f"{row.get('title','')} {row.get('snippet','')} {row.get('category','')}".lower()
        for word in WORD.findall(text):
            if word not in STOP and len(word)<35: counter[word]+=1
    return [w for w,n in counter.most_common(limit) if n>=2]


def adaptive_queries(history: dict[str,dict], regions: list[str], limit: int=20) -> list[dict]:
    seeds=discovery_seeds(history,limit=limit); out=[]
    region=regions[0] if regions else "Пушкино"
    templates=("{seed} производитель оптом {region}","{seed} контрактное производство СТМ {region}","{seed} прайс опт Московская область")
    for seed in seeds:
        for t in templates:
            out.append({"branch":"S_adaptive","query":t.format(seed=seed,region=region),"region":region,"category":seed})
            if len(out)>=limit: return out
    return out
