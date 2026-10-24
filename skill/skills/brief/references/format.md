# brief.draft.json

One JSON object, written to `brief.draft.json` in the run folder. Code checks it when you finish,
then adds the links, story details, quote attributions, the scan of every kept story, and the
ledger. So the draft holds only your judgment and your words.

## Fields

| Field | Required | What goes in it |
|---|---|---|
| `date` | yes | the day, `YYYY-MM-DD` (the skill's argument) |
| `headline` | yes | the day in one event-first line, 10–160 characters |
| `what_happened` | yes | 3–6 `{text, stories}`: one concrete event each, news language, no interpretation. `stories` is the ids it's about (at least one). |
| `lead` | yes | 2–4 paragraphs, separated by `\n\n`: the through-line |
| `sections` | yes | the article, in reading order (see below) |
| `updates` | no | `{story, text}` for each follow-up you cover: what's new since the earlier brief |
| `notes` | no | short lines on gaps: a failed source, rules-only triage, a quiet day |
| `fetch_notes` | no | `{url: why}` for each page you fetched |

A section is `{heading, body, stories, split?, quotes?}`:
- **`heading`:** event-first, at most 120 characters.
- **`body`:** paragraphs separated by `\n\n`. No URLs, no story ids. Light markdown (`**bold**`,
  `*italic*`) is fine.
- **`stories`:** the ids of every story the section covers, the most important first. At least one.
- **`split`:** optional, only for a genuinely two-sided call. `{for, against, read}`, a few
  sentences each, in your voice.
- **`quotes`:** optional. `{story, text}`, where `text` is word for word from that story's detail
  file: a picked comment, the full text or the summary. "…" may stand for omitted words. Code
  finds the author and the thread.

Story ids are the index's `id` values, in forms like `arxiv:<paper>`, `url:<hash>`,
`hf-model:<org>/<name>`, `hn:<thread>` and `lesswrong:<post>`. An id listed in an index line's
`merged` also works; code maps it to that line.

The check rejects:
- an id that isn't in today's index;
- a quote that isn't in its story's detail file;
- a URL or id in any prose field;
- fewer than 3 or more than 6 `what_happened` items (fewer are allowed only when the day has
  fewer stories);
- a heading over 120 characters;
- an article over 3,750 words.

## A short example

This shows the shape only. The day, the stories and the ids are made up, so never copy them. Real
briefs are longer.

```json
{
  "date": "2026-01-15",
  "headline": "Acme open-sources a 30B model that beats its own API: the closed tier's lead shrinks to weeks",
  "what_happened": [
    {"text": "Acme released Bolt-30B under Apache 2.0, scoring above its paid model on coding evals.", "stories": ["hf-model:acme/bolt-30b", "url:0f0f0f0f0f0f0f0f"]},
    {"text": "The EU published its first fines under the AI Act's transparency rules.", "stories": ["url:1a1a1a1a1a1a1a1a"]},
    {"text": "A paper showed reward models can be gamed by response length alone.", "stories": ["arxiv:2601.00001"]}
  ],
  "lead": "Acme gave away a model that beats the one it sells. That's the day.\n\nSecond paragraph…",
  "sections": [
    {
      "heading": "Acme open-sources Bolt-30B, which beats its paid API: why sell what you give away",
      "body": "First paragraph…\n\nSecond paragraph…",
      "stories": ["hf-model:acme/bolt-30b", "url:0f0f0f0f0f0f0f0f", "hn:40000001"],
      "quotes": [{"story": "hn:40000001", "text": "We swapped it in for our eval harness and it held up on the long-context tasks"}]
    }
  ],
  "updates": [{"story": "url:2b2b2b2b2b2b2b2b", "text": "New since the 13th: the second lab confirmed the same jailbreak."}],
  "notes": ["The Verge's feed failed today, so press coverage may be thin."],
  "fetch_notes": {"https://huggingface.co/acme/bolt-30b": "to confirm the license and the eval table"}
}
```
