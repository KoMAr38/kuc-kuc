# Kuc-kuc: nalozi koje otvaraš ti

Ovo radiš jednom. Treba ti oko 40 minuta i platna kartica.
Rok: do nedelje 15.11, jer tada ide prvi pravi poziv na tvoj telefon.

**Pravilo za ceo dokument:** lozinke, ključeve i brojeve telefona ne šalješ meni u razgovor.
Meni javljaš samo ono što je izričito navedeno na kraju.

---

## Deo 1: Google Cloud (oko 20 minuta)

### 1.1 Otvori nalog i uključi plaćanje

1. Otvori <https://console.cloud.google.com/> i prijavi se svojim Google nalogom.
2. Prihvati uslove korišćenja.
3. Ako ti ponudi besplatan probni period (dugme **Start free** ili **Try for free**), prihvati ga.
   Tražiće adresu i karticu. Kartica služi za potvrdu identiteta.
4. Ako ti ne ponudi probni period, otvori <https://console.cloud.google.com/billing>,
   klikni **Create account** i unesi karticu.

Koliko će koštati: Google deo za ceo ovaj projekat očekujem ispod 5 dolara, verovatno i nula,
jer servisi rade samo kad neko nešto traži. Skupi deo su telefonski pozivi, a to je Twilio.

### 1.2 Napravi projekat

1. Otvori <https://console.cloud.google.com/projectcreate>.
2. U polje **Project name** upiši `kuc-kuc`.
3. Ispod imena piše **Project ID** (na primer `kuc-kuc-471203`). Zapiši ga. To je jedino što mi javljaš iz ovog dela.
4. Klikni **Create**.
5. Proveri da je plaćanje vezano za projekat: otvori <https://console.cloud.google.com/billing/projects>.
   Pored projekta `kuc-kuc` treba da stoji ime naloga za plaćanje. Ako piše da je plaćanje isključeno,
   klikni tri tačke desno, pa **Change billing**, i izaberi svoj nalog.

### 1.3 Instaliraj Google Cloud CLI

To je program preko kog ja sa tvog računara postavljam servise na Google Cloud.

1. Preuzmi instalaciju sa Google-ove stranice: <https://docs.cloud.google.com/sdk/docs/install-sdk>
   (odeljak Windows, dugme za **Google Cloud CLI installer**).
2. Pokreni preuzeti fajl `GoogleCloudSDKInstaller.exe`. Potpisao ga je Google LLC.
3. Sve opcije ostavi kako jesu i klikći **Next**, pa **Install**.
4. Na poslednjem ekranu skini kvačicu sa **Start Google Cloud SDK Shell** i klikni **Finish**.

### 1.4 Prijavi se u CLI

Otvori novi PowerShell prozor (Start, ukucaj `powershell`, Enter) i pokreni redom ove tri komande.

Kucaj `gcloud.cmd`, ne samo `gcloud`. PowerShell na Windows-u često ne dozvoljava skripte,
pa samo `gcloud` javi crvenu grešku „running scripts is disabled on this system".
Sa `gcloud.cmd` radi bez ikakve izmene podešavanja.

```bash
gcloud.cmd init
```

Otvoriće ti pregledač. Prijavi se istim Google nalogom i klikni **Allow**.
Kad se vratiš u prozor, pitaće te koji projekat: ukucaj broj pored `kuc-kuc`.
Ako pita za podrazumevanu zonu ili region, odgovori `n`.

```bash
gcloud.cmd auth application-default login
```

Opet se otvara pregledač. Isti nalog, opet **Allow**. Ovo daje dozvolu programima na tvom računaru, ne samo komandama.

```bash
gcloud.cmd config get-value project
```

Treba da ispiše tvoj Project ID. Ako ispiše, ovaj deo je gotov.

---

## Deo 2: Twilio (oko 20 minuta)

Twilio je servis koji obavlja same telefonske pozive.

### 2.1 Otvori nalog

1. Otvori <https://www.twilio.com/try-twilio>.
2. Unesi ime, mejl i lozinku koju sam smisliš. Potvrdi mejl, pa potvrdi svoj broj mobilnog kodom iz poruke.
3. Na pitanja o tome šta gradiš odgovori bilo kako (na primer: Voice, sa kodom, Python). To ne menja ništa.

### 2.2 Pređi sa probnog na pravi nalog

Probni nalog može da zove samo brojeve koje si ručno potvrdio i samo u zemlji u kojoj si se prijavio.
Sudije i baba nisu na tom spisku, zato nalog mora da bude pravi.

1. U konzoli <https://console.twilio.com/> klikni traku **Trial** na vrhu ili dugme **Upgrade**.
2. Popuni profil i adresu, unesi karticu i uplati početni iznos od **20 dolara**.
3. **Auto-recharge ostavi isključen.** Tako Twilio ne može sam da povuče još novca sa kartice.
   Dogovorili smo plafon od 75 dolara ukupno, a dopunjuješ ručno kad ti javim da treba.

### 2.3 Kupi jedan američki broj

Sa tog broja će Kuc-kuc zvati. Košta oko 1 do 2 dolara mesečno.

1. U konzoli idi na **Phone Numbers**, pa **Manage**, pa **Buy a number**.
2. Zemlja: **United States**. Pod **Capabilities** ostavi uključeno samo **Voice**.
3. Klikni **Search**, izaberi bilo koji broj i klikni **Buy**.

### 2.4 Dozvoli pozive ka pet zemalja

Twilio podrazumevano blokira pozive u većinu zemalja da bi sprečio zloupotrebu.

1. U konzoli idi na **Voice**, pa **Settings**, pa **Geo permissions**.
2. Uključi ovih pet zemalja i nijednu drugu:
   **United States, Canada, United Kingdom, Norway, Serbia**.
3. Za svaku uključi samo obične opsege (**low-risk**). Skupe (**high-risk**) ostavi isključene.
4. Klikni **Save**.

Ako Srbije nema na spisku ili ne da da se uključi, javi mi. Tada tražimo dozvolu od Twilio podrške,
a to može da potraje dan ili dva, zato je dobro da ovo proveriš što pre.

### 2.5 Napravi Verify servis

Verify šalje kod na telefon posetioca pre demo poziva, tako da niko ne može da pozove tuđi broj.

1. U konzoli idi na **Verify**, pa **Services**, pa **Create new**.
2. **Friendly name:** `Kuc-kuc`. To ime piše u poruci sa kodom.
3. Uključi kanale **SMS** i **Voice** i sačuvaj.

### 2.6 Četiri podatka koja ćeš uneti u nedelju

Ne šalji mi ih. Samo zapamti gde su, pa ćeš ih u nedelju sam uneti u svoj terminal jednom komandom za svaki.

| Podatak | Gde je u Twilio konzoli |
|---|---|
| Account SID (počinje sa `AC`) | početna strana, odeljak **Account Info** |
| Auth Token | isto mesto, klikni **Show** |
| Verify Service SID (počinje sa `VA`) | **Verify**, **Services**, klik na `Kuc-kuc` |
| Kupljeni broj (oblik `+1...`) | **Phone Numbers**, **Manage**, **Active numbers** |

---

## Šta mi javljaš kad završiš

1. Google **Project ID** (nije tajna).
2. „Twilio je spreman" i da li je Srbija prošla u koraku 2.4.

Ako negde zapne, pošalji mi šta piše na ekranu, bez lozinki i ključeva.

---

## Kasnije, ne sada

- **Telegram bot, Google prijava i šifra za upis** za tvoju stranicu: uputstvo je u [uputstvo-porodica.md](uputstvo-porodica.md).
- **Do četvrtka 19.11:** zakazivanje lansiranja na Product Hunt-u.
- **Kredit od 50 dolara za hakaton:** piše da važi dok traje zaliha. Pogledaj stranicu hakatona na Product Hunt-u i prijavi se ako je obrazac još otvoren.
