---
name: reel-analyzer
description: Analyze public Instagram Reel URLs using transcript plus representative visual frames. Use when a user sends an instagram.com/reel URL and asks for a summary, insights, visible resources, methods, or practical takeaways.
user-invocable: true
metadata:
  openclaw:
    always: true
    requires:
      bins:
        - python3
        - ffmpeg
        - openclaw
---

# Instagram Reel Analyzer

Use this skill for public Instagram Reel URLs.

## Deterministic extraction

Canonical runtime command:

```bash
python3 -m reel_analyzer.reel_analyzer "<INSTAGRAM_REEL_URL>"
```

The command returns JSON containing metadata, `transcript`, and `visual_facts`.

## Response rule

Treat transcript and visual facts as complementary evidence from the same Reel. Reconcile ambiguous spoken references such as “here”, “this method”, or “this site” with visible frames. Do not invent text or URLs that are unreadable. If the two channels conflict, state the uncertainty rather than silently choosing one.

Respond in Ukrainian by default with:

1. A short overall summary.
2. Main insights and claims.
3. Named tools, websites, methods, templates, or resources actually visible/heard.
4. Practical takeaways.
5. A brief uncertainty note only when extraction was incomplete.

Do not require a `/reel` command when the user has simply pasted a Reel URL in the dedicated Reel Analyzer topic.

## MVP limits

- Public Instagram Reels only.
- No Instagram cookies, passwords, or authenticated sessions.
- Maximum Reel duration is 5 minutes.
- If Instagram blocks retrieval, explain that the public Reel could not be retrieved; do not request account credentials.
