"""Auto-tag contacts from what they actually talk about.

Fires on every interaction logged. Reads the title + body, extracts topics,
attaches `topic:<keyword>` tags to the contact, and writes a `system`
interaction noting which tags were auto-added (so the trail is visible).

Two modes — automatically picked:

  HEURISTIC (default): a small keyword extractor that strips stopwords,
    pulls out noun-like words, and dedupes. No setup, no cost, no network.
    Quality: rough — picks up the literal vocabulary used.

  AI: if ANTHROPIC_API_KEY is set in the env, calls Claude with the
    interaction text and asks for topic tags. Higher quality (understands
    synonyms, deals with phrasing variations). Costs ~$0.005 per call.

Either way, the contact ends up with tags like `topic:copper`,
`topic:signage`, `topic:rebrand` based on what was actually discussed.

Search the topbar for those topics later → finds every contact who
mentioned them. No manual tagging required.
"""
import json
import os
import re
import time
import urllib.error
import urllib.request

NAME = "auto-tag-from-interactions"
VERSION = "0.1.0"
DESCRIPTION = ("After every interaction is logged, extracts topics from its "
               "title + body and auto-tags the contact. Uses Claude if "
               "ANTHROPIC_API_KEY is set, otherwise a heuristic keyword "
               "extractor that works with no setup.")


# --- Heuristic extractor (no network, no key) ---

_STOPWORDS = {
    # generic English noise
    "a","about","after","again","against","all","am","an","and","any","are",
    "aren","aren't","as","at","be","because","been","before","being","below",
    "between","both","but","by","can","can't","cannot","could","couldn't",
    "did","didn","didn't","do","does","doesn","doesn't","doing","don","don't",
    "down","during","each","few","for","from","further","had","hadn","hadn't",
    "has","hasn","hasn't","have","haven","haven't","having","he","he'd","he'll",
    "he's","her","here","here's","hers","herself","him","himself","his","how",
    "how's","i","i'd","i'll","i'm","i've","if","in","into","is","isn","isn't",
    "it","it's","its","itself","just","let's","me","more","most","mustn",
    "mustn't","my","myself","no","nor","not","now","of","off","on","once","only",
    "or","other","ought","our","ours","ourselves","out","over","own","same",
    "shan","shan't","she","she'd","she'll","she's","should","shouldn","shouldn't",
    "so","some","such","than","that","that's","the","their","theirs","them",
    "themselves","then","there","there's","these","they","they'd","they'll",
    "they're","they've","this","those","through","to","too","under","until",
    "up","very","was","wasn","wasn't","we","we'd","we'll","we're","we've",
    "were","weren","weren't","what","what's","when","when's","where","where's",
    "which","while","who","who's","whom","why","why's","will","with","won",
    "won't","would","wouldn","wouldn't","you","you'd","you'll","you're","you've",
    "your","yours","yourself","yourselves","also","get","got","make","made",
    "want","wants","like","want","one","two","three","said","says","say",
    "thing","things","good","great","really","quite","much","many","lot","lots",
    "back","still","might","may","need","needs","needed","look","looks","looking",
    "us","using","use","used","next","new","old",
    # CRM-process noise (events, not topics)
    "call","email","meeting","intro","discuss","discussed","talked","spoke",
    "asked","asking","ask","mentioned","said","told","wrote","sent","received",
    "scheduled","reschedule","reschedule","followup","follow","up","reply",
    "replied","question","questions","quick","check","check-in",
    # business-meta (not the topic itself)
    "client","customer","contact","lead","prospect","company","team","person",
    "people","email",
}

_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9\-]{2,}")


def _heuristic_extract(text: str, max_tags: int = 6) -> list[str]:
    """Return up to max_tags distinct topic words from `text`. Lowercased,
    stopword-filtered, deduped, length >= 3."""
    if not text:
        return []
    tokens = [t.lower() for t in _WORD_RE.findall(text)]
    seen, out = set(), []
    for t in tokens:
        if t in _STOPWORDS or len(t) < 3 or t.isdigit():
            continue
        if t in seen:
            continue
        seen.add(t)
        out.append(t)
        if len(out) >= max_tags:
            break
    return out


# --- Anthropic API path (used only if ANTHROPIC_API_KEY is set) ---

def _claude_extract(text: str, max_tags: int = 6) -> list[str]:
    """Call Claude to extract topic tags from text. Returns [] on any error so
    the heuristic fallback can take over silently."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return []
    body = {
        "model": "claude-sonnet-4-6",
        "max_tokens": 200,
        "messages": [{
            "role": "user",
            "content": (
                "Extract up to "
                f"{max_tags} short topic tags (1-2 words each, lowercase, "
                "hyphenated if multi-word) from this CRM interaction. Topics "
                "should describe what the person CARES ABOUT or is interested "
                "in — not the activity itself. Return JSON only: "
                '{"tags": ["topic-one", "topic-two", ...]}.\n\n'
                f"INTERACTION:\n{text}"
            ),
        }],
    }
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps(body).encode(),
        method="POST",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read().decode())
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError):
        return []
    # Claude returns content as a list of blocks; concat any text blocks.
    raw = ""
    for block in data.get("content", []):
        if block.get("type") == "text":
            raw += block.get("text", "")
    # Look for JSON in the reply.
    m = re.search(r"\{[^}]*\"tags\"[^}]*\}", raw, re.DOTALL)
    if not m:
        return []
    try:
        parsed = json.loads(m.group(0))
        tags = parsed.get("tags") or []
        return [str(t).strip().lower() for t in tags if t][:max_tags]
    except (ValueError, TypeError):
        return []


# --- Hook ---

def on_interaction_logged(ctx, interaction: dict, conn):
    """Fires inside the interactions.log transaction. Reads title+body,
    extracts topics, attaches `topic:<word>` tags to the contact, and logs
    a `system` interaction recording what was auto-tagged."""
    contact_id = interaction.get("contact_id")
    if not contact_id:
        return
    title = interaction.get("title") or ""
    body = interaction.get("body") or ""
    text = (title + " " + body).strip()
    if not text:
        return
    # Skip our own auto-tagged system interactions to avoid feedback loops.
    if (interaction.get("source") or "").startswith("plugin:auto-tag"):
        return

    # Prefer AI when configured, fall back to heuristic.
    tags = _claude_extract(text) or _heuristic_extract(text)
    if not tags:
        return

    now = int(time.time())
    applied = []
    for raw in tags:
        # Normalize to a topic: prefix so they're recognizable in the UI.
        name = "topic:" + re.sub(r"[^a-z0-9\-]+", "-", raw.lower()).strip("-")
        if name == "topic:" or not name[6:]:
            continue
        row = conn.execute("SELECT id FROM tags WHERE name = ?", (name,)).fetchone()
        if row:
            tag_id = row[0]
        else:
            conn.execute(
                "INSERT INTO tags (name, color, scope, created_at) VALUES (?,?,?,?)",
                (name, None, "contact", now),
            )
            tag_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        cur = conn.execute(
            """INSERT OR IGNORE INTO contact_tags
                 (contact_id, tag_id, added_at, added_by)
               VALUES (?,?,?,?)""",
            (contact_id, tag_id, now, ctx.user_id),
        )
        if cur.rowcount:
            applied.append(name)

    if applied:
        mode = "claude" if os.environ.get("ANTHROPIC_API_KEY") else "heuristic"
        conn.execute(
            """INSERT INTO interactions
                 (contact_id, type, channel, title, body, metadata_json,
                  source, occurred_at, created_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                contact_id, "system", "plugin",
                f"auto-tagged: {', '.join(applied)}",
                None,
                json.dumps({"plugin": NAME, "mode": mode,
                            "from_interaction_id": interaction.get("id"),
                            "tags": applied}),
                f"plugin:auto-tag:{mode}", now, now,
            ),
        )
