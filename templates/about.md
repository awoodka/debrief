# How Debrief works

Debrief is a daily briefing on AI and machine learning. An automated pipeline that Alex Woodka built and runs puts each issue together overnight. No one edits an issue before it publishes.

## When it runs

A new issue publishes every morning. The pipeline starts at 3 a.m. Eastern. It also checks discussion threads at 9 a.m., 3 p.m. and 9 p.m., so each issue knows which stories people are talking about.

## What it reads

Debrief reads 52 sources. 32 of them can bring in a new story: lab and company blogs, paper feeds such as arXiv and Hugging Face Daily Papers, the tech press, and government AI offices such as NIST and the UK AI Security Institute. The other 20 add discussion and context, among them Hacker News, LessWrong, the Alignment Forum, and newsletters such as Import AI and Simon Willison's Weblog.

## How stories are chosen

A language model scores each new story against a written set of rules about what matters to someone following AI and machine learning. Those scores were checked against stories labeled by hand, and they agree on 119 of 120. The stories that pass are the day's "kept" stories.

## How an issue is written

Claude, Anthropic's model, reads the kept stories, their discussion threads and, when a story needs it, the original page. Then it writes the issue. Before anything publishes, an automatic check confirms that every quote appears word for word in its source, that every story cited was kept that day, and that the issue stays within its length limits. Anything that fails goes back to the writer to fix.

## What you'll see

- **Figure of the day.** The one number that best sums up the issue, on days when one stands out. The check confirms it appears in its story's source.
- **Charts.** At most one per issue, when a section turns on a clear comparison. Every value comes from a source the section cites.
- **The case for, the case against, and what the analysis suggests.** These appear when a story is genuinely two-sided. "The analysis suggests" is the writer's judgment, made from the sources.
- **Everything kept today.** Every story that passed, with a § mark pointing to where the issue covers it.
- **"Some sources didn't load today."** A source failed that night, so some of its stories may be missing.

## Limits

- Debrief can be wrong. Quotes are checked word for word, but summaries and judgments are not reviewed by a person.
- When a source fails to load, its stories can be missing for that day.
- Issues before October 26, 2026 come from the first version of the pipeline and keep their original layout.

## Corrections

Send corrections or questions to alexwoodka@gmail.com.
