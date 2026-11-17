// The live demo: verify a phone number, start a run, then show what the system does, row by row.
// Everything shown comes from the timeline API. Text is always set with textContent.
(function () {
  "use strict";

  var REFUSALS = {
    bad_number: "That does not look like a phone number. Include the country code, for example +1 415 555 0100.",
    country: "The live demo can only call numbers in the United States, Canada, the United Kingdom and Norway.",
    number_type: "That is a premium, toll-free or special number. The demo only calls ordinary mobile and home lines.",
    own_number: "That is the number Kuc-kuc calls from. Enter the number of your own phone instead.",
    phone_limit: "This number has had its two demo calls for today. Come back tomorrow.",
    ip_limit: "Too many tries from this network today. Come back tomorrow.",
    budget: "Today's demo budget is spent. Real calls cost real money.",
    ended: "The live demo has ended.",
    low_balance: "The demo is paused so that calls to real people stay paid for.",
    wrong_code: "That code did not match. Check it and try again.",
    session: "Your verification has expired. Verify your number again.",
    verify_failed: "The code could not be sent just now. Try the other way (text or call).",
    forbidden: "This demo run belongs to another session. Verify your number again."
  };
  var OFFER_SAMPLE = { budget: true, ended: true, low_balance: true };

  var HAZARDS = { heat: "extreme heat", cold: "extreme cold", wind: "strong wind", snow: "heavy snow" };
  var ANSWERS = {
    ok: "fine",
    needs_help: "needs help",
    yes: "yes",
    no: "no",
    unclear: "not clear, so treated as a problem",
    no_answer: "no answer"
  };
  var RESULTS = {
    ok: "all is well. The family has been told.",
    ok_by_neighbor: "the neighbour went over and reports all is well.",
    needs_help: "help is needed. The family got an urgent message.",
    unreachable: "nobody could be reached. The family got an urgent message.",
    unconfirmed: "the neighbour went but did not report back. The family got an urgent message.",
    not_delivered: "the warning could not be delivered. The family has been told.",
    blocked: "calls are switched off right now. The family got an urgent message."
  };
  var NOTICES = {
    call_ok: "“She answered and says she is fine.”",
    call_unwell: "URGENT: “She says she is not well. We are calling the neighbour.”",
    neighbor_called: "“The neighbour is being called and asked to check on her.”",
    neighbor_going: "“The neighbour is on the way. We will ask shortly how it went.”",
    resolved_ok: "“The neighbour reports she is fine.”",
    needs_help_194: "URGENT: “The neighbour reports she needs help. Call the emergency number now.”",
    nobody_reachable: "URGENT: “She is not answering and no neighbour could be reached.”",
    neighbor_unconfirmed: "URGENT: “The neighbour went over but is no longer answering.”",
    call_blocked: "URGENT: “The call was not made because calls are stopped.”"
  };

  function who(p) {
    return p.role === "neighbor" ? "the neighbour (you again, in a second role)" : "you";
  }

  // One entry per timeline event type: text for the row and, optionally, its tone.
  var ROWS = {
    episode_started: function (p) {
      return ["Forecast crossed the threshold: " + (HAZARDS[p.hazard] || "dangerous weather"), "quiet"];
    },
    call_queued: function (p) {
      var wait = Number(p.delay_s || 0);
      return [wait > 0 ? "Next call to " + who(p) + " in " + wait + " seconds" : "Call to " + who(p) + " queued", "quiet"];
    },
    call_placed: function (p) {
      return ["Calling " + who(p) + "…"];
    },
    call_answered: function () {
      return ["Picked up"];
    },
    answer_interpreted: function (p) {
      var heard = p.transcript ? "Heard: “" + p.transcript + "” → " : "Heard → ";
      return [heard + (ANSWERS[p.answer] || p.answer), p.answer === "ok" || p.answer === "yes" ? "ok" : "urgent"];
    },
    family_notified: function (p) {
      return ["Family notified: " + (NOTICES[p.key] || "a message was sent"), p.urgent === "true" ? "urgent" : null];
    },
    episode_closed: function (p) {
      var good = p.result === "ok" || p.result === "ok_by_neighbor";
      return ["Done: " + (RESULTS[p.result] || p.result), good ? "ok" : "urgent"];
    },
    call_missed: function () {
      return ["No answer"];
    },
    call_hung_up: function () {
      return ["Hung up before the question was answered"];
    },
    call_failed: function () {
      return ["The call could not be placed"];
    },
    call_blocked: function (p) {
      return ["Call blocked (" + (p.reason || "limit") + ")", "urgent"];
    }
  };

  var el = {
    phoneForm: document.getElementById("demo-phone-form"),
    phone: document.getElementById("demo-phone"),
    send: document.getElementById("demo-send"),
    codeForm: document.getElementById("demo-code-form"),
    code: document.getElementById("demo-code"),
    verify: document.getElementById("demo-verify"),
    scenarios: document.getElementById("demo-scenarios"),
    message: document.getElementById("demo-message"),
    sample: document.getElementById("demo-sample"),
    timeline: document.getElementById("demo-timeline")
  };
  if (!el.phoneForm) {
    return;
  }

  var state = { token: null, episode: null, after: 0, timer: null, countdown: null, neighbourGoing: false };

  function say(text, kind) {
    el.message.textContent = text || "";
    if (kind) {
      el.message.setAttribute("data-kind", kind);
    } else {
      el.message.removeAttribute("data-kind");
    }
  }

  function refuse(code) {
    say(REFUSALS[code] || "Something went wrong. Reload the page and try again.", "error");
    el.sample.hidden = !OFFER_SAMPLE[code];
    if (code === "session" || code === "forbidden") {
      state.token = null;
      el.scenarios.hidden = true;
      el.codeForm.hidden = true;
    }
  }

  function post(path, body) {
    return fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    }).then(function (response) {
      return response.json().then(
        function (data) {
          return { ok: response.ok, data: data };
        },
        function () {
          return { ok: false, data: {} };
        }
      );
    });
  }

  function busy(button, on) {
    button.disabled = on;
  }

  function scenarioButtons() {
    return el.scenarios.querySelectorAll("button[data-scenario]");
  }

  function stopCountdown() {
    if (state.countdown) {
      clearInterval(state.countdown);
      state.countdown = null;
    }
  }

  // While the system waits before the next call, count the seconds down for the visitor.
  function countDown(seconds, before, after) {
    stopCountdown();
    var left = seconds;
    function show() {
      say(before + left + (left === 1 ? " second" : " seconds") + after);
    }
    show();
    state.countdown = setInterval(function () {
      left -= 1;
      if (left <= 0) {
        stopCountdown();
        say("Calling…");
      } else {
        show();
      }
    }, 1000);
  }

  // A sentence above the timeline that says what is happening to the visitor's phone right now.
  function narrate(event) {
    var p = event.public || {};
    var neighbour = p.role === "neighbor";
    if (event.type === "call_queued" && Number(p.delay_s) > 0) {
      if (neighbour && state.neighbourGoing) {
        countDown(Number(p.delay_s), "The neighbour is on the way. We call back in ", " to ask how she is.");
      } else {
        countDown(Number(p.delay_s), "No answer. Trying again in ", ".");
      }
    } else if (event.type === "call_placed") {
      stopCountdown();
      if (!neighbour) {
        say("Your phone is ringing. Pick up and answer out loud, or let it ring and see what happens.");
      } else if (state.neighbourGoing) {
        say("The follow-up call: you are still the neighbour. Say “she is fine” or “she needs help”.");
      } else {
        say("Now we are calling you again. This time you are the neighbour: say “yes” if you can go and check on her.");
      }
    } else if (event.type === "answer_interpreted" && neighbour && p.answer === "yes") {
      state.neighbourGoing = true;
    } else if (event.type === "call_answered") {
      say("Picked up. Listen, then answer after the question.");
    } else if (event.type === "answer_interpreted") {
      say("Understanding the answer…");
    }
  }

  function addRow(event) {
    var make = ROWS[event.type];
    var parts = make ? make(event.public || {}) : [event.type, "quiet"];
    var item = document.createElement("li");
    var time = document.createElement("time");
    var text = document.createElement("span");
    var at = new Date(event.at);
    time.dateTime = event.at;
    time.textContent = at.toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false
    });
    text.textContent = parts[0];
    if (parts[1]) {
      item.setAttribute("data-tone", parts[1]);
    }
    item.appendChild(time);
    item.appendChild(text);
    el.timeline.appendChild(item);
  }

  function stopPolling() {
    if (state.timer) {
      clearInterval(state.timer);
      state.timer = null;
    }
  }

  function poll() {
    var url =
      "/api/demo/timeline/" + encodeURIComponent(state.episode) +
      "?token=" + encodeURIComponent(state.token) + "&after=" + state.after;
    fetch(url)
      .then(function (response) {
        if (!response.ok) {
          stopPolling();
          refuse(response.status === 403 ? "forbidden" : "");
          return null;
        }
        return response.json();
      })
      .then(function (data) {
        if (!data) {
          return;
        }
        data.events.forEach(function (event) {
          state.after = Math.max(state.after, event.seq);
          addRow(event);
          narrate(event);
        });
        if (data.closed) {
          stopPolling();
          stopCountdown();
          say("That was one complete run. You can try the other answer: pick up, or let it ring.");
          scenarioButtons().forEach(function (button) {
            busy(button, false);
          });
        }
      })
      .catch(function () {
        /* a missed poll is fine; the next one catches up */
      });
  }

  el.phoneForm.addEventListener("submit", function (event) {
    event.preventDefault();
    var channel = el.phoneForm.querySelector('input[name="channel"]:checked').value;
    busy(el.send, true);
    say("Sending the code…");
    post("/api/demo/verify/start", { phone: el.phone.value, channel: channel })
      .then(function (result) {
        busy(el.send, false);
        if (!result.ok) {
          refuse(result.data.code);
          return;
        }
        el.sample.hidden = true;
        el.codeForm.hidden = false;
        el.code.focus();
        say(channel === "call" ? "We are calling you with a code." : "We sent you a code by text message.");
      })
      .catch(function () {
        busy(el.send, false);
        refuse("");
      });
  });

  el.codeForm.addEventListener("submit", function (event) {
    event.preventDefault();
    busy(el.verify, true);
    post("/api/demo/verify/check", { phone: el.phone.value, code: el.code.value })
      .then(function (result) {
        busy(el.verify, false);
        if (!result.ok) {
          refuse(result.data.code);
          return;
        }
        state.token = result.data.token;
        el.codeForm.hidden = true;
        el.scenarios.hidden = false;
        say("Number verified. Pick the weather and keep your phone close.");
      })
      .catch(function () {
        busy(el.verify, false);
        refuse("");
      });
  });

  scenarioButtons().forEach(function (button) {
    button.addEventListener("click", function () {
      if (!state.token) {
        refuse("session");
        return;
      }
      scenarioButtons().forEach(function (other) {
        busy(other, true);
      });
      stopPolling();
      stopCountdown();
      state.neighbourGoing = false;
      el.timeline.textContent = "";
      say("Starting. Your phone should ring in about 15 seconds.");
      post("/api/demo/run", { token: state.token, scenario: button.getAttribute("data-scenario") })
        .then(function (result) {
          if (!result.ok) {
            scenarioButtons().forEach(function (other) {
              busy(other, false);
            });
            refuse(result.data.code);
            return;
          }
          state.episode = result.data.episode_id;
          state.after = 0;
          poll();
          state.timer = setInterval(poll, 1000);
        })
        .catch(function () {
          scenarioButtons().forEach(function (other) {
            busy(other, false);
          });
          refuse("");
        });
    });
  });
})();
