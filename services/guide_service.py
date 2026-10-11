"""In-app guide: the "Get started" checklist in Studio Chat and the
first-visit tours (static/js/guide.js).

Every checklist step ticks itself off from what the user has actually done
(db.guide_progress_facts) - nothing to tick by hand. The few things the
server can't see (an idea picked in the Ideas panel) arrive as events.
Tours seen and the dismissed checklist are stored per user (users.guide_state)
so they don't come back on another device.
"""

TOURS = ("studio", "brand", "approvals", "review", "settings")
EVENTS = ("idea_used",)

# (key, title, why it matters, action label, link or in-page action, help article)
STEPS = [
    ("brand_profile", "Set up your brand profile", "Every post follows your brand's voice, colours and products.",
     "Open brand profile", "/brand-profile", "brand-profile"),
    ("post", "Create your first post", "Describe a post in one line and get captions for each platform.",
     "Write a brief", "action:composer", "studio-chat"),
    ("image", "Create an image or a carousel", "Tick Image, or type /carousel for a set of slides.",
     "Try /carousel", "action:carousel", "image-commands"),
    ("idea", "Use an idea from \"Ideas for you\"", "Ready-made briefs from your industry's news and upcoming festivals.",
     "Show ideas", "action:ideas", "ideas-for-you"),
    ("approval", "Send a post for approval", "Your reviewer approves each platform, or asks for changes.",
     "How approvals work", "/help/approvals", "approvals"),
    ("social", "Connect a social account", "Link Instagram, Facebook or LinkedIn to publish from AVIR AI.",
     "Connect accounts", "/settings", "social-accounts"),
    ("published", "Publish or schedule a post", "Publish an approved post, or schedule one to go out by itself.",
     "How scheduling works", "/help/scheduling", "scheduling"),
]


def checklist(user_id: int) -> dict:
    from db import get_guide_state, guide_progress_facts

    facts = guide_progress_facts(user_id)
    state = get_guide_state(user_id)
    done_map = {**facts, "idea": facts["idea_link"] or "idea_used" in state["events"]}
    steps = [
        {"key": key, "title": title, "why": why, "action_label": label, "action": action,
         "help": f"/help/{article}", "done": bool(done_map.get(key))}
        for key, title, why, label, action, article in STEPS
    ]
    done = sum(s["done"] for s in steps)
    return {
        "steps": steps, "done": done, "total": len(steps), "complete": done == len(steps),
        "dismissed": state["checklist_dismissed"], "tours_seen": state["tours"],
    }


def update_state(user_id: int, tour_seen: str | None = None, dismiss_checklist: bool | None = None,
                 event: str | None = None, reset_tours: bool = False) -> dict:
    from db import get_guide_state, save_guide_state

    state = get_guide_state(user_id)
    if tour_seen in TOURS and tour_seen not in state["tours"]:
        state["tours"].append(tour_seen)
    if reset_tours:
        state["tours"] = []
    if dismiss_checklist is not None:
        state["checklist_dismissed"] = bool(dismiss_checklist)
    if event in EVENTS and event not in state["events"]:
        state["events"].append(event)
    return save_guide_state(user_id, state)
