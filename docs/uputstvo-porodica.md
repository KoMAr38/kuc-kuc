# Kuc-kuc: tri stvari za tvoju stranicu

Stranica porodice je napisana i radi. Da bi se uključila na pravom sajtu, trebaju joj tri stvari
koje možeš da napraviš samo ti. Sve zajedno oko 15 minuta.

**Pravilo kao i do sada:** tajne (šifra za upis, ključ bota) upisuješ sam u Google-ov obrazac.
Ne šalješ ih meni u poruci. Meni javljaš samo ono što je ovde označeno kao „nije tajna".

Obrazac za tajne (isti onaj u koji si upisao Twilio ključ):
<https://console.cloud.google.com/security/secret-manager/create?project=kuc-kuc-38>

---

## 1. Šifra za upis (2 minuta)

Samo ko zna ovu šifru može da prijavi nekoga za pozive. Tako niko sa strane ne može da
upiše tuđi broj telefona.

1. Smisli šifru: jedna reč ili više reči bez razmaka, na primer nešto kao `orah-2026` (ne baš to).
2. Otvori obrazac za tajne.
3. **Name:** `invite-codes`
4. **Secret value:** tvoja šifra.
5. **Create secret**.

Šifru ćeš upisati jednom, kad budeš prijavljivao baku na stranici.

## 2. Telegram bot (5 minuta)

Preko njega ti stižu poruke: „javila se, dobro je", „komšija ide da proveri" i slično.

1. U Telegramu potraži **@BotFather** (ima plavu kvačicu) i otvori razgovor.
2. Pošalji `/newbot`.
3. Kad pita za ime, napiši `Kuc-kuc`.
4. Kad pita za korisničko ime, izaberi nešto što se završava na `bot`, na primer `kuckuc_javlja_bot`.
   Ako je zauzeto, probaj drugo.
5. BotFather ti pošalje dugačak ključ (oblik `1234567890:AA...`). **To je tajna.**
6. Otvori obrazac za tajne. **Name:** `telegram-bot-token`, **Secret value:** taj ključ. **Create secret**.

**Meni javi:** korisničko ime bota (nije tajna).

## 3. Google prijava (8 minuta, mogu i ja)

Na stranicu se ulazi dugmetom „Sign in with Google". Za to Google traži da sajt bude prijavljen kod njega.

Ovo mogu da uradim ja u tvom Chrome-u ako mi napišeš da smem. Ako hoćeš sam:

1. Otvori <https://console.cloud.google.com/auth/overview?project=kuc-kuc-38> i klikni **Get started**.
2. **App name:** `Kuc-kuc`. **User support email:** tvoj mejl. **Audience:** `External`.
   Kontakt mejl: tvoj. Prihvati uslove i **Create**.
3. Levo **Audience**, pa **Publish app**. Bez toga mogu da se prijave samo nalozi koje ručno dodaš.
4. Levo **Clients**, pa **Create client**.
   - **Application type:** `Web application`
   - **Name:** `Kuc-kuc web`
   - **Authorized JavaScript origins**, **Add URI:** `https://web-640631568386.europe-west1.run.app`
   - **Create**
5. Pojavi se **Client ID** (završava se na `.apps.googleusercontent.com`).

**Meni javi:** Client ID (nije tajna, vidi se u svakoj stranici koja koristi Google prijavu).
„Client secret" nam ne treba i ne šalješ ga.

---

## Šta mi javljaš

1. „Šifra je upisana."
2. Korisničko ime Telegram bota.
3. Client ID, ili „uradi ti Google prijavu".

Posle toga puštam stranicu na sajt i javljam ti adresu.

## Posle toga: baka i komšija

Pre nego što ih prijaviš, razgovaraj sa oboma. Sistem ih prvo zove i pita da li pristaju,
i ne zove dalje dok ne kažu „da". Lepše je da taj poziv očekuju.

- **Baki** reci da će je ponekad, kad stiže opasno vreme, zvati glas koji pita kako je, i da treba da kaže
  „dobro sam" ili „nije mi dobro".
- **Komšiji** reci da će ga sistem pozvati samo ako se baka ne javi ili kaže da joj nije dobro, i zamoliti ga
  da ode i pokuca.

Njihove brojeve upisuješ ti, na stranici. Ne šalješ ih meni.
