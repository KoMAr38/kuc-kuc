# Product Hunt gallery

All images are 1270 x 760. The first five are uploaded to the scheduled launch.

| # | File | What it shows | How it is made |
|---|---|---|---|
| logo | `images/thumbnail.png` | The door-and-knock mark, 480 x 480. | Drawn from `web/static/door.svg`. |
| 1 | `images/gallery-1-hero.png` | The promise in one sentence. | `python docs/launch/images/render.py` from `images/src/gallery-1-hero.html`. |
| 2 | `images/gallery-2-how-it-works.png` | Forecast, call, neighbour. | `render.py`, same way. |
| 3 | `images/gallery-3-live-demo.png` | The demo timeline, from the first call to the neighbour's report. | `render.py`; the eight rows are the events the live demo shows. |
| 4 | `images/gallery-4-architecture.png` | Three services, the jobs, Pub/Sub and Cloud Tasks between them. | `render.py`. Arrows must match the site's `/architecture` page: services never call each other. |
| 5 | `images/gallery-5-who-it-is-for.png` | Who it is for and what it is not. | `render.py`. |
| 6 | `images/gallery-6-family-page.png` | What the family sees: the person and the neighbour active, a finished check, the recorded answer. | `python docs/launch/images/capture_family.py`: the local preview with made-up people and Twilio test numbers, never the real page. |
| 7 | `images/gallery-7-under-load.png` | The load test: 2,000 simulated people, 14 instances serving at the peak, zero 13 minutes after the last case closed. | `python docs/launch/images/capture_load.py`: Cloud Monitoring's per-minute instance counts of `engine` and `voice`, kept in `images/src/loadtest-instances.json`. |
| spare | `images/gallery-1-landing.png` | The live landing page. | Screenshot of the site at 1270 x 760. |

`render.py` checks the texts and sizes and writes nothing if a page fails. Look at every PNG at
100% after rendering. Details are in `images/README.txt`.

## Before launch day

Image 4 and the first comment must describe what is deployed on that day. If the family page or
the load test is not live, they must not appear in either.
