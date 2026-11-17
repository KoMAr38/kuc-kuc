# Product Hunt launch: texts

Status: **scheduled on 13 November 2026** for Friday 20 November 2026, 12:01 AM Pacific time
(09:01 in Central Europe), and entered in "Build for Good with Cloud Run". The owner approved
the name, tagline, description and the hackathon answer. The post and its date can still be edited.
The first comment is posted (approved by the owner on 17 November).

## Fields

| Field | Text | Length |
|---|---|---|
| Name | Kuc-kuc | |
| Tagline | A knock on the door when dangerous weather comes | 48 of 60 |
| Link | https://web-640631568386.europe-west1.run.app | |
| Topics | Social Impact, Artificial Intelligence, Health | |
| Pricing | Free | |

**Description** (246 characters)

> When dangerous weather is forecast, Kuc-kuc phones an older person who lives alone and asks if they are okay. No answer, or “I’m not well”? It phones a neighbour to go and knock. The family sees every step. Works on a landline. Runs on Cloud Run.

## Hackathon answer (asked when scheduling for Nov 20, max 1000 characters)

Question: how does the product turn technology into positive change, what was deployed on
Cloud Run, and how does it work together?

Submitted as written (962 characters), approved by the owner:

> My grandmother lives alone in a village in southern Serbia, with a landline and no smartphone. Kuc-kuc (“knock-knock”) is for people like her. When a heatwave or hard frost is forecast, it phones her and asks if she is all right. If she does not answer, or says she is unwell, it phones a neighbour and asks them to knock on her door. Her family, far away, sees every step. Any doubt counts as a problem.
>
> On Cloud Run: three microservices and a job, all scaling to zero. web is the site and live demo. engine is one pure state machine fed by Pub/Sub. voice is the only code that touches telephony: it speaks with a Chirp 3 HD voice and sends the recorded answer to Gemini on Vertex AI. A forecast job runs every 3 hours from Cloud Scheduler. Services never call each other: events go through Pub/Sub, and every wait (call again in 10 minutes, ask the neighbour in 20) is a Cloud Tasks task, so nothing stays awake. Used: web app, microservices, data processing.

It says the family sees every step: true at launch only if the family page and the Telegram
notices are live by then. If they slip, change that sentence in the scheduler before Nov 20.

## First comment (maker's comment)

Status: **approved by the owner and posted on the launch page on 17 November 2026** (scheduled launch, before going live). Says only what is live on 17 November (watchdog included).
Numbers from the load test of 13 November. The village is not named.

> Hi Product Hunt! "Kuc-kuc" is Serbian for "knock-knock".
>
> My grandmother lives alone in a village in southern Serbia. She has a landline, no smartphone, and a neighbour across the road. Her family is far away. Heatwaves and hard frosts are when we worry most, and exactly when nobody remembers to call.
>
> **What it does.** Every three hours Kuc-kuc reads the forecast for each person's village. When heat, frost, wind or snow crosses a threshold, it phones them the evening before with a warning, and on the day itself with one question: are you all right? They answer out loud, on any phone. If they don't pick up, or say they're not well, it phones the neighbour and asks them to go and knock, then calls the neighbour back 20 minutes later to ask how she is. The family sees every step on a web page and on Telegram. One rule runs through all of it: any doubt counts as a problem, never as "fine". And a forecast never rings anyone's phone before 08:00 or after 20:00 their time.
>
> **Try it.** The demo on the site calls your own phone, in English. Don't pick up, and a moment later it calls you back as the neighbour.
>
> **How it is built on Cloud Run.** Three services, all scaling to zero:
> - `web`: the site, the live demo and the family page.
> - `engine`: private (internal ingress only), one pure state machine. It does nothing but react to events from Pub/Sub and timers from Cloud Tasks.
> - `voice`: the only code that touches the phone network. It speaks with a Chirp 3 HD voice (Serbian and English) and sends the recorded answer to Gemini on Vertex AI, which tells "I'm fine" from "I'm not well" from "unclear".
>
> Plus Cloud Run jobs: `forecast-scan`, started by Cloud Scheduler every three hours; `watchdog`, every ten minutes, which closes any check that has stopped moving and tells the family at once, so a lost message never turns into silence; and `loadtest`. Services never call each other: every event goes through Pub/Sub, and every wait ("call again in 10 minutes", "ask the neighbour in 20") is a Cloud Tasks task, so nothing stays awake waiting. State lives in Firestore, recordings in Cloud Storage (deleted after 7 days), secrets in Secret Manager.
>
> **Why scale to zero.** Dangerous weather is rare. On an ordinary day there is nothing to do, so nothing runs. Our load test put 2,000 simulated people in a heatwave at once: 3,008 simulated calls, every case closed in 213 seconds at a peak of 14 instances, and then everything went back to zero.
>
> **Limits, said plainly.** It is not an emergency service. It is a pilot by invitation, because every call costs real money. Its first real user is my grandmother.
>
> The Architecture page on the site shows all of this, live.

## Images

All in `images/`. Upload the logo first, then the gallery in this order:

| File | What it shows |
|---|---|
| `thumbnail.png` | Logo: the door-and-knock mark, 480 x 480 |
| `gallery-1-hero.png` | The headline and what it does in two sentences |
| `gallery-2-how-it-works.png` | The three steps |
| `gallery-3-live-demo.png` | The live demo timeline |
| `gallery-4-architecture.png` | The parts and how they are connected |
| `gallery-5-who-it-is-for.png` | Who it is for, and what it is not |
| `gallery-6-family-page.png` | What the family sees (made-up person and test numbers) |
| `gallery-7-under-load.png` | The load test: 2,000 people, 14 instances, then zero |
| `gallery-1-landing.png` | Optional extra: a screenshot of the real landing page |

The five gallery images are rendered from the pages in `images/src/` by `images/render.py`
(see `images/README.txt`), so their text can be corrected and the images rebuilt.

Uploaded on 17 November 2026 with the owner's go-ahead: the gallery now has seven images
(the five above, then the family page and the load test).
