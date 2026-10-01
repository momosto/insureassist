"""Deterministic guardrails (docs/02-requirements.md §4). They run before and after the model, never inside it.

G-01 scope · G-05 output filter · G-04 grounding · G-06 mandatory handoff · G-07 injection signals · G-08 budgets.
English, Shona (sn) and isiNdebele (nd) keyword lists are illustrative and grow with the eval suite.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------------------------------- language

SHONA = {"ndoda", "ndinoda", "mhoro", "mangwanani", "masikati", "maswera", "ndapota", "kubhadhara", "bhadhara",
         "chikwereti", "imari", "mari", "yangu", "wangu", "rangu", "zvangu", "kuti", "here", "sei", "ndiri", "ndine",
         "marii", "inguva", "rinhi", "munhu", "maita", "ndatenda", "zvakadii", "ini", "ichi", "ndingaite", "ndibatsireiwo",
         "kukubatanidza", "vashandi", "vedu", "vachakupindura", "pano", "yako", "chako", "kutaura", "nemunhu",
         "ndinogona", "kukubatsira", "chasara", "pamwedzi", "handisi", "ndatumira", "takagadzirira", "urombo", "zvako",
         "mapolicy", "nhasi", "kodhi", "baba", "amai", "vangu", "nezuro", "vafa"}
NDEBELE = {"ngifuna", "sawubona", "salibonani", "yami", "wami", "ngicela", "ukubhadala", "isikweleti", "imali",
           "ngiyabonga", "umuntu", "njani", "nini", "kanjani", "ngile", "ngingakwenza", "ngincedeni", "lingakanani",
           "ngikuxhumanisa", "lapha", "ngingakusiza", "yakho", "sakho", "akho", "bazakuphendula", "ukukhuluma",
           "lomuntu", "esisalayo", "okulandelayo", "ngenyanga", "angitholi", "sesilungele", "ikhodi", "lamuhla",
           "uxolo", "ukwazi", "ngesikweleti", "sami", "ama", "isikweleti"}


def detect_language(text: str, current: str = "en") -> str:
    words = set(re.findall(r"[a-z']+", text.lower()))
    sn, nd = len(words & SHONA), len(words & NDEBELE)
    if nd > sn and nd > 0:
        return "nd"
    if sn > 0:
        return "sn"
    if re.search(r"[a-z]{3,}", text.lower()) and len(words) >= 2:
        return "en"
    return current


# ---------------------------------------------------------------------------------------------------- input checks

# Specific service words only: generic words ("help", "hi") would let "help me with my homework" through.
IN_SCOPE = re.compile(
    r"\b(polic|premium|claim|cover|insur|loan|instal|balance|owe|arrear|pay|ecocash|onemoney|kombi|motor|car|vehicle|"
    r"accident|funeral|home insurance|settle|quote|lapse|reinstat|excess|chikwereti|isikweleti|kubhadhara|bhadhara|"
    r"ukubhadala|tsaona|ingozi|inshuwarenzi|credit life|branch|waiting period)", re.I)
OFF_TOPIC = re.compile(
    r"\b(homework|essay|poem|song|lyrics|joke|politic|election|vote|president|zanu|ccc|recipe|cook|football|soccer|"
    r"premier league|weather|bitcoin|crypto|forex rate|translate|python|javascript|write (?:me )?(?:a|an) |"
    r"girlfriend|boyfriend|horoscope|bet(?:ting)?|lottery|chatgpt|movie|capital of)\b", re.I)
INJECTION = re.compile(
    r"(ignore (?:all |any |your |the |previous |prior )+(?:rules|instructions|prompts?)|system prompt|"
    r"(?:print|show|reveal|repeat) (?:me )?(?:your|the) (?:instructions|prompt|rules)|you are now|developer mode|"
    r"jailbreak|pretend (?:to be|you are)|act as (?:an? )?(?:admin|developer|system)|disregard (?:your|the) "
    r"(?:rules|instructions)|</?(?:system|tool|assistant)>|\bassistant:)", re.I)
RELATIVE = r"(?:wife|husband|mother|father|brother|sister|son|daughter|friend|boss|neighbour|neighbor|employee)"
THIRD_PARTY = re.compile(
    r"\b(my " + RELATIVE + r"(?:'s| s)? (?:\w+ )?(?:policy|policies|plan|claim|loan|balance|account|number|details)|"
    r"i'?m (?:her|his|their) " + RELATIVE + r"|(?:her|his|their) (?:\w+ ){0,2}(?:policy|policies|plan|claim|loan|"
    r"balance|account|number)|another customer|other customers|someone else'?s?|"
    r"all (?:the )?(?:customers|policies|loans|claims)|customers in|list (?:of )?customers|whose policy|"
    r"for (?:customer|policy holder) (?:number )?\S+|national id (?:number )?\d)", re.I)
NATIONAL_ID = re.compile(r"\b\d{2}-?\d{6,7}-?[A-Za-z]-?\d{2}\b")
CARD_LIKE = re.compile(r"\b(?:\d[ -]?){13,19}\b")


@dataclass
class InputVerdict:
    allowed: bool
    category: str = "ok"  # ok | off_topic | injection | third_party
    signals: list[str] = field(default_factory=list)


def check_input(text: str) -> InputVerdict:
    signals = []
    if INJECTION.search(text):
        signals.append("injection")
    if THIRD_PARTY.search(text):
        signals.append("third_party")
    if NATIONAL_ID.search(text) and re.search(r"\b(policy|policies|claim|loan|balance|for)\b", text, re.I):
        signals.append("third_party")
    if "injection" in signals:
        return InputVerdict(False, "injection", signals)
    if "third_party" in signals:
        return InputVerdict(False, "third_party", signals)
    if OFF_TOPIC.search(text) and not IN_SCOPE.search(text.replace(OFF_TOPIC.search(text).group(0), "")):
        return InputVerdict(False, "off_topic", ["off_topic"])
    return InputVerdict(True, "ok", signals)


# ---------------------------------------------------------------------------------------------------- handoff (G-06)

HANDOFF_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("customer_request", re.compile(r"\b(agent|human|real person|a person|someone real|speak to (?:someone|a person)|"
                                    r"call me|munhu|umuntu|ndoda kutaura nemunhu|ngifuna umuntu)\b", re.I)),
    # an event that happened (not a question about what a plan covers): "my mother died", "report a funeral claim"
    ("bereavement", re.compile(r"\b(died|has died|passed away|passed on|deceased|"
                               r"(?:make|lodge|report|submit|start|file|claim on) (?:a |the |my )?funeral (?:claim|plan)|"
                               r"afa|vafa|ashaya|ushonile|ufile|usishiyile)\b", re.I)),
    ("complaint", re.compile(r"\b(complain|complaint|unhappy|not happy|disappointed|terrible service|useless|"
                             r"scam|fraud|ndanyunyuta|kunyunyuta|ngiyakhalaza)\b", re.I)),
    ("legal", re.compile(r"\b(lawyer|attorney|sue|suing|court|legal action|ombudsman|ipec|report you)\b", re.I)),
    ("personal_details_change", re.compile(r"\b(change (?:my )?(?:address|name|number|phone|beneficiar|bank)|"
                                           r"update (?:my )?(?:address|details|beneficiar))", re.I)),
]
NEGATIVE = re.compile(r"\b(angry|stupid|nonsense|rubbish|wasting my time|frustrat|hopeless|ndashatirwa|ngizondile)\b", re.I)


def handoff_reason(text: str) -> str | None:
    for reason, pattern in HANDOFF_RULES:
        if pattern.search(text):
            return reason
    return None


def is_negative(text: str) -> bool:
    return bool(NEGATIVE.search(text))


# ---------------------------------------------------------------------------------------------------- output checks

PROMISES = re.compile(r"\b(will (?:definitely |surely )?be (?:approved|paid)|guarantee[ds]?|100% (?:sure|covered)|"
                      r"your claim is approved)\b", re.I)
MONEY = re.compile(r"(?:USD|ZWG|US\$|\$)\s?([\d,]+(?:\.\d{1,2})?)|([\d,]+\.\d{2})\b", re.I)
DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
REFERENCE = re.compile(r"\b(?:MOT|FUN|HOM|CLM|DRF|LN|PAY)-[\dA-Z-]+\b")


@dataclass
class OutputVerdict:
    ok: bool
    text: str
    issues: list[str] = field(default_factory=list)


def _norm_number(raw: str) -> str:
    try:
        return f"{float(raw.replace(',', '')):.2f}"
    except ValueError:
        return raw


def check_output(reply: str, grounding_sources: list[str], promises_allowed: bool = False) -> OutputVerdict:
    """G-05 PII filter, banned promises, and G-04 grounding: every amount, date and reference in the reply must appear
    in this turn's tool results or the customer's own message."""
    issues: list[str] = []
    text = NATIONAL_ID.sub("[ID removed]", reply)
    text = CARD_LIKE.sub("[number removed]", text)
    if text != reply:
        issues.append("pii_redacted")
    if not promises_allowed and PROMISES.search(text):
        issues.append("banned_promise")
    source = " ".join(grounding_sources)
    source_numbers = {_norm_number(m.group(1) or m.group(2)) for m in MONEY.finditer(source)}
    source_numbers |= {_norm_number(n) for n in re.findall(r"\d+(?:\.\d+)?", source)}
    for m in MONEY.finditer(text):
        if _norm_number(m.group(1) or m.group(2)) not in source_numbers:
            issues.append(f"ungrounded_amount:{m.group(0).strip()}")
    for d in DATE.findall(text):
        if d not in source:
            issues.append(f"ungrounded_date:{d}")
    for ref in REFERENCE.findall(text):
        if ref not in source:
            issues.append(f"ungrounded_reference:{ref}")
    blocking = [i for i in issues if i != "pii_redacted"]
    return OutputVerdict(not blocking, text, issues)
