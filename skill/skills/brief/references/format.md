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
| `figure` | no | the figure of the day, `{value, caption, story}` (below) |
| `chart` | no | at most one chart, in one section: `{section, caption, unit, bars}` (below) |

A section is `{heading, body, stories, split?, quotes?}`:
- **`heading`:** event-first, at most 120 characters.
- **`body`:** paragraphs separated by `\n\n`. No URLs, no story ids. Light markdown (`**bold**`,
  `*italic*`) is fine.
- **`stories`:** the ids of every story the section covers, the most important first. At least one.
- **`split`:** optional, only for a genuinely two-sided call. `{for, against, read}`, a few
  sentences each. The page labels them "The case for", "The case against" and "The analysis
  suggests", so `read` is the analysis's call, with no "I" or "my".
- **`quotes`:** optional. `{story, text}`, where `text` is word for word from that story's detail
  file: a picked comment, the full text or the summary. "…" may stand for omitted words. Code
  finds the author and the thread.

The figure of the day is the single most telling number in the issue, shown large near the top
of the page. Leave `figure` out when nothing stands out.
- **`value`:** the number exactly as it appears in that story's source text, such as `"7,403"` or
  `"85%"`.
- **`caption`:** one plain sentence on what the number counts. No first person.
- **`story`:** the id of the story it comes from.

A chart is a few bars inside one section. Add one only when that section turns on a clear numeric
comparison from one source.
- **`section`:** the section it goes in, counting from 1.
- **`bars`:** 2 to 5 `{label, value}`. `value` is a plain number (`7403`, `85`, `2.54`), exactly
  as the source states it, from a story the section lists in `stories`.
- **`unit`:** `"%"` for percentages (the bars are then drawn out of 100), a word such as `"ms"` or
  `"tokens"`, or `""` for a plain count.
- **`caption`:** one plain sentence: what is measured, and whose numbers they are.

Story ids are the index's `id` values, in forms like `arxiv:<paper>`, `url:<hash>`,
`hf-model:<org>/<name>`, `hn:<thread>` and `lesswrong:<post>`. An id listed in an index line's
`merged` also works; code maps it to that line.

The check rejects:
- an id that isn't in today's index;
- a quote that isn't in its story's detail file;
- a figure whose value isn't in its story's detail file, or only as part of a longer number;
- a chart in a section that doesn't exist, with fewer than 2 or more than 5 bars, or with a value
  that isn't in a story its section lists;
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
  "figure": {"value": "71.3%", "caption": "Bolt-30B's score on the coding benchmark, above the model Acme sells.", "story": "hf-model:acme/bolt-30b"},
  "fetch_notes": {"https://huggingface.co/acme/bolt-30b": "to confirm the license and the eval table"}
}
```
