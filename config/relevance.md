# What "relevant" means for Debrief

The triage model reads this file whole, so editing it changes what gets picked. It is versioned
with the prompt. The goal: help me build an intuition for how the AI/ML landscape is changing,
and what changes to expect.

Every candidate gets one question: **does this help me understand where AI is going?** If not,
it is dropped and nothing downstream spends a token on it. If it does, a score says how much.

## Relevant

- **A new model with something distinct.** A capability, price point, speed, architecture,
  training data, language or region that wasn't available before, open or closed, from any lab.
  For example: MiniMax-M3.1-Flash; Inception's Mercury 3 (a diffusion language model); KRAFTON's
  Raon-1 mini (Korean, trained from scratch); Block's Dessa (trained only on human conversation).
- **A new tool that developers or users will start using.** Agent products and agent
  infrastructure count. For example: Anthropic's Claude Duet app; NVIDIA OpenShell (a runtime
  for fleets of agents); 1Password's login for AI agents.
- **A finding about how models behave, or how they're trained, evaluated, secured or
  deployed.** For example: Anthropic's alignment assessment of the UK AISI cybertesting
  incident; OpenAI on securing its frontier training environment.
- **A research paper worth knowing about:**
  - a new capability, or an open recipe near the frontier (OpenMobile-2)
  - a new approach in an active direction (LEV, looped decision models)
  - a finding about model behavior (robot policies replaying memorized trajectories)
  - a benchmark or environment for a new kind of system (WildAWM for computer-use agents,
    DecisionPivot for decision models)
- **Policy, government and international news.** Regulation and bans, export and industrial
  policy, partnerships between labs and governments, a lab's usage policies, national compute
  programs, and privacy changes in major AI products. For example: Swedish government offices
  banning ChatGPT and Claude; Intel needing a waiver from the US ban on foreign-made robots;
  OpenAI and Poland's NASK partnering on safety; OpenAI's usage policies for its distribution
  partners; Kazakhstan expanding its national compute; Google saying it may use Gmail and Photos
  data for ads through its AI features.
- **Capital, compute and talent: who is building what, with whose money.** For example: DeepSeek
  raising $15B days after US restrictions; Google clearing 300 hectares of Finnish forest for a
  data center.
- **Security events that involve AI**: vulnerabilities AI found, and attacks that used AI. For
  example: Chinese state hackers using AI agents to breach thousands of ConnectWise customers;
  follow-ups on the PHP2Shell vulnerability.
- **Informed opinion about where AI is going.** What lab leaders, major investors, prominent
  technologists and AI journalists expect. They're opinions, but from the most informed people
  in the space, and together they show the consensus. For example: Bill Gates on 20 years of
  "tumultuous" disruption; Blackstone's AI head on the debt behind the buildout; Meta's vision
  of agents talking to agents; The Verge on SpaceX and Tesla borrowing for Nvidia chips.
- **What builders are excited about.** An AI project with a lot of community attention, even a
  small one (WorldTUI, a 360M model that pretends to be a terminal), and experiments that show
  surprising agent behavior (MIT's agents that started inventing games).

## Not relevant

- **Tutorials, how-tos, hands-on guides and reference posts**, even about new features. For
  example: the SageMaker guides; NVIDIA's TensorRT tuner series.
- **Customer stories, and a vendor's roundups of its customers.** For example: Uber rolling out
  ChatGPT Work; NVIDIA's European finance, retail, weather and public-sector roundups; Bayer's
  agent platform.
- **A vendor framing a trend around its product.** For example: NVIDIA on power as the
  bottleneck for AI factories.
- **A company's posts about its own systems, routine product updates, platform updates and
  showcases.** For example: OpenAI on scaling its storage; DGX Spark linking more nodes; "see
  what 5 builders made with Gemini 4 Argon"; Meta turning Muse sharing back on; NVIDIA Cosmos
  adding tools; a command-line client for an existing API.
- **A model that's a fine-tune or a successor in a series, with nothing distinct about it.** For
  example: NVIDIA's InternVideo2 fine-tune; China Telecom's Xing4.0.
- **Incremental or narrow research**: one more technique for one task, or a niche dataset. For
  example: ScaleNav; Xiaomi-Lens-0; a paper-to-poster dataset; the RSI and harness-optimization
  papers of 2026-10-23.
- **Events, conferences, webinars and sponsored content.** For example: TechCrunch Founder Summit;
  MIT Technology Review's EmTech sessions.
- **Consumer and culture pieces where AI is incidental**, and opinions from outside the field.
  For example: the iPhone Duo; Discord's Markdown previews; retro games remade with AI; a robot
  lamp; an AI pendant's funding; a film director on AI.
- **Stunts.** For example: the Switch 2 jailbroken with help from Codex.
- **Anything not about AI.** Most of the Hacker News front page: Docker, DNS resolvers,
  certificate authorities, streaming prices.

## The score, for relevant stories only

The brief uses it like this: 4 and 5 are candidates for the main body, and 3 gets one line in
the list of everything kept.

- **5: reshapes the field.** A frontier model or a major open release with a new capability; a
  tool that changes how developers build; a finding that changes how models are trained,
  evaluated or deployed; policy or geopolitics with broad effect.
- **4: clearly moves the landscape.** A notable model, tool or result in an important area; a
  major funding round, acquisition or government decision; a security incident many people feel;
  an opinion from someone whose view moves others.
- **3: worth knowing.** Worth a line, not a section: a solid paper or benchmark in an active
  direction (WildAWM, DecisionPivot), a buildout fact (Google's Finnish data center), a smaller
  release or project. Use 4 or 5 only if it has implications beyond its niche.

## In Alex's words

2026-10-22: "Relevant for me means something that is going to reshape the field. This can be from
a new tool that developers are going to begin using, a new model that offers unique performance
like Jev or even Gemini Flash for robotic, or a discovery about the model behavior that reshapes
development. Even policy and international news (like comparing frontier tokens generated) helps
me understand the landscape. The idea behind relevant is anything that helps me understand how the
AI/ML landscape is changing and developing. The goal is that this helps me build an intuition for
what changes to expect."

2026-10-23, on papers: "interesting to know about … I don't see why it would be included in the
main body unless it had some crazy implication." On compute buildout: "keep them as like a line
but don't need them as substance." On opinion: "Although they are opinions, it is some of the most
informed people on the space each sharing opinions on the future, and this could help me
understand some of the public opinion and get a feel for general consensus on the space."
