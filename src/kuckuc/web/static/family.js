// The family's page: sign in, enrol someone, see what happened, call now, pause.
// All text shown comes from TEXT (Serbian and English) or from the API, and is set with textContent.
(function () {
  "use strict";

  var TEXT = {
    title: { sr: "Tvoja stranica", en: "Your page" },
    intro: { sr: "Prijavi se da vidiš osobe koje čuvaš.", en: "Sign in to see the people you look after." },
    local_note: { sr: "Lokalni pregled: upiši bilo koji mejl.", en: "Local preview: type any email." },
    sign_in: { sr: "Prijavi se", en: "Sign in" },
    sign_in_off: { sr: "Prijava još nije uključena.", en: "Sign-in is not switched on yet." },
    invite_only: {
      sr: "Kuc-kuc je za sada samo za pozvane porodice: prijaviti se mogu samo one koje su dobile poziv.",
      en: "Kuc-kuc is a pilot by invitation: only families who have been invited can sign in."
    },
    sign_out: { sr: "Odjavi se", en: "Sign out" },
    signed_in_as: { sr: "Prijavljen:", en: "Signed in as" },
    telegram_title: { sr: "Obaveštenja", en: "Notifications" },
    telegram_connect: { sr: "Poveži Telegram", en: "Connect Telegram" },
    telegram_hint: { sr: "Otvoriće se Telegram; pritisni Start. Poruke o pozivima stižu tamo.", en: "Telegram opens; press Start. Messages about the calls arrive there." },
    telegram_linked: { sr: "Telegram je povezan. Poruke o pozivima stižu tamo.", en: "Telegram is connected. Messages about the calls arrive there." },
    telegram_none: { sr: "Telegram bot još nije podešen.", en: "The Telegram bot is not set up yet." },
    people_title: { sr: "Osobe", en: "People" },
    no_people: { sr: "Još nikog nisi prijavio.", en: "You have not enrolled anyone yet." },
    status_pending_consent: { sr: "čeka pristanak", en: "waiting for consent" },
    status_active: { sr: "aktivno", en: "active" },
    status_paused: { sr: "pauzirano", en: "paused" },
    status_declined: { sr: "bez pristanka", en: "declined" },
    local_time: { sr: "Tamo je sada", en: "Time there now" },
    forecast_title: { sr: "Naredna tri dana", en: "Next three days" },
    forecast_calm: { sr: "Nema opasnog vremena.", en: "No dangerous weather." },
    forecast_unknown: { sr: "Prognoza trenutno nije dostupna.", en: "The forecast is not available right now." },
    hazard_heat: { sr: "velika vrućina", en: "extreme heat" },
    hazard_cold: { sr: "velika hladnoća", en: "extreme cold" },
    hazard_wind: { sr: "jak vetar", en: "strong wind" },
    hazard_snow: { sr: "mnogo snega", en: "heavy snow" },
    call_now: { sr: "Pozovi sada: {name}", en: "Call now: {name}" },
    pause: { sr: "Pauziraj", en: "Pause" },
    resume: { sr: "Nastavi", en: "Resume" },
    intro_again: { sr: "Ponovi uvodni poziv: {name}", en: "Repeat the introduction call: {name}" },
    confirm_late: { sr: "Tamo je sada {time}. Svejedno pozvati: {name}?", en: "It is {time} there now. Call {name} anyway?" },
    yes_call: { sr: "Da, pozovi: {name}", en: "Yes, call {name}" },
    cancel: { sr: "Odustani", en: "Cancel" },
    neighbors: { sr: "Komšije", en: "Neighbours" },
    history: { sr: "Šta se dešavalo", en: "What has happened" },
    no_history: { sr: "Još nema poziva.", en: "No calls yet." },
    play: { sr: "Snimak odgovora", en: "Recorded answer" },
    add_title: { sr: "Dodaj osobu", en: "Add a person" },
    consent_note: { sr: "Odmah posle prijave zovemo osobu i svakog komšiju uvodnim pozivom. Bez izgovorenog „da“ nema daljih poziva.", en: "Right after enrolment we call the person and each neighbour with an introduction. Without a spoken “yes” there are no further calls." },
    f_invite: { sr: "Pozivni kod", en: "Invite code" },
    f_name: { sr: "Ime kako je oslovljavamo u pozivu (na primer: baba Vera)", en: "Name as spoken in the call (for example: grandma Vera)" },
    f_family: { sr: "Ko je uključio službu, kako se to kaže njoj (na primer: vaš unuk Petar)", en: "Who turned the service on, as said to them (for example: your grandson Peter)" },
    f_gender: { sr: "Pol", en: "Gender" },
    female: { sr: "ženski", en: "female" },
    male: { sr: "muški", en: "male" },
    f_phone: { sr: "Telefon (fiksni ili mobilni)", en: "Phone (landline or mobile)" },
    f_lang: { sr: "Jezik poziva", en: "Language of the call" },
    f_place: { sr: "Mesto (samo selo ili grad, bez ulice)", en: "Place (village or town only, no street)" },
    f_neighbors: { sr: "Komšije koje smemo da pozovemo (najviše tri)", en: "Neighbours we may call (three at most)" },
    f_n_name: { sr: "Ime komšije", en: "Neighbour's name" },
    f_n_phone: { sr: "Telefon komšije", en: "Neighbour's phone" },
    add_neighbor: { sr: "Dodaj komšiju", en: "Add a neighbour" },
    submit: { sr: "Prijavi i pozovi radi pristanka", en: "Enrol and call for consent" },
    err_invite: { sr: "Pozivni kod nije ispravan.", en: "The invite code is not valid." },
    err_bad_number: { sr: "Neki broj telefona nije ispravan.", en: "One of the phone numbers is not valid." },
    err_already_enrolled: {
      sr: "Ovaj broj je već prijavljen, pogledaj gore u spisku.",
      en: "This number is already enrolled; see the list above."
    },
    neighbor_again: { sr: "Pozovi ponovo: {name}", en: "Call again: {name}" },
    neighbor_late: { sr: "Tamo je sada {time}.", en: "It is {time} there now." },
    neighbor_now: { sr: "Pozovi sada: {name}", en: "Call now: {name}" },
    neighbor_morning: { sr: "Ujutru u 8", en: "At 8 in the morning" },
    neighbor_declined_confirm: {
      sr: "Snimljeno je „ne”. Pozovi ponovo samo ako ste se u međuvremenu dogovorili.",
      en: "A \u201cno\u201d was recorded. Call again only if you have talked since and they agreed."
    },
    neighbor_agreed: { sr: "Dogovorili smo se, pozovi: {name}", en: "We have talked, call {name}" },
    err_telegram_first: {
      sr: "Prvo poveži Telegram (gore, u delu Obaveštenja): tamo ti stižu sve poruke o pozivima.",
      en: "Connect Telegram first (Notifications, above): every message about the calls arrives there."
    },
    err_place: { sr: "Izaberi mesto sa spiska.", en: "Pick the place from the list." },
    err_busy: { sr: "Poziv je već u toku ili zakazan.", en: "A call is already under way or planned." },
    err_not_active: { sr: "Osoba još nije dala pristanak ili je pauzirana.", en: "The person has not consented yet, or is paused." },
    err_generic: { sr: "Nešto nije uspelo. Pokušaj ponovo.", en: "Something went wrong. Try again." },
    err_too_many: {
      sr: "Već pratiš najveći broj osoba za jedan nalog.",
      en: "You already follow the most people one account can."
    },
    err_not_pending: {
      sr: "Pristanak je već dat ili odbijen; ovaj poziv više nije potreban.",
      en: "Consent was already given or refused; this call is no longer needed."
    },
    started: { sr: "Pokrenuto. Telefon će zazvoniti za nekoliko sekundi.", en: "Started. The phone will ring in a few seconds." },
    enrolled: { sr: "Prijavljeno. Uvodni pozivi su krenuli.", en: "Enrolled. The introduction calls are on their way." },
    enrolled_later: {
      sr: "Prijavljeno. Tamo je sada noć, pa uvodni pozivi kreću u {time} po tamošnjem vremenu.",
      en: "Enrolled. It is night there now, so the introduction calls start at {time} local time."
    },
    intro_later: {
      sr: "Tamo je sada noć, pa uvodni poziv kreće u {time} po tamošnjem vremenu.",
      en: "It is night there now, so the introduction call starts at {time} local time."
    },
    kind_intro: { sr: "Uvodni poziv", en: "Introduction" },
    kind_intro_neighbor: { sr: "Uvodni poziv komšiji", en: "Introduction to a neighbour" },
    kind_headsup: { sr: "Najava", en: "Heads-up" },
    kind_check: { sr: "Provera", en: "Check" },
    kind_manual: { sr: "Provera na zahtev", en: "Check on request" },
    r_ok: { sr: "dobro je", en: "all is well" },
    r_ok_by_neighbor: { sr: "komšija javlja da je dobro", en: "the neighbour reports all is well" },
    r_needs_help: { sr: "treba pomoć", en: "help is needed" },
    r_unreachable: { sr: "niko nije dostupan", en: "nobody could be reached" },
    r_unconfirmed: { sr: "komšija se nije javio posle odlaska", en: "the neighbour did not report back" },
    r_not_delivered: { sr: "nije preneto", en: "not delivered" },
    r_consented: { sr: "pristanak dat", en: "consent given" },
    r_declined: { sr: "pristanak nije dat", en: "consent not given" },
    r_no_consent: { sr: "bez odgovora", en: "no answer" },
    r_blocked: { sr: "pozivi su zaustavljeni", en: "calls are stopped" },
    r_running: { sr: "u toku", en: "in progress" },
    r_scheduled: { sr: "zakazano", en: "scheduled" },
    e_episode_started: { sr: "Počelo", en: "Started" },
    e_episode_scheduled: { sr: "Zakazano", en: "Scheduled" },
    e_call_queued: { sr: "Poziv u redu", en: "Call queued" },
    e_call_placed: { sr: "Zovemo", en: "Calling" },
    e_call_answered: { sr: "Podignuta slušalica", en: "Picked up" },
    e_answer_interpreted: { sr: "Odgovor", en: "Answer" },
    e_family_notified: { sr: "Poruka porodici", en: "Family notified" },
    e_call_refused: {
      sr: "Mreža je odbila broj, telefon nije zazvonio",
      en: "The network refused the number; the phone did not ring"
    },
    e_family_not_notified: {
      sr: "Poruka porodici NIJE stigla (Telegram nije povezan ili nije dostupan)",
      en: "Message to the family NOT delivered (Telegram not connected or not reachable)"
    },
    e_episode_closed: { sr: "Kraj", en: "Done" },
    e_call_missed: { sr: "Bez odgovora", en: "No answer" },
    e_call_hung_up: { sr: "Prekinuta veza", en: "Hung up" },
    e_call_failed: { sr: "Poziv nije prošao", en: "The call failed" },
    e_call_blocked: { sr: "Poziv zaustavljen", en: "Call blocked" },
    role_person: { sr: "osoba", en: "person" },
    role_neighbor: { sr: "komšija", en: "neighbour" },
    a_ok: { sr: "dobro", en: "fine" },
    a_needs_help: { sr: "treba pomoć", en: "needs help" },
    a_yes: { sr: "da", en: "yes" },
    a_no: { sr: "ne", en: "no" },
    a_unclear: { sr: "nejasno", en: "unclear" },
    a_no_answer: { sr: "bez odgovora", en: "no answer" }
  };

  var app = document.getElementById("family-app");
  // Until when an introduction may ring where the person lives; later, the family is asked first.
  var INTRO_START = Number(app.getAttribute("data-intro-start")) || 8;
  var INTRO_END = Number(app.getAttribute("data-intro-end")) || 23;
  if (!app) {
    return;
  }
  var $ = function (id) {
    return document.getElementById(id);
  };
  var state = { me: null, lang: "en", place: null, timer: null, shape: "", seen: {} };
  try {
    var stored = localStorage.getItem("kuckuc-lang");
    if (stored) {
      state.lang = stored === "sr" ? "sr" : "en";
    } else {
      state.lang = (navigator.language || "en").slice(0, 2) === "sr" ? "sr" : "en";
    }
  } catch (e) {
    state.lang = "en";
  }

  function t(key, values) {
    var entry = TEXT[key];
    var text = entry ? entry[state.lang] || entry.en : key;
    Object.keys(values || {}).forEach(function (name) {
      text = text.replace("{" + name + "}", values[name]);
    });
    return text;
  }

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) {
      node.className = className;
    }
    if (text !== undefined && text !== null) {
      node.textContent = text;
    }
    return node;
  }

  function say(text, kind) {
    var box = $("family-message");
    box.textContent = text || "";
    if (kind) {
      box.setAttribute("data-kind", kind);
    } else {
      box.removeAttribute("data-kind");
    }
  }

  function api(method, path, body) {
    return fetch(path, {
      method: method,
      headers: body ? { "Content-Type": "application/json" } : {},
      body: body ? JSON.stringify(body) : undefined,
      credentials: "same-origin"
    }).then(function (response) {
      return response.json().then(
        function (data) {
          return { ok: response.ok, status: response.status, data: data };
        },
        function () {
          return { ok: response.ok, status: response.status, data: {} };
        }
      );
    }, function () {
      // No connection: answer like a failed request, so nothing stays locked waiting.
      return { ok: false, status: 0, data: {} };
    });
  }

  function applyLanguage() {
    document.documentElement.lang = state.lang;
    Array.prototype.forEach.call(document.querySelectorAll("[data-t]"), function (node) {
      node.textContent = t(node.getAttribute("data-t"));
    });
    Array.prototype.forEach.call(document.querySelectorAll("[data-lang]"), function (button) {
      button.setAttribute("aria-pressed", button.getAttribute("data-lang") === state.lang ? "true" : "false");
    });
  }

  function timeThere(tz) {
    try {
      return new Intl.DateTimeFormat([], { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: tz }).format(new Date());
    } catch (e) {
      return "";
    }
  }

  function hourThere(tz) {
    return Number(timeThere(tz).slice(0, 2));
  }

  function when(iso) {
    var d = new Date(iso);
    return d.toLocaleDateString([], { day: "numeric", month: "short" }) + " " +
      d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
  }

  // --- one person ----------------------------------------------------------------------------

  // Who a call went to, by name: "person" and "neighbour" were read as the wrong people.
  function who(person, role) {
    if (role === "person") {
      return person.name;
    }
    if (role === "neighbor" && person.neighbors.length === 1) {
      return person.neighbors[0].name;
    }
    return t("role_" + role);
  }

  function eventText(event, person) {
    var p = event.public || {};
    var text = t("e_" + event.type);
    if (p.role) {
      text += " (" + who(person, p.role) + ")";
    }
    if (event.type === "answer_interpreted") {
      text += ": " + t("a_" + p.answer) + (p.transcript ? " · “" + p.transcript + "”" : "");
    } else if (event.type === "episode_closed") {
      text += ": " + t("r_" + p.result);
    } else if (event.type === "call_queued" && Number(p.delay_s) > 0) {
      text += " · " + Math.round(Number(p.delay_s) / 60) + " min";
    }
    return text;
  }

  // ``opened``: ids of the episodes whose details were open, kept when the history is redrawn.
  function renderEpisodes(box, person, episodes, opened) {
    box.textContent = "";
    box.appendChild(el("h4", null, t("history")));
    if (!episodes.length) {
      box.appendChild(el("p", "fine-print", t("no_history")));
      return false;
    }
    var running = false;
    episodes.forEach(function (episode, index) {
      var details = el("details", "episode");
      details.dataset.id = episode.id;
      details.open = opened ? !!opened[episode.id] : index === 0;
      var result = episode.result ? t("r_" + episode.result) : t(episode.state === "scheduled" ? "r_scheduled" : "r_running");
      if (!episode.result && episode.state !== "scheduled") {
        running = true;
      }
      var summary = el("summary", null, when(episode.created_at) + " · " + t("kind_" + episode.kind) + " · " + result);
      details.appendChild(summary);
      var list = el("ol", "timeline");
      episode.events.forEach(function (event) {
        var row = el("li");
        var time = el("time", null, new Date(event.at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }));
        time.dateTime = event.at;
        row.appendChild(time);
        row.appendChild(el("span", null, eventText(event, person)));
        list.appendChild(row);
      });
      details.appendChild(list);
      episode.calls.forEach(function (call) {
        if (!call.has_recording) {
          return;
        }
        var wrap = el("p", "recording");
        wrap.appendChild(el("span", null, t("play") + " (" + who(person, call.role) + "): "));
        var audio = el("audio");
        audio.controls = true;
        audio.preload = "none";
        audio.src = "/api/family/recordings/" + encodeURIComponent(call.id);
        wrap.appendChild(audio);
        details.appendChild(wrap);
      });
      box.appendChild(details);
    });
    return running;
  }

  function loadEpisodes(person, box) {
    return api("GET", "/api/family/persons/" + person.id + "/episodes").then(function (result) {
      if (!result.ok) {
        return false;
      }
      state.seen[person.id] = JSON.stringify(result.data);
      return renderEpisodes(box, person, result.data);
    });
  }

  function isRunning(episodes) {
    return episodes.some(function (episode) {
      return !episode.result && episode.state !== "scheduled";
    });
  }

  function loadForecast(person, box) {
    api("GET", "/api/family/persons/" + person.id + "/forecast").then(function (result) {
      box.textContent = "";
      box.appendChild(el("strong", null, t("forecast_title") + ": "));
      if (!result.ok || !result.data.days || !result.data.days.length) {
        box.appendChild(el("span", null, t("forecast_unknown")));
        return;
      }
      var hazards = result.data.days.filter(function (day) {
        return day.hazard;
      });
      if (!hazards.length) {
        box.appendChild(el("span", null, t("forecast_calm")));
        return;
      }
      box.appendChild(
        el("span", "urgent", hazards.map(function (day) {
          return new Date(day.day + "T12:00:00").toLocaleDateString([], { weekday: "long" }) + ": " + t("hazard_" + day.hazard);
        }).join("; "))
      );
    });
  }

  function act(person, path, body, okText) {
    say("");
    // Every button waits for the reply: a second click used to start a second call.
    var buttons = Array.prototype.slice.call(document.querySelectorAll("#people button"));
    buttons.forEach(function (button) {
      button.disabled = true;
    });
    return api("POST", "/api/family/persons/" + person.id + path, body).then(function (result) {
      if (result.ok) {
        var later = result.data && result.data.intro_at_local;
        say(later ? t("intro_later", { time: later }) : okText || "");
        return refresh();
      }
      buttons.forEach(function (button) {
        button.disabled = false;
      });
      var key = "err_" + (result.data.code || "generic");
      say(TEXT[key] ? t(key) : t("err_generic"), "error");
      return null;
    });
  }

  function personCard(person) {
    var card = el("article", "card person");
    var head = el("header");
    head.appendChild(el("h3", null, person.name));
    head.appendChild(el("span", "chip chip-" + person.status, t("status_" + person.status)));
    card.appendChild(head);
    card.appendChild(el("p", "fine-print", person.place + " · " + person.phone + " · " + t("local_time") + " " + timeThere(person.tz)));

    var forecast = el("p", "forecast");
    card.appendChild(forecast);
    loadForecast(person, forecast);

    var actions = el("div", "row");
    var confirmBox = el("p", "confirm");
    confirmBox.hidden = true;
    if (person.status === "active") {
      var call = el("button", "button", t("call_now", { name: person.name }));
      call.type = "button";
      call.addEventListener("click", function () {
        var hour = hourThere(person.tz);
        if (hour >= 8 && hour < 20) {
          act(person, "/call-now", null, t("started"));
          return;
        }
        // Outside daytime there: ask first, on the page, with the time it is for them.
        confirmBox.textContent = "";
        confirmBox.appendChild(el("span", null, t("confirm_late", { time: timeThere(person.tz), name: person.name }) + " "));
        var yes = el("button", "button", t("yes_call", { name: person.name }));
        yes.type = "button";
        yes.addEventListener("click", function () {
          confirmBox.hidden = true;
          act(person, "/call-now", null, t("started"));
        });
        var no = el("button", "link-button", t("cancel"));
        no.type = "button";
        no.addEventListener("click", function () {
          confirmBox.hidden = true;
        });
        confirmBox.appendChild(yes);
        confirmBox.appendChild(no);
        confirmBox.hidden = false;
      });
      actions.appendChild(call);
    }
    if (person.status === "active" || person.status === "paused") {
      var pause = el("button", "button quiet", t(person.status === "paused" ? "resume" : "pause"));
      pause.type = "button";
      pause.addEventListener("click", function () {
        act(person, "/pause", { paused: person.status !== "paused" });
      });
      actions.appendChild(pause);
    }
    if (person.status === "pending_consent") {
      var again = el("button", "button quiet", t("intro_again", { name: person.name }));
      again.type = "button";
      again.addEventListener("click", function () {
        act(person, "/intro", null, t("started"));
      });
      actions.appendChild(again);
    }
    card.appendChild(actions);
    card.appendChild(confirmBox);

    if (person.neighbors.length) {
      card.appendChild(el("h4", null, t("neighbors")));
      var list = el("ul", "neighbors");
      person.neighbors.forEach(function (neighbor) {
        var row = el("li", null, neighbor.name + " · " + neighbor.phone + " · ");
        row.appendChild(el("span", "chip chip-" + neighbor.status, t("status_" + neighbor.status)));
        if (neighbor.status === "pending_consent" || neighbor.status === "declined") {
          // A neighbour who missed the introduction, or whose "no" was recorded: the family talks to them
          // first, then asks again. A recorded "no" needs the family to say they agreed.
          var again = el("button", "link-button", t("neighbor_again", { name: neighbor.name }));
          again.type = "button";
          var path = "/neighbors/" + neighbor.id + "/intro";
          var agreed = neighbor.status === "declined" ? { agreed: true } : {};
          var call = function (extra, shown) {
            var hour = hourThere(person.tz);
            if (hour >= INTRO_START && hour < INTRO_END) {
              act(person, path, extra, t("started"));
              return;
            }
            // Night there: the family chooses. They may just have talked to the neighbour, who is waiting.
            var choice = el("span", "confirm", " " + t("neighbor_late", { time: timeThere(person.tz) }) + " ");
            var now = el("button", "button", t("neighbor_now", { name: neighbor.name }));
            now.type = "button";
            now.addEventListener("click", function () {
              act(person, path, Object.assign({ now: true }, extra), t("started"));
            });
            var morning = el("button", "link-button", t("neighbor_morning"));
            morning.type = "button";
            morning.addEventListener("click", function () {
              act(person, path, extra, t("started"));
            });
            choice.appendChild(now);
            choice.appendChild(document.createTextNode(" "));
            choice.appendChild(morning);
            shown.replaceWith(choice);
          };
          again.addEventListener("click", function () {
            if (neighbor.status !== "declined") {
              call(agreed, again);
              return;
            }
            var ask = el("span", "confirm", " " + t("neighbor_declined_confirm") + " ");
            var yes = el("button", "button", t("neighbor_agreed", { name: neighbor.name }));
            yes.type = "button";
            yes.addEventListener("click", function () {
              call({ agreed: true }, ask);
            });
            ask.appendChild(yes);
            again.replaceWith(ask);
          });
          row.appendChild(document.createTextNode(" "));
          row.appendChild(again);
        }
        list.appendChild(row);
      });
      card.appendChild(list);
    }

    var history = el("div", "history");
    card.appendChild(history);
    person._history = history;
    return card;
  }

  // --- the whole page ------------------------------------------------------------------------

  function renderTelegram(me) {
    var box = $("telegram-state");
    box.textContent = "";
    if (me.telegram_linked) {
      box.textContent = t("telegram_linked");
    } else if (me.telegram_link_url) {
      var link = el("a", "button", t("telegram_connect"));
      link.href = me.telegram_link_url;
      link.rel = "noopener";
      link.target = "_blank";
      box.appendChild(link);
      box.appendChild(el("span", "fine-print", " " + t("telegram_hint")));
    } else {
      box.textContent = t("telegram_none");
    }
  }

  function render() {
    var me = state.me;
    $("signed-out").hidden = !!me;
    $("signed-in").hidden = !me;
    applyLanguage();
    if (!me) {
      return;
    }
    $("me-email").textContent = me.email;
    renderTelegram(me);
    var people = $("people");
    people.textContent = "";
    if (!me.persons.length) {
      people.appendChild(el("p", "fine-print", t("no_people")));
      $("add-person").open = true;
    }
    state.shape = JSON.stringify(me);
    state.seen = {};
    var pending = me.persons.map(function (person) {
      people.appendChild(personCard(person));
      return loadEpisodes(person, person._history);
    });
    Promise.all(pending).then(function (running) {
      keepWatching(running.some(Boolean));
    });
  }

  // While a call is in progress, look again every few seconds so the timeline fills in by itself.
  function keepWatching(running) {
    clearTimeout(state.timer);
    var waiting = state.me && state.me.persons.some(function (person) {
      return person.status === "pending_consent";
    });
    if (running || waiting) {
      state.timer = setTimeout(poll, 4000);
    }
  }

  // Only what changed is drawn again. Rebuilding every card every few seconds stopped a recording
  // after four seconds, closed a question before it could be answered, and asked for the forecast again.
  function poll() {
    api("GET", "/api/family/me").then(function (result) {
      if (!result.ok) {
        keepWatching(true); // a passing error: look again later
        return;
      }
      if (JSON.stringify(result.data) !== state.shape) {
        state.me = result.data; // a consent, a pause, a new neighbour: the cards themselves change
        render();
        return;
      }
      var checks = state.me.persons.map(function (person) {
        return api("GET", "/api/family/persons/" + person.id + "/episodes").then(function (reply) {
          if (!reply.ok) {
            return true;
          }
          var json = JSON.stringify(reply.data);
          var box = person._history;
          var playing = Array.prototype.some.call(box.querySelectorAll("audio"), function (audio) {
            return !audio.paused;
          });
          if (json === state.seen[person.id] || playing) {
            return isRunning(reply.data); // unchanged, or someone is listening: redraw later
          }
          var opened = {};
          Array.prototype.forEach.call(box.querySelectorAll("details[data-id]"), function (details) {
            opened[details.dataset.id] = details.open;
          });
          if (reply.data.length && !(reply.data[0].id in opened)) {
            opened[reply.data[0].id] = true; // a new episode opens, as on a fresh page
          }
          state.seen[person.id] = json;
          return renderEpisodes(box, person, reply.data, opened);
        });
      });
      Promise.all(checks).then(function (running) {
        keepWatching(running.some(Boolean));
      });
    });
  }

  function refresh() {
    return api("GET", "/api/family/me").then(function (result) {
      state.me = result.ok ? result.data : null;
      if (state.me && !hasStoredLanguage()) {
        state.lang = state.me.lang === "sr" ? "sr" : "en";
      }
      render();
    });
  }

  function hasStoredLanguage() {
    try {
      return !!localStorage.getItem("kuckuc-lang");
    } catch (e) {
      return false;
    }
  }

  function signIn(credential) {
    api("POST", "/api/auth/google", { credential: credential }).then(function (result) {
      if (result.ok) {
        refresh();
      } else {
        $("signed-out").appendChild(el("p", "urgent", t("err_generic")));
      }
    });
  }

  // --- wiring --------------------------------------------------------------------------------

  Array.prototype.forEach.call(document.querySelectorAll("[data-lang]"), function (button) {
    button.addEventListener("click", function () {
      state.lang = button.getAttribute("data-lang");
      try {
        localStorage.setItem("kuckuc-lang", state.lang);
      } catch (e) {
        /* the choice just lasts for this visit */
      }
      if (state.me) {
        api("POST", "/api/family/lang", { lang: state.lang });
      }
      render();
    });
  });

  $("sign-out").addEventListener("click", function () {
    api("POST", "/api/auth/logout").then(refresh);
  });

  var local = $("local-sign-in");
  if (local) {
    local.addEventListener("submit", function (event) {
      event.preventDefault();
      signIn($("local-email").value.trim());
    });
  }

  var clientId = app.getAttribute("data-client-id");
  if (clientId) {
    var waitForGoogle = setInterval(function () {
      if (!(window.google && google.accounts && google.accounts.id)) {
        return;
      }
      clearInterval(waitForGoogle);
      google.accounts.id.initialize({
        client_id: clientId,
        callback: function (response) {
          signIn(response.credential);
        }
      });
      google.accounts.id.renderButton($("google-button"), { theme: "outline", size: "large", text: "signin_with" });
    }, 200);
  }

  // Place search: the server asks the geocoding service, so the browser talks only to this site.
  var search = $("place-search");
  var results = $("place-results");
  var searchTimer = null;
  search.addEventListener("input", function () {
    state.place = null;
    clearTimeout(searchTimer);
    var query = search.value.trim();
    if (query.length < 2) {
      results.textContent = "";
      return;
    }
    searchTimer = setTimeout(function () {
      api("GET", "/api/family/places?q=" + encodeURIComponent(query)).then(function (result) {
        results.textContent = "";
        (result.ok ? result.data : []).forEach(function (place) {
          var item = el("li");
          var label = place.name + (place.region ? ", " + place.region : "") + (place.country ? ", " + place.country : "");
          var pick = el("button", "link-button", label);
          pick.type = "button";
          pick.addEventListener("click", function () {
            state.place = place;
            search.value = place.name;
            results.textContent = "";
          });
          item.appendChild(pick);
          results.appendChild(item);
        });
      });
    }, 350);
  });

  function neighborRow() {
    var row = el("div", "row neighbor-row");
    var name = el("input");
    name.type = "text";
    name.maxLength = 60;
    name.setAttribute("data-field", "name");
    name.setAttribute("aria-label", t("f_n_name"));
    name.placeholder = t("f_n_name");
    var phone = el("input");
    phone.type = "tel";
    phone.setAttribute("data-field", "phone");
    phone.setAttribute("aria-label", t("f_n_phone"));
    phone.placeholder = t("f_n_phone");
    row.appendChild(name);
    row.appendChild(phone);
    return row;
  }

  $("add-neighbor").addEventListener("click", function () {
    var rows = $("neighbor-rows");
    if (rows.children.length < 3) {
      rows.appendChild(neighborRow());
    }
    if (rows.children.length >= 3) {
      $("add-neighbor").hidden = true;
    }
  });

  $("enrol-form").addEventListener("submit", function (event) {
    event.preventDefault();
    var form = event.target;
    if (!state.place) {
      say(t("err_place"), "error");
      return;
    }
    var submit = form.querySelector('button[type="submit"]');
    if (submit.disabled) {
      return;
    }
    // Locked until the answer is back: a second click must not enrol the same person twice.
    submit.disabled = true;
    var neighbors = [];
    Array.prototype.forEach.call($("neighbor-rows").children, function (row) {
      var name = row.querySelector('[data-field="name"]').value.trim();
      var phone = row.querySelector('[data-field="phone"]').value.trim();
      if (name && phone) {
        neighbors.push({ name: name, phone: phone });
      }
    });
    api("POST", "/api/family/persons", {
      invite_code: form.invite_code.value.trim(),
      name: form.name.value.trim(),
      family_name: form.family_name.value.trim(),
      gender: form.gender.value,
      phone: form.phone.value.trim(),
      lang: form.lang.value,
      lat: state.place.lat,
      lon: state.place.lon,
      place: state.place.name,
      neighbors: neighbors
    }).then(function (result) {
      submit.disabled = false;
      if (!result.ok) {
        var code = result.data.code;
        say(TEXT["err_" + code] ? t("err_" + code) : t("err_generic"), "error");
        return;
      }
      form.reset();
      state.place = null;
      $("neighbor-rows").textContent = "";
      $("add-neighbor").hidden = false;
      $("add-person").open = false;
      var later = result.data && result.data.intro_at_local;
      say(later ? t("enrolled_later", { time: later }) : t("enrolled"));
      refresh();
    });
  });

  applyLanguage();
  refresh();
})();
