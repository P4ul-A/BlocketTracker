"""Conservative tyre inclusion rules over original Swedish/English advert text.

Equipment lists describe included inventory unless the seller denies inclusion or
offers it at extra cost. Unknown wording and contradictory statements stay
unknown. These rules deliberately do not infer tyres from AWD or wheel bearings.
"""

import re


WHEEL = r"(?:däck(?:en|et)?|hjul(?:en|et)?|tyres?|tires?|wheels?)"
SEASON = r"(?:sommar|vinter|summer|winter)"
PAID = (
    r"köp(?:a|as)?\s+till|tillköp|mot\s+(?:en\s+)?(?:extra\s+)?(?:kostnad|tillägg)|"
    r"kostar\s+extra|extra\s+(?:kostnad|avgift)|säl[ j]?s\s+separat|"
    r"(?:förmånliga|bra)\s+priser|extra\s+charge|additional\s+cost|sold\s+separately|"
    r"(?:vid\s+köp\s+av|innehåller).*trygghetspaket|trygghetspaket.*innehåller"
)


def normalize_tyres(text):
    """Expand seasonal abbreviations for matching without changing saved quotes."""
    text = " ".join(text.casefold().split())
    text = re.sub(r"\bs\s*[-/]?\s*(?:och|&|o|/)\s*v\s*[- ]*" + WHEEL,
                  "sommarhjul vinterhjul", text)
    text = re.sub(r"\bv\s*[-/]?\s*(?:och|&|o|/)\s*s\s*[- ]*" + WHEEL,
                  "vinterhjul sommarhjul", text)
    text = re.sub(r"\b(sommar|vinter)\s*-?\s*(?:och|&|o|/)\s*(sommar|vinter)\s*[- ]*(däck|hjul)",
                  r"\1\3 \2\3", text)
    text = re.sub(r"\bs\s*-\s*(däck|hjul)", r"sommar\1", text)
    return re.sub(r"\bv\s*-\s*(däck|hjul)", r"vinter\1", text)


def tyre_clauses(sentence):
    """Separate independent offers while retaining qualifiers after a tyre name.

    In particular, keep 'Vinterdäck, använda en säsong, går att köpa till'
    together, but separate 'Sommarhjul ingår, vinterhjul kostar extra'.
    """
    text = normalize_tyres(sentence)
    return re.split(
        r"\b(?:men|but)\b|[,;]\s*(?=(?:(?:inga|utan|extra)\s+)?" + SEASON
        + r"|inga\s+andra|extra\s+(?:uppsättning|set))"
        + r"|\boch\s+(?=inga\s+" + SEASON + r")", text)


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
            quotes = [sentence]
            # A standalone following qualifier may complete a tyre inventory line.
            if index + 1 < len(sentences):
                following = normalize_tyres(sentences[index + 1])
                if re.match(r"(?:ingår\s+(?:inte|ej)|medföljer\s+(?:inte|ej)|"
                            r"(?:finns\s+att\s+)?köpa\s+till|mot\s+tillägg|not\s+included)\b", following):
                    clause += " " + following
                    quotes.append(sentences[index + 1])
            paid = bool(re.search(PAID, clause) or re.search(
                r"\b" + WHEEL + r"\s*(?:på\s+fälg\s*)?[:=-]?\s*\d[\d ]*\s*(?:kr\b|sek\b|:-)", clause))
            denied = bool(re.search(
                r"\b(?:inga|inget|utan|no|without)\s+(?:\w+[ -]+){0,2}(?:" + SEASON + r"[ -]*)?" + WHEEL + r"\b"
                + r"|\b(?:ingår|medföljer|inkluderas)\s+(?:inte|ej)\b"
                + r"|\b(?:inte|ej)\s+(?:ingår|medföljer|inkluderade)\b"
                + r"|\b(?:not\s+included|saknas)\b", clause))
            explicit_inclusion = bool(re.search(r"\b(?:ingår|medföljer|följer\s+med|included|levereras\s+med)\b", clause))
            uncertain = bool(re.search(
                r"\b(?:erbjuder|kan\s+ordna|möjlighet\s+till|vid\s+köp|om\s+du|if\s+you|"
                r"tidigare\s+ingick|hade\s+tidigare)\b|\?", clause))
            # Do not interpret e.g. extra lights near a seasonal tyre as extra tyres.
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
        facts["extra_tires"] = {"value": True, "evidence": "\n".join(quotes),
                                "evidence_sentences": quotes, "source": "rule",
                                "reason": "Both summer and winter tyre sets are included."}
    return facts
