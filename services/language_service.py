"""Post languages: captions and hashtags written natively in the language
the user picks in Studio Chat's Style menu (default English).

Brand, product and company names, numbers, prices and URLs stay as they are;
hashtags mix the language with a few English ones (people search both). The
compliance checks and the rest of the pipeline are unchanged. Images keep
English text: image models still render Indic scripts unreliably.
"""

# code: (name shown in the menu, how the writer is told to write it)
LANGUAGES = {
    "en": ("English", "English"),
    "hinglish": (
        "Hinglish",
        "Hinglish - Hindi and English mixed the way Indians write on social media, in the Latin alphabet",
    ),
    "hi": ("हिन्दी Hindi", "Hindi, in Devanagari script"),
    "gu": ("ગુજરાતી Gujarati", "Gujarati, in Gujarati script"),
    "mr": ("मराठी Marathi", "Marathi, in Devanagari script"),
    "ta": ("தமிழ் Tamil", "Tamil, in Tamil script"),
    "te": ("తెలుగు Telugu", "Telugu, in Telugu script"),
    "kn": ("ಕನ್ನಡ Kannada", "Kannada, in Kannada script"),
    "bn": ("বাংলা Bengali", "Bengali, in Bengali script"),
    "ml": ("മലയാളം Malayalam", "Malayalam, in Malayalam script"),
    "pa": ("ਪੰਜਾਬੀ Punjabi", "Punjabi, in Gurmukhi script"),
    "ar": ("العربية Arabic", "Modern Standard Arabic, in Arabic script"),
}


def normalize(code: str | None) -> str:
    return code if code in LANGUAGES else "en"


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


def options() -> list[dict]:
    """For the composer's Language menu."""
    return [{"code": code, "name": name} for code, (name, _) in LANGUAGES.items()]
