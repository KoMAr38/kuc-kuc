# Call budgets per payer: design

Approved by the user on 22 Nov 2026 ("organizacije i limit troška prvo"; the grandmother is paused, so every
payer, the service's own account included, gets a hard cap).

## Why
Pilots are paid and never free (the user's decision, 22 Nov). An organisation pays a fixed season fee, or
call costs plus a fee. Either way, its calls must never cost more than it pays. Calls to Serbia cost
$0.60–0.82 a minute on Twilio, so a cap matters most there.

## Goals
- Every real person belongs to one **payer**: an organisation, or the service's own account (`own`).
  - A payer has a cap per calendar month, a cap per season (start and end dates), or both.
  - Caps are in US dollars, because that is the currency the phone providers bill in.
- **Before a check's first call**, the worst case of the whole check is reserved against every cap of its
  payer.
  - The worst case is every call the escalation could still make, each counted at one minute (calls end
    at 60 seconds): the person's tries, two asks to each neighbour, and two follow-ups.
  - The price per minute follows the country and the line type (landline or mobile) of each number. A
    number missing from the price table counts at the dearest listed price.
  - If the reservation does not fit, the check does not start. The family gets an urgent message that
    calls are paused because the budget is used up. The payer's staff get one urgent message a day.
- **A check that has started always finishes**: its reservation covers every call it can still make.
- **After a check closes**, the watchdog swaps the reservation for what was actually spent: one minute
  for every call that connected (a person, a voicemail or a machine answered), and nothing for an
  unanswered one.
- The payer's staff see on their page what has been spent, and what is reserved, against each cap.
- The invite code a family uses decides the payer. An organisation's code puts its people on its budget.
  The codes in settings put people on `own`.

## Non-goals
- A full organisation view (all people, several staff roles, logo, report). It comes later.
- Billing an organisation, or reading the real price from the provider's invoice.
- Demo and simulated checks: they keep their own daily budget.

## Anti-goals
- A cap that a check can pass because two checks started at the same moment. The reservation is one
  transaction over the budget documents.
- Stopping a check half-way because of money.
- A payer with no budget record whose calls go ahead anyway: no record means no money.

## Approaches
1. **Chosen: reserve the worst case at the start, settle at the close.** It is exact about the guarantee
   ("an escalation always finishes") and safe under parallel starts. The cost is that money stays held
   until the watchdog settles, up to about 10 minutes after the close.
2. Counting each call as it is placed: rejected. A check could run out of money between the call to the
   person and the call to the neighbour.
3. Counting cost from the provider's invoices: rejected for now. It arrives too late to stop a call, and
   it differs per provider.

## Left to the builder
- Payers are created and changed by an admin script that the service owner runs. There is no admin page.
