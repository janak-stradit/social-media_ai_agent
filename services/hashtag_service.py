"""A last tidy-up of the hashtags AVIR AI writes, before the post is shown.

The hashtag agent is asked to avoid spam tags, but a model still slips in
engagement bait now and then (#follow4follow, #likeforlike, #fyp...). Those
tags make a brand look cheap and platforms don't show posts more for them, so
they are removed here, together with repeats (#Diwali and #diwali) and tags
that can't work (only digits, or very long).

This never empties a set: if every tag would go, the set is kept as it was.
Only generated hashtags pass through here; a person's own edits are left
alone (services/post_edit_service.py).
"""

# Engagement bait and other tags that add nothing for a brand. Lower case, without "#".
SPAM_TAGS = frozenset({
    "follow", "followme", "followforfollow", "follow4follow", "f4f", "followback", "followforfollowback",
    "followers", "follower", "instafollow", "like", "likes", "like4like", "likeforlike", "likeforlikes",
    "likesforlikes", "l4l", "liker", "likers", "instalike", "tagsforlikes", "comment4comment", "c4c",
    "spam4spam", "s4s", "shoutout4shoutout", "sub4sub", "instagood", "photooftheday", "picoftheday",
    "instadaily", "fyp", "foryou", "foryoupage", "explore", "explorepage",
})
MAX_TAG_LENGTH = 60  # characters after "#"; longer ones are typos or sentences


def tidy(tags) -> list[str]:
    """The same tags in the same order, without spam, repeats or broken ones."""
    original = [str(t).strip() for t in (tags or []) if str(t).strip()]
    kept, seen = [], set()
    for tag in original:
        word = tag.lstrip("#").strip()
        key = word.lower()
        if not word or key in seen or key in SPAM_TAGS or word.isdigit() or len(word) > MAX_TAG_LENGTH:
            continue
        seen.add(key)
        kept.append(tag)
    return kept or original
