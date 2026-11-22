# German calls and the "automated call" notice: design

Approved by the user on 22 Nov 2026 ("Da, svi jezici").

## Why
Market research (22 Nov) found that the first paying customers are most likely German home-alarm services
(Hausnotruf providers) and cities that run a Hitzetelefon. Serbia stays as a donor-funded reference pilot.
Selling in Germany needs calls in German. Every call in the EU also has to say that it is automated
(EU AI Act, Art. 50, in force since 2 Aug 2026).

## Goals
- Every call, in every language, says at the start that it is an automated call.
- The welcome call, in every language, also says:
  - that a computer listens to the answer;
  - that the recording is deleted after seven days (the bucket lifecycle rule);
  - that Kuc-kuc is not an emergency service, and which number to call instead. The number follows the
    country of the person's phone, not the language: Serbia 194, Europe 112, US/Canada 911, UK 999,
    Australia 000, and plain words where the country is unknown. A Serbian speaker in Germany is told 112.
    The family's urgent Serbian messages name the same number.
- Both welcome calls (to the person and to a neighbour) say that a computer assesses the answer.
- German (de) is a language a family can choose for the person: every sentence said on the phone has a
  German version, spoken by the voice de-DE-Chirp3-HD-Leda at rate 0.9.
- Gemini gets German yes/no examples for German calls.

## Non-goals
- The family page and the Telegram messages stay in Serbian and English. The family-facing language is a
  separate type from the call language.
- A German caller ID or number, a second phone provider, or a German weather service. Those come later and
  need accounts and money.

## Anti-goals
- Any change to the instructions Gemini gets for Serbian or English calls. They were measured on real audio
  on 17 Nov. A test pins them byte for byte.
- A German person called before German answers have been measured on real German voices.

## Approaches
1. **Chosen: table-driven texts per language in `scripts.py`.** Each language gets its own render function
   and its own closing table. The call language (`Lang`: sr, en, de) is a separate type from the
   family-facing language (`PageLang`: sr, en).
2. Adding German to every family-facing text as well: rejected for now. The buyers are organisations, and
   their staff view is a later step.
3. Generating German at call time by translating the English text with Gemini: rejected. The words would be
   untested and could change from call to call.

## Left to the builder
- German wording follows the existing English and Serbian lines: formal "Sie", "Auf Wiederhören" on the phone.
- Numbers that are said aloud are written as words, so the voice says them the way people do
  ("sto devedeset četiri", "eins eins zwei").
