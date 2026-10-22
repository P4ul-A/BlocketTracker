"""Conservative tyre inclusion rules over original Swedish/English advert text.

Equipment lists describe included inventory unless the seller denies inclusion or
offers it at extra cost. Unknown wording and contradictory statements stay
unknown. These rules deliberately do not infer tyres from AWD or wheel bearings.
"""

import re


WHEEL = r"(?:(?:däck|hjul)uppsättning(?:ar)?|däck(?:en|et)?|hjul(?:en|et)?|tyres?|tires?|wheels?)"
SEASON = r"(?:sommar|vinter|summer|winter)"
PAID = (
    r"köp(?:a|as)?\s+till|tillköp|mot\s+(?:en\s+)?(?:extra\s+)?(?:kostnad|tillägg)|"
    r"kostar\s+extra|extra\s+(?:kostnad|avgift)|sälj(?:e)?s\s+separat|köp(?:a|as)?\s+separat|"
    r"(?:förmånliga|bra)\s+(?:paket)?priser|extra\s+charge|additional\s+cost|sold\s+separately|"
    r"(?:vid\s+köp\s+av|innehåller).*trygghetspaket|trygghetspaket.*innehåller|"
    r"vid\s+paketering|rabatteras"
)


def normalize_tyres(text):
    """Expand seasonal abbreviations for matching without changing saved quotes."""
    text = " ".join(text.casefold().split())
    text = re.sub(r"\bs\s*[-/]?\s*(?:och|&|o|/)\s*v\s*[- ]*" + WHEEL,
                  "sommarhjul vinterhjul", text)
    text = re.sub(r"\bv\s*[-/]?\s*(?:och|&|o|/)\s*s\s*[- ]*" + WHEEL,
                  "vinterhjul sommarhjul", text)
    text = re.sub(r"\b(sommar|vinter)\s*-?\s*(?:och|samt|&|o|/)\s*(?:dubbade\s+|dubbfria\s+)?(sommar|vinter)\s*[- ]*(däck|hjul)",
                  r"\1\3 \2\3", text)
    text = re.sub(r"\bsommar\s+(?:och|samt|&)\s+(dubbdäck|friktionsdäck)",
                  r"sommarhjul \1", text)
    text = re.sub(r"\bs\s*-\s*(däck|hjul)", r"sommar\1", text)
    return re.sub(r"\bv\s*-\s*(däck|hjul)", r"vinter\1", text)


def tyre_clauses(sentence):
    """Separate independent offers while retaining qualifiers after a tyre name.

    In particular, keep 'Vinterdäck, använda en säsong, går att köpa till'
    together, but separate 'Sommarhjul ingår, vinterhjul kostar extra'.
    """
    text = normalize_tyres(sentence)
    text = re.sub(r"\b(ingår|medföljer)\s+och\s+(?=" + SEASON + r")", r"\1, ", text)
    parts = re.split(r"[,;]|\b(?:men|but)\b|\boch\s+(?=inga\s+" + SEASON + r")", text)
    clauses = []
    for part in parts:
        part = part.strip()
        qualifier = re.match(r"(?:använda\b|på\b|går\s+att\s+köpa|finns\s+att\s+köpa|"
                             r"kostar\b|ingår\b|medföljer\b|mot\s+tillägg|\d[\d ]*\s*(?:kr|sek|:-))", part)
        if qualifier and clauses and any(tyre_mentions(clauses[-1])) and not any(tyre_mentions(part)):
            clauses[-1] += ", " + part
        else:
            clauses.append(part)
    return clauses


def tyre_mentions(clause):
    """Identify seasonal tyres and explicit additional sets with word boundaries."""
    winter = bool(re.search(r"\b(?:vinter[ -]*" + WHEEL
                            + r"|winter[ -]+" + WHEEL
                            + r"|dubbdäck(?:en)?|friktionsdäck(?:en)?)\b", clause))
    summer = bool(re.search(r"\b(?:sommar[ -]*" + WHEEL
                            + r"|summer[ -]+" + WHEEL + r")\b", clause))
    additional = bool(re.search(
        r"\b(?:extra|ytterligare|andra|additional|second)\s+(?:\w+[ -]+){0,3}"
        + r"(?:" + WHEEL + r"|uppsättning(?:ar)?|sets?)\b"
        + r"|\b(?:två|2|two)\s+(?:uppsättningar|sets)\b", clause))
    return winter, summer, additional


def tyre_facts(sentences):
    """Resolve included tyre inventory with verbatim, possibly multiple quotes.

    Scan all source sentences so omissions by the LLM do not hide clear seller
    statements. Two included seasonal sets establish an additional set. Opposing
    statements about a field remain unknown instead of choosing one arbitrarily.
    A separately priced offer never establishes included inventory.
    """
    findings = {key: [] for key in ("winter_tires", "summer_tires", "extra_tires")}

    def record(key, value, quotes, reason):
        """Retain the decision and its original evidence for final reconciliation."""
        findings[key].append((value, quotes, reason))

    for index, sentence in enumerate(sentences):
        for clause in tyre_clauses(sentence):
            winter, summer, additional = tyre_mentions(clause)
            generic = bool(re.search(r"\b" + WHEEL + r"\b", clause))
            no_other = bool(re.search(r"\b(?:inga\s+(?:andra|extra)|no\s+(?:other|extra))\s+" + WHEEL, clause))
            if not (winter or summer or additional or no_other):
                continue
            # A shared all-season set is not two separate seasonal inventories.
            if re.search(r"\b(?:allround|åretrunt|året\s+runt|all[ -]season|samma\s+(?:däck|uppsättning))", clause):
                continue
            quotes = [sentence]
            # A standalone following qualifier may complete a tyre inventory line.
            if index + 1 < len(sentences):
                following = normalize_tyres(sentences[index + 1])
                if re.match(r"(?:ingår\s+(?:inte|ej)|medföljer\s+(?:inte|ej)|"
                            r"(?:finns\s+att\s+)?köpa\s+till|mot\s+tillägg|not\s+included)\b", following):
                    clause += " " + following
                    quotes.append(sentences[index + 1])
            paid = bool(re.search(PAID, clause) or re.search(
                r"\b" + WHEEL + r"\s*(?:på\s+fälg\s*)?(?:kostar\s+|från\s+|[:=-]\s*)?"
                + r"\d[\d ]*\s*(?:kr\b|sek\b|:-)", clause))
            tyre_name = r"\b(?:" + SEASON + r"[ -]*)?" + WHEEL + r"\b"
            denied = bool(re.search(
                r"\b(?:inga|inget|utan|no|without)\s+(?:\w+[ -]+){0,2}(?:" + SEASON + r"[ -]*)?" + WHEEL + r"\b"
                + r"|" + tyre_name + r"\s*[:,]?\s*(?:(?:ingår|medföljer|inkluderas|finns)\s+(?:inte|ej)|saknas|not\s+included)\b"
                + r"|\b(?:saknar|utan|(?:ingår|medföljer)\s+(?:inte|ej))\s+(?:några\s+)?" + tyre_name, clause))
            explicit_inclusion = bool(re.search(r"\b(?:ingår|medföljer|följer\s+med|included|levereras\s+med)\b", clause))
            uncertain = bool(re.search(
                r"\b(?:erbjuder|kan\s+ordna|möjlighet\s+till|vid\s+köp|om\s+du|if\s+you|"
                r"tidigare\s+ingick|hade\s+tidigare|kommer\s+(?:att\s+)?behövas|"
                r"behöver\s+(?:köpa|skaffa)|måste\s+köpas)\b|\?", clause))
            # An unnamed 'extra set' alone need not refer to tyres.
            if not (generic or winter or summer):
                continue
            if no_other:
                record("extra_tires", False, quotes, "Seller explicitly excludes other tyre sets.")
                continue
            if not paid and not denied and uncertain and not explicit_inclusion:
                continue
            value = not (paid or denied)
            reason = ("Tyres are offered at extra cost." if paid else
                      "Seller explicitly excludes these tyres." if denied else
                      "Tyres are listed as included inventory.")
            for key, mentioned in (("winter_tires", winter), ("summer_tires", summer),
                                   ("extra_tires", additional or (winter and summer))):
                if mentioned:
                    record(key, value, quotes, reason)
            if paid and (winter or summer):
                record("extra_tires", False, quotes, reason)

    facts = {}
    for key, candidates in findings.items():
        values = {value for value, _, _ in candidates}
        value = next(iter(values)) if len(values) == 1 else None
        quotes = list(dict.fromkeys(quote for _, evidence, _ in candidates for quote in evidence))
        facts[key] = {"value": value, "evidence": "\n".join(quotes),
                      "evidence_sentences": quotes, "source": "rule",
                      "reason": ("Conflicting tyre statements; inclusion is unknown." if len(values) > 1
                                 else candidates[0][2] if candidates else "No supported tyre statement.")}
    winter, summer = facts["winter_tires"], facts["summer_tires"]
    if winter["value"] is True and summer["value"] is True:
        quotes = list(dict.fromkeys(winter["evidence_sentences"] + summer["evidence_sentences"]))
        excludes_others = any(not value and reason == "Seller explicitly excludes other tyre sets."
                              for value, _, reason in findings["extra_tires"])
        if excludes_others:
            quotes = list(dict.fromkeys(quotes + facts["extra_tires"]["evidence_sentences"]))
        facts["extra_tires"] = {"value": None if excludes_others else True, "evidence": "\n".join(quotes),
                                "evidence_sentences": quotes, "source": "rule",
                                "reason": ("Conflicting tyre statements; inclusion is unknown." if excludes_others
                                           else "Both summer and winter tyre sets are included.")}
    return facts
