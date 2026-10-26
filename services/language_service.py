"""Post languages: captions and hashtags written natively in the language
the user picks in Studio Chat's Style menu (default English).

The Language list shows English plus the languages of the user's country
(COUNTRIES): the Country on their brand profile page, else its markets
(REGION_COUNTRIES), else the one their browser suggests; with none of those
it is English only.

Brand, product and company names, numbers, prices and URLs stay as they are;
hashtags mix the language with a few English ones (people search both). The
compliance checks and the rest of the pipeline are unchanged. Images keep
English text: image models still render non-Latin scripts unreliably.
"""

# code: (name shown in the menu, how the writer is told to write it, short
# English name for the Style button)
LANGUAGES = {
    "en": ("English", "English", "English"),
    # India
    "hinglish": (
        "Hinglish",
        "Hinglish - Hindi and English mixed the way Indians write on social media, in the Latin alphabet",
        "Hinglish",
    ),
    "hi": ("हिन्दी Hindi", "Hindi, in Devanagari script", "Hindi"),
    "gu": ("ગુજરાતી Gujarati", "Gujarati, in Gujarati script", "Gujarati"),
    "mr": ("मराठी Marathi", "Marathi, in Devanagari script", "Marathi"),
    "ta": ("தமிழ் Tamil", "Tamil, in Tamil script", "Tamil"),
    "te": ("తెలుగు Telugu", "Telugu, in Telugu script", "Telugu"),
    "kn": ("ಕನ್ನಡ Kannada", "Kannada, in Kannada script", "Kannada"),
    "bn": ("বাংলা Bengali", "Bengali, in Bengali script", "Bengali"),
    "ml": ("മലയാളം Malayalam", "Malayalam, in Malayalam script", "Malayalam"),
    "pa": ("ਪੰਜਾਬੀ Punjabi", "Punjabi, in Gurmukhi script", "Punjabi"),
    "or": ("ଓଡ଼ିଆ Odia", "Odia, in Odia script", "Odia"),
    "as": ("অসমীয়া Assamese", "Assamese, in Assamese (Bengali-Assamese) script", "Assamese"),
    "ur": ("اردو Urdu", "Urdu, in Nastaliq (Perso-Arabic) script", "Urdu"),
    # South Asia
    "ne": ("नेपाली Nepali", "Nepali, in Devanagari script", "Nepali"),
    "si": ("සිංහල Sinhala", "Sinhala, in Sinhala script", "Sinhala"),
    # Middle East
    "ar": ("العربية Arabic", "Modern Standard Arabic, in Arabic script", "Arabic"),
    "fa": ("فارسی Persian", "Persian (Farsi), in Persian script", "Persian"),
    "he": ("עברית Hebrew", "Hebrew, in Hebrew script", "Hebrew"),
    "tr": ("Türkçe Turkish", "Turkish", "Turkish"),
    # Europe
    "es": ("Español Spanish", "Spanish", "Spanish"),
    "fr": ("Français French", "French", "French"),
    "de": ("Deutsch German", "German", "German"),
    "pt": ("Português Portuguese", "Portuguese", "Portuguese"),
    "it": ("Italiano Italian", "Italian", "Italian"),
    "nl": ("Nederlands Dutch", "Dutch", "Dutch"),
    "pl": ("Polski Polish", "Polish", "Polish"),
    "sv": ("Svenska Swedish", "Swedish", "Swedish"),
    "da": ("Dansk Danish", "Danish", "Danish"),
    "no": ("Norsk Norwegian", "Norwegian (Bokmål)", "Norwegian"),
    "fi": ("Suomi Finnish", "Finnish", "Finnish"),
    "el": ("Ελληνικά Greek", "Greek, in Greek script", "Greek"),
    "ro": ("Română Romanian", "Romanian", "Romanian"),
    "cs": ("Čeština Czech", "Czech", "Czech"),
    "hu": ("Magyar Hungarian", "Hungarian", "Hungarian"),
    "uk": ("Українська Ukrainian", "Ukrainian, in Cyrillic script", "Ukrainian"),
    "ru": ("Русский Russian", "Russian, in Cyrillic script", "Russian"),
    # East and Southeast Asia
    "zh": ("简体中文 Chinese", "Simplified Chinese", "Chinese"),
    "zh-hant": ("繁體中文 Traditional Chinese", "Traditional Chinese", "Chinese"),
    "ja": ("日本語 Japanese", "Japanese", "Japanese"),
    "ko": ("한국어 Korean", "Korean, in Hangul", "Korean"),
    "id": ("Bahasa Indonesia Indonesian", "Indonesian (Bahasa Indonesia)", "Indonesian"),
    "ms": ("Bahasa Melayu Malay", "Malay (Bahasa Melayu)", "Malay"),
    "th": ("ไทย Thai", "Thai, in Thai script", "Thai"),
    "vi": ("Tiếng Việt Vietnamese", "Vietnamese", "Vietnamese"),
    "fil": ("Filipino", "Filipino (Tagalog)", "Filipino"),
    "taglish": (
        "Taglish",
        "Taglish - Tagalog and English mixed the way Filipinos write on social media",
        "Taglish",
    ),
    # Africa
    "sw": ("Kiswahili Swahili", "Swahili", "Swahili"),
    "am": ("አማርኛ Amharic", "Amharic, in Ge'ez script", "Amharic"),
    "ha": ("Hausa", "Hausa", "Hausa"),
    "yo": ("Yorùbá Yoruba", "Yoruba", "Yoruba"),
    "zu": ("isiZulu Zulu", "Zulu", "Zulu"),
    "af": ("Afrikaans", "Afrikaans", "Afrikaans"),
}

# ISO country code: (name, its languages besides English, most used first)
COUNTRIES = {
    # India and South Asia
    "IN": ("India", ["hi", "hinglish", "bn", "te", "mr", "ta", "ur", "gu", "kn", "ml", "or", "pa", "as"]),
    "PK": ("Pakistan", ["ur", "pa"]),
    "BD": ("Bangladesh", ["bn"]),
    "NP": ("Nepal", ["ne", "hi"]),
    "LK": ("Sri Lanka", ["si", "ta"]),
    # GCC and Middle East
    "AE": ("United Arab Emirates", ["ar", "hi", "ur", "ml", "fil"]),
    "SA": ("Saudi Arabia", ["ar"]),
    "QA": ("Qatar", ["ar"]),
    "KW": ("Kuwait", ["ar"]),
    "BH": ("Bahrain", ["ar"]),
    "OM": ("Oman", ["ar"]),
    "JO": ("Jordan", ["ar"]),
    "LB": ("Lebanon", ["ar", "fr"]),
    "IQ": ("Iraq", ["ar"]),
    "IR": ("Iran", ["fa"]),
    "IL": ("Israel", ["he", "ar"]),
    "TR": ("Turkey", ["tr"]),
    "EG": ("Egypt", ["ar"]),
    # North America
    "US": ("United States", ["es"]),
    "CA": ("Canada", ["fr"]),
    "MX": ("Mexico", ["es"]),
    # Latin America
    "BR": ("Brazil", ["pt"]),
    "AR": ("Argentina", ["es"]),
    "CO": ("Colombia", ["es"]),
    "CL": ("Chile", ["es"]),
    "PE": ("Peru", ["es"]),
    "VE": ("Venezuela", ["es"]),
    # Europe
    "GB": ("United Kingdom", []),
    "IE": ("Ireland", []),
    "FR": ("France", ["fr"]),
    "DE": ("Germany", ["de", "tr"]),
    "ES": ("Spain", ["es"]),
    "PT": ("Portugal", ["pt"]),
    "IT": ("Italy", ["it"]),
    "NL": ("Netherlands", ["nl"]),
    "BE": ("Belgium", ["nl", "fr", "de"]),
    "CH": ("Switzerland", ["de", "fr", "it"]),
    "AT": ("Austria", ["de"]),
    "SE": ("Sweden", ["sv"]),
    "NO": ("Norway", ["no"]),
    "DK": ("Denmark", ["da"]),
    "FI": ("Finland", ["fi", "sv"]),
    "PL": ("Poland", ["pl"]),
    "CZ": ("Czech Republic", ["cs"]),
    "HU": ("Hungary", ["hu"]),
    "RO": ("Romania", ["ro"]),
    "GR": ("Greece", ["el"]),
    "UA": ("Ukraine", ["uk", "ru"]),
    "RU": ("Russia", ["ru"]),
    # East and Southeast Asia
    "CN": ("China", ["zh"]),
    "HK": ("Hong Kong", ["zh-hant", "zh"]),
    "TW": ("Taiwan", ["zh-hant"]),
    "JP": ("Japan", ["ja"]),
    "KR": ("South Korea", ["ko"]),
    "SG": ("Singapore", ["zh", "ms", "ta"]),
    "MY": ("Malaysia", ["ms", "zh", "ta"]),
    "ID": ("Indonesia", ["id"]),
    "TH": ("Thailand", ["th"]),
    "VN": ("Vietnam", ["vi"]),
    "PH": ("Philippines", ["fil", "taglish"]),
    # Oceania
    "AU": ("Australia", []),
    "NZ": ("New Zealand", []),
    # Africa
    "NG": ("Nigeria", ["ha", "yo"]),
    "KE": ("Kenya", ["sw"]),
    "TZ": ("Tanzania", ["sw"]),
    "ET": ("Ethiopia", ["am"]),
    "ZA": ("South Africa", ["zu", "af"]),
    "MA": ("Morocco", ["ar", "fr"]),
    "DZ": ("Algeria", ["ar", "fr"]),
    "TN": ("Tunisia", ["ar", "fr"]),
}


def normalize(code: str | None) -> str:
    return code if code in LANGUAGES else "en"


def normalize_country(code: str | None) -> str | None:
    code = (code or "").upper()
    return code if code in COUNTRIES else None


def instruction(code: str | None, what: str = "caption") -> str:
    """Extra system-prompt lines for a non-English post ("" for English)."""
    code = normalize(code)
    if code == "en":
        return ""
    how = LANGUAGES[code][1]
    if what == "hashtags":
        return (
            f"\nLANGUAGE: the post is written in {how}. Make about half of the hashtags in that language and the rest "
            "in English (people search both). Keep brand and product names as they are spelled."
        )
    return (
        f"\nLANGUAGE: write the {what} in {how}, the way a native speaker writes on social media - natural, not a "
        "word-for-word translation. Keep brand, product and company names, numbers, prices and URLs exactly as "
        "given. Any required disclaimer may stay in English if that is how it is usually shown."
    )


# Brand profile markets (services/compliance_rules.REGIONS) -> country
REGION_COUNTRIES = {"US": "US", "UAE/GCC": "AE", "India": "IN"}


def countries_for_regions(regions) -> list[str]:
    return [REGION_COUNTRIES[r] for r in regions or [] if r in REGION_COUNTRIES]


def options(countries: str | list[str] | None = None) -> list[dict]:
    """For the composer's Language menu: English first, then each country's
    languages in order (English only when no country is known)."""
    if isinstance(countries, str) or countries is None:
        countries = [countries]
    codes = ["en"]
    for country in filter(None, map(normalize_country, countries)):
        codes += [c for c in COUNTRIES[country][1] if c not in codes]
    return [{"code": code, "name": LANGUAGES[code][0], "short": LANGUAGES[code][2]} for code in codes]


def countries() -> list[dict]:
    """For the composer's Country search, A-Z."""
    return sorted(({"code": code, "name": name} for code, (name, _) in COUNTRIES.items()), key=lambda c: c["name"])
