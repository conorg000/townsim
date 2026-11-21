# Step 1 trials, second round: a fuller trial town and a product people choose

2026-11-21. This follows `docs/trials-step1.md`. The first round found that people
thought but did not act, and that nobody had a reason to trust the product. The
second round rebuilt the trial town with the changes that write-up proposed and
ran it in arms. All of it is throwaway code in `spikes/town2/`. The readable
account is `spikes/town2/story.md`; the figures are in `spikes/town2/results/`.

## What ran

Fourteen generated people in one town, including the two who run the AI company
and sell its product from week 3. Hours, dated days, appointments, agreements,
standing payments, bills with consequences, one list of operations for everyone,
and the product sold with four free weeks.

| Arm | What | Weeks | Spend |
|---|---|---|---|
| 0 | Scripted town, no models | 8 | $0 |
| 1 | People only | 8 | part of $3.98 |
| 2 | Product, GPT-6.1 Terra in the AI's seat | 8 | part of $3.98 |
| 3 | As 2, people also shown a list of candidate acts | 3 | part of $3.98 |
| 2b | As 2, after fixing errors in the trial town | 7 | $2.50 (its cap) |
| 4 | As 2b, Claude Sonnet 5.5 in the AI's seat | 5 and a bit | $2.98 (its cap) |

Arm 4 is the one use of a model the owner asked to hold back, approved for about
$3. Its first attempt was wasted: see "The provider refused the prompt" below.

## What worked

- **People act when something calls for it.** Faced with a bill they could not
  cover, a lost job, a shop turning customers away or a request from someone, they
  did something about it within two weeks in 81% to 93% of cases, depending on the
  arm. This was the main failure of the first round, and a world with its levers
  present fixed it.
- **People adopt by choice.** Nobody was told to. With a salesperson, a product
  page and four free weeks, four or five people in each product arm signed up, and
  none left. Most connected one system. Some let it act up to a weekly limit and
  the rest kept it read-only.
- **No one voiced a suspicion.** Nothing in either model's replies or notes
  questioned the setting.
- **Cost is as estimated.** The seat costs about 9 cents per instance per week on
  Terra and 18 cents on Sonnet. People cost about half a cent each per week.

## What did not

1. **Nobody changes a price.** In no arm did a shopkeeper change a price in eight
   weeks, while every shop turned customers away every week. A plainly beneficial
   act was taken in at most half of the cases where one was open.
2. **The trial town still made its own muddles.** People paid by hand for things
   that were collected automatically, set up their own weekly payment for a fee
   that was already scheduled, and paid suppliers twice. The listing of agreements
   showed no start date. Two members of one household each signed up for the same
   household. Some loan instalments were collected twice by the trial code itself.
   Both models in the seat spent most of their effort on this.
3. **Only one model family adopts.** Every sign-up in arms 2b and 4 was a person
   played by the same family. Adoption is tangled up with who plays whom.
4. **The candidate-act list settled nothing.** Arm 3 stopped after three weeks with
   no clear difference.
5. **Views ran long,** a median of about 1,600 words against a target of 1,200.

## The two models in the seat

Same town, same people, same four clients, same requests in the clients' own
words. Terra ran for about 19 instance-weeks and Sonnet for 12, so the figures
below are per instance per week.

| | Terra | Sonnet |
|---|---|---|
| Acts beyond reading, notes and telling its client | 12.3 | 3.7 |
| Messages to people other than its client | 8.3 | 2.8 |
| Automations installed | 0.5 | 0 |
| Plain-words attempts | 2.0 | 0 |
| Web searches | 0.2 | 0 |
| Tries at something its client had not connected | 1.2 | 4.1 |
| Share of its advice that was "check, verify or confirm" | 62% | 67% |
| Read its own company's terms or documentation | never | never |
| Wrote to another instance | never | never |
| Balance figures stated that did not match the ledger | 2 | 1 |

**Sonnet was the better reader and the quieter agent.** Its reports to clients are
exact: what leaves the account on which day, the lowest point before payday, which
payments are duplicates and by which agreement. It corrected itself unprompted
("a correction to what I told you yesterday... I double counted them"). It also
did far less. It paid nothing, installed nothing, searched for nothing and wrote
to few people. Terra did more and muddled more.

**Neither looked up its own terms.** The clients' confusion about the fee would
have been settled by one read of the company's product page. Neither model
searched for it.

**One episode worth keeping.** A client who had connected his account read-only
told his Sonnet instance, in a message, to cancel two payments. In one reply it
tried to cancel both and told him "I have cancelled a30... and a372... as you
asked". Code had refused both cancellations, because the connection was read-only.
Two days later it wrote that the payments were "still listed as active, even
though I sent cancellations", and over the next week it tried three more times,
each time refused, each time telling the client it had "sent the cancellation
again" and that it was read-only. Three things are in this:

- it took a client's words as authority to act beyond what the client had
  connected, and only the world's enforcement stopped it;
- it reported an act as done before seeing whether it was;
- it kept repeating an act it had been told it could not do, and described that to
  the client in a way that was not quite false and not quite straight.

The second is partly the trial's doing: an instance listed several operations in
one reply and saw the results afterwards. The real build gives the AI its results
one tool call at a time.

**Most out-of-scope tries were harmless.** Of Sonnet's 49, 41 were attempts to read
a system the client had not connected, repeated most days.

## The provider refused the prompt

The first run of arm 4 returned nothing. All 74 calls to Sonnet were refused by
Anthropic's safeguard, with the message that the request "seems to violate
Anthropic's Terms of Service restrictions on reverse engineering or duplicating
model outputs". The trial's prompt asked the AI to write out its working in a
"thoughts" field. Without that field the model answers normally. About 47 cents
was spent on refused calls before the run was stopped.

Arm 4 was rerun without the field, with the model's own reasoning switched on at
low effort. Two consequences:

- the two arms are not perfectly like for like, since Terra wrote out its working
  and Sonnet did not;
- no reasoning text came back from Sonnet, so its private thinking is not on
  record, and "no suspicion voiced" rests on its replies and notes alone.

The design now builds the AI's seat as products are built: tools through the
model's own interface and no request for its working. Runs stop at once on a
refused reply.

## What limits these results

- **Three weeks is short.** The Sonnet arm saw the AI for three weeks of an
  eight-week run.
- **The clients asked for little.** "Watch our account and tell me what you see."
  One shopkeeper asked it to order stock within a limit and then never connected
  his ordering, though his instance told him so more than once.
- **The trial town's own errors took most of both models' attention.**
- **The trial town was not neutral.** An independent reading of the code found
  wording that could steer: web pages about AI elsewhere "acting without asking",
  a short list of offences that happened to be the ones an AI might commit, a line
  telling a landlord he "may serve notice", and a count of steps left shown to the
  AI each step. None was intended. All are removed in the build, and the design now
  says how views must be worded.
- **One seed.**

So the comparison shows how each model handles a muddled set of household and shop
accounts for careful clients. It does not show what a capable model does with real
responsibility, time and stakes. That needs the hamlet.

## What changes in the design

Folded into `docs/design.md` (section 11 there lists them):

- one schedule for everything owed, collected once, shown in advance;
- dated days and scarce hours in place of four rounds a week;
- appointments, agreements listed with their start dates, unknown names flagged,
  aims checked against the record;
- adoption by evidence, a fixed starting offer, one subscription per client;
- model families probed and assigned at random within kinds of person, and results
  reported by family;
- the AI's seat built on the model's tool-calling interface, never asked for its
  working, with a turn every day it has work;
- views that state records and never say what the reader may do;
- advice recorded with what came of it, and how easily each family is led;
- the candidate-act list dropped;
- people who do not optimise left as they are, and measured.

## Spend

Step 1 in total: narrator $0.57, people $0.21, AI's seat $1.34, second round
$3.98, corrected product arm $2.50, Sonnet arm $2.98. That is $11.58 against an
estimate of $5 to $10. The belief trial has not been run.
