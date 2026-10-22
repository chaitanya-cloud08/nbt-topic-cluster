"""Text normalisation and topic-term extraction for Hindi headlines and English URL slugs."""
from __future__ import annotations

import re
import unicodedata

_ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍﻿"), None)
_DEVANAGARI_WORD = re.compile(r"[ऀ-ॿ]+")
_LATIN_WORD = re.compile(r"[A-Za-z][A-Za-z0-9]+")
_YEAR = re.compile(r"\b(20[2-3][0-9])\b")

# Spelling variants that should count as one word.
HINDI_ALIASES = {
    "डॉ": "डॉक्टर", "डाक्टर": "डॉक्टर", "डॉक्टरों": "डॉक्टर",
    "करवा": "करवाचौथ",  # "करवा चौथ" written as two words
    "प्रेगनेंसी": "प्रेग्नेंसी", "प्रेग्नेंसी": "प्रेग्नेंसी",
}

HINDI_STOP = set("""
के का की को में से पर और है हैं था थी थे हो होगा होगी होंगे होता होती होते ने लिए लिये जो कि यह ये वह वो इस इन उस उन
एक दो तीन चार पांच पाँच तो भी ही न ना नहीं कर करें करना करने करते करती किया किए कैसे क्या क्यों कब कौन कहां कहाँ
जानें जानिए जान देखें देखिए बताया बताई बताए बताते बताती बनेगी बनेगा बनेंगी बनेंगे बनाना बनाएं बनाने आसान तरीका तरीके
सही बात बातें खास नया नई नए सबसे साथ बाद पहले अब ऐसे ऐसी ऐसा अपने अपनी अपना इसका इसकी इसके उनके उनकी उनका उसके उसकी
उसका जब तक तब लेकर मिली मिला मिले मिलेगा रही रहा रहे सकते सकती सकता लगेंगे लगती लगता लगे आएगी आएंगे आएगा वाली वाला
वाले जैसी जैसे जैसा कुछ सब सभी बहुत ज्यादा कम अगर लेकिन या फिर वहीं यहां यहाँ वरना जाता जाती जाते गया गई गए दिया दी
दिए लिया ली लिए कहा कहीं कही हुआ हुई हुए होने रहें रखें रखना रखते चाहिए जरूरी जरूर आज कल दिन साल बार तरह चलिए आइए
रेसिपी डिजाइन टिप्स फोटो तस्वीरें तस्वीर वीडियो खबर खबरें न्यूज लाइव अपडेट मिनट घंटे पूरी पूरा पूरे नाम
राशिफल पंचांग ज्योतिष मूलांक भविष्यफल जन्मतिथि मुहूर्त शुभ मेष वृषभ मिथुन कर्क कन्या तुला वृश्चिक धनु मकर कुंभ मीन
घायल गिरफ्तार गिरफ्तारी आरोपी आरोपियों मृत मृतक
सिंह कुमार शाह यादव शर्मा गुप्ता वर्मा मिश्रा तिवारी पांडे पांडेय चौधरी पटेल खान अली
पति पत्नी बेटा बेटे बेटी बेटियों पिता मां माता भाई बहन बच्चा बच्चे बच्ची बच्चों परिवार परिजन
पुलिस सरकार मामला मामले मामलों लोग लोगों शहर जिला जिले गांव राज्य देश दुनिया भारत भारतीय बड़ा बड़ी बड़े
खिलाफ बयान दावा फैसला फैसले सवाल जवाब शुरू खत्म हुआ हुई हुए मौका तैयारी करोड़ लाख रुपये रुपए प्रतिशत
""".split())

EN_STOP = set("""
a an the to of in on for and or with by from at is are was were be been being how what why when which who whom whose
know tips tip make made making use using used get gets best top new latest easy simple this that these those your you
it its can will would should could do does did not no all more most less way ways thing things step steps day days
year years time times people says said shares share shared reveals revealed told tells explains explain explained
check see here list photo photos pic pics video videos inside look like just also after before during over under
about into than then one two three four five ten special benefit benefits trick tricks secret secrets idea ideas
design designs trendy trending viral news hindi live update updates ki ka ke aur se me mein hai hain ko par wala wali
know-how amp cms html articleshow details detail full what's here's why's vs via per off out up down get-the
home homes at-home help helps quick quickly perfect right wrong good bad big small long short real reason reasons
things know-about must should never always every everyone someone thing women woman men man people's world's
police government govt case people city district village state country india indian claim decision statement
rashifal horoscope panchang jyotish ank bhavishyafal mulank yuti muhurat shubh mesh vrishabh mithun kark kanya tula vrishchik dhanu makar kumbh meen
aries taurus gemini leo virgo libra scorpio sagittarius capricorn aquarius pisces pisce ary
injured injury injuries arrested arrest accused died dead death killed
singh kumar shah yadav sharma gupta verma mishra tiwari pandey chaudhary choudhary patel khan ali
husband wife son daughter father mother brother sister child children kid family
crore lakh rupee rs percent start started end ends match today tomorrow yesterday report reports
""".split())


def normalise(text: str) -> str:
    return unicodedata.normalize("NFC", (text or "").translate(_ZERO_WIDTH))


def hindi_words(text: str) -> list[str]:
    out = []
    for w in _DEVANAGARI_WORD.findall(normalise(text)):
        w = w.rstrip("।॥")
        w = HINDI_ALIASES.get(w, w)
        if len(w) >= 2:
            out.append(w)
    return out


def latin_words(text: str) -> list[str]:
    return [w.lower() for w in _LATIN_WORD.findall(normalise(text))]


def stem_en(word: str) -> str:
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 4 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def slug_tokens(slug: str) -> list[str]:
    return [t for t in re.split(r"[-_/.]+", slug.lower()) if t]


def years_in(text: str) -> set[str]:
    return set(_YEAR.findall(text))


class TermExtractor:
    """Turns a story into a set of topic terms: 'en:doctor', 'hi:डॉक्टर', 'en:karwachauth' ..."""

    def __init__(self, extra_stopwords: set[str] | None = None):
        extra = {normalise(w).lower() for w in (extra_stopwords or set())}
        self.en_stop = EN_STOP | {stem_en(w) for w in extra if w.isascii()} | {w for w in extra if w.isascii()}
        self.hi_stop = HINDI_STOP | {w for w in extra if not w.isascii()}

    def en_terms(self, slug: str) -> set[str]:
        toks = [stem_en(t) for t in slug_tokens(slug)]
        terms: set[str] = set()
        for t in toks:
            if t.isdigit() or len(t) < 3 or t in self.en_stop:
                continue
            terms.add(t)
        # Adjacent pairs joined ("karwa"+"chauth" -> "karwachauth") so spelling variants meet.
        for a, b in zip(toks, toks[1:]):
            if a.isalpha() and b.isalpha() and a not in self.en_stop and b not in self.en_stop:
                terms.add(a + b)
        return {f"en:{t}" for t in terms}

    def hi_terms(self, text: str) -> set[str]:
        terms = set()
        for w in hindi_words(text):
            if w in self.hi_stop or len(w) < 3:
                continue
            terms.add(f"hi:{w}")
        for w in latin_words(text):
            w = stem_en(w)
            if len(w) >= 3 and w not in self.en_stop and not w.isdigit():
                terms.add(f"en:{w}")
        return terms

    def story_terms(self, slug: str, headline: str) -> set[str]:
        return self.en_terms(slug) | self.hi_terms(headline)
