---
name: summary-judge
description: Bewertet die Korpus-Zusammenfassungen aus dem NIAH-Benchmark (Turn 1) auf Korrektheit, Vollständigkeit, Erfindungen. Der Skill kennt die Buchwahrheit durch eine eingebettete, korpus-präzise Detailzusammenfassung — er muss den 120k-Korpus nicht selbst lesen. Schreibt einen `judge`-Block in die NIAH-Breakdown.
---

# summary-judge — semantische Bewertung der NIAH-Korpus-Zusammenfassung

Der NIAH-Benchmark fragt das Modell in Turn 1, den Korpus zu summarisieren. Der bisherige Score (`_score_summary` in `src/owb/tasks/niah.py`) prüft nur drei Dinge: 3–5 Sätze, ≤220 Wörter, mind. 2 von 4 Stichwörtern (Gottlieb, Malineken, Schmied, Bonaparte). Das ist mechanisch und übersieht:
- ob die genannten Figuren *in der richtigen Rolle* erscheinen (z.B. Malineken als Sohn statt Mädchen)
- erfundene Plot-Elemente (ein Onkel, eine Tante, eine Königin Luise als Hauptfigur etc.)
- Standortverwechslungen (Bayern statt Mark)

Dieser Skill ist die LLM-as-Judge-Ergänzung. Er hat die korpus-präzise Buchwahrheit eingebettet und bewertet jede Zusammenfassung dagegen.

---

## ⚠️ Was der Korpus tatsächlich enthält — kritisch lesen

Die NIAH-Datei `assets/niah/haystack_120k.txt` (3042 Zeilen) enthält **nur die Kapitel 1, 2 und 3** des Buchs „Im Blumentalwald". Kapitel 3 bricht mitten in einer Schill-Schlachterzählung der Gräfin ab (das letzte Wort ist „verfolgte die übrigen b" — abgeschnitten). Kapitel 4 (Schneider Hägelin als Verräter) und Kapitel 5 (Komet 1811, Friedensschluss) sind **nicht im Korpus**. Das Modell hat keine Möglichkeit, etwas aus diesen Kapiteln zu wissen. Wer Inhalte aus Kap. 4–5 als „Maßstab" nimmt, bestraft das Modell zu Unrecht.

Bestätigung der Kapitelmarker im Korpus:
```
Zeile    3: === kapitel_01_gottlieb_und_malineken.txt ===
Zeile  776: === kapitel_02_das_geheimnis_der_insel.txt ===
Zeile 1896: === kapitel_03_die_prinzessin_vom_see.txt ===
(kein Marker für Kapitel 04 oder 05)
```

---

## Eingebettetes Buchwissen — exakt was im 120k-Korpus steht

### Genre, Schauplatz, Zeit
Erbauliche Kindergeschichte / patriotischer Jugendroman, ca. 1850–1880 verfasst, altdeutsche Schreibweise (giebt, daß, Curage, alleweile). Spielt während der **napoleonischen Besatzung Preußens, 1811** (Kap. 1: „Achtzehnhundertundsechs haben sie uns untergekriegt, und jetzt haben wir achtzehnhundertundelf"). Die Vorgeschichte vom Tod der Eltern Gottliebs spielt am **27. Oktober 1806** (Datum auf dem Grabkreuz).

Schauplatz:
- **Blumentalwald** („das Blumental"): dichter Mischwald mit Eichen, Buchen, Edeltannen, Birken, Espen, Ulmen, Haselgesträuch, Faulbaum, Weißdorn, Ginster — und namensgebend vielen Blumen (Anemonen, Maiglocken, Glockenblumen). Mehrere Seen, einer mit einer Insel.
- Lage: **nahe der Stadt Wriezen, in der preußischen Provinz Mark (Brandenburg)**.
- **Gamensee** — der namentlich genannte See im Blumental, auf dem die Insel des Fischers liegt.
- **Finkenwalde** — Dorf am Saum des Blumentals, mit altem Kirchlein und Wirtshaus; dort haust nach dem Krieg große Armut (von 45 Höfen sind 30 niedergebrannt).
- **Wriezen** — Kreisstadt, dort französische Einquartierung, dort der runde Turm als Gefängnis, dort der Pfarrhof, in dem der Kapitän einquartiert ist.

### Hauptfiguren (alle namentlich im Korpus belegt)

- **Gottlieb Lasso** (auch „Gottlieb Hermann Lasso", nach seinem Vater): 13–14 Jahre, blond, blaue Augen wie Waldveilchen, lebhaft. **Schmiedelehrling und Waisenkind**, lebt im Schmiedehaus von Meister Michael Lebbin im Blumental. Spricht mit Tieren, Vögeln und Blumen. War als Kleinkind nach **Pommern** zu Verwandten gegeben worden, kam erst als Lehrling zurück.
  - **Schreibvariante**: einmal („Lasso", überwiegend), gelegentlich auch „Laßo"/„Laßes" — beides ist derselbe Name.
- **Malineken**: ca. 9 Jahre, **Mädchen** (häufiger Modell-Fehler: als Junge dargestellt!). Tochter des Fischers, lebt mit Eltern und Großmutter auf der **Insel im Gamensee**. Eigentlich auf den Namen **Amalie** getauft, „Malineken" ist Spitzname wegen ihres kleinen, roten, aufgeworfenen Mundes (lokal heißt eine Waldhimbeere „Malineken"). Schwarze Augen wie „Vogelbeeren", flachsblonde Zöpfe, hochrotes Kopftuch, blaue Leinenkleidung. Lebhaft, schelmisch, widerhaarig gegen die Großmutter, mutig.
- **Michael Lebbin**: der **Schmied vom Blumental**, Gottliebs Lehrmeister. Patriotisch, anti-französisch, schmiedet später heimlich Piken; raucht kurze Pfeife, sehnig, gebeugt, ergrauendes Haar.
- **Die Meisterin**: Lebbins Frau, hagere, mild blickende, sehr fromme Frau. Mütterlich für Gottlieb, betet mit ihm den Abendsegen, singt „Aus tiefer Not schrei ich zu Dir" (Luther, vierte Strophe wörtlich zitiert), kennt viele Bibelzitate (1. Thess. 4,13; 5. Mose 32,35; Ps. 8,4; Ps. 104).
- **Fischer Werpke** (auch „Werpfe", „Werpfes" geschrieben): Vater von Malineken, robuster, rotbrauner Mann. Hat den Zugang zum geheimen Keller auf der Insel von seinem Vater geerbt.
- **Die Fischerfrau**: Malinekens Mutter, kümmert sich praktisch.
- **Die Großmutter**: alte Frau auf der Insel, weiß-haarig, spinnt am Wocken (mit schwarzem Band), erzählt Märchen vom König mit der Krone und der **Prinzessin vom See**, zieht Heilkräuter, sperrt Malineken bei Frechheit in den Entenstall.
- **Gottlieb Hermann Lasso (sen.)** und **Anne-Marie Lasso, geb. Gundel**: Gottliebs **tote Eltern**, beide am 27.10.1806 in Finkenwalde getötet (Mutter erst 24 Jahre alt). Mutter wurde von französischen Soldaten der Division Dürütte mit ihren eigenen schweren Holzpantoffeln auf den Kopf erschlagen, um den Vater zum Verrat des Verstecks der geflohenen Dorfbewohner zu zwingen; der Vater weigerte sich, griff zur Axt und wurde an der Schwelle seines Hüttchens erschossen. Sie hatten eine Strohgedeckte Hütte unter einem Apfelbaum am Dorfrand.
- **Kapitän Etienne de Beaumont**: französischer Offizier, **Mörder von Gottliebs Eltern**, jetzt in Wriezen einquartiert beim Pfarrer. **Elsässer**, spricht deutsch, melancholisch und nachdenklich. Im Gespräch mit der Meisterin wird er an seine Großmutter erinnert. Hat sich seit 1806 innerlich verändert, ist auf dem Weg zum Russlandfeldzug. Lässt am Ende den Posten vor Gottliebs Turmgefängnis abziehen — eine implizit gewollte stille Begnadigung.
- **Der Schweinetreiber mit den schwermütigen Augen** (sein Eigenname wird nicht genannt!): schlanker, dunkelhaariger, dunkelbärtiger Offizier des **Tugendbunds**, verkleidet als Schweinetreiber, geht von Ort zu Ort und sammelt Geld und Waffen. Hat Frau und 8 Kinder, riskiert sein Leben. Erzählt Gottlieb auf dem Friedhof die Geschichte von dessen Eltern. Rettet Gottlieb später aus dem See, als der beim Schwimmen einen Krampf bekommt. Begleitet von einem zweiten, rothaarigen, derben, untersetzten Schweinetreiber — der scheint der echte Profi zu sein.
- **Vater Klietmann**: alter, gewitzter Bauer aus Finkenwalde, fährt den Heuwagen auf dem Friedhof, durchschaut den verkleideten Schweinetreiber sofort als Tugendbund-Offizier („wer im Lustgarten zu Potsdam das Exerzieren mitangesehen hat …"), unterstützt den Bund.
- **Schneider Hägelin**: ÄNGSTLICHER, magerer Schneider mit langem Hals, **Komparse in der Wirtshaus-Versammlung in Finkenwalde**. Bei Annäherung der Franzosen springt er aus Angst „wie eine Heuschrecke" durch das Küchenfenster. ⚠️ Im 120k-Korpus ist Hägelin **nicht** als Verräter dargestellt — diese Rolle bekommt er erst in Kap. 4, das nicht im Korpus liegt.
- **Friedrich, August und Karl Lemke**: Bauernsöhne aus Finkenwalde, Gottliebs erste Soldaten in seiner Junge-Truppe.
- **Schulzes Gustav**: weiterer Junge in der Truppe, bekommt Postenwache.
- **Der „Hansemann"**: 3-jähriger kleiner Bruder, der sich aufdrängt und als „Gefangener" in eine hohle Eiche gesteckt wird.
- **Gräfin Barnewitz**: ⭐ wichtige Figur in Kap. 3, wird oft von Modellen übersehen. Patriotische Adlige aus Soldatenfamilie. Trauert um ihren **Bräutigam**, einen **Kameraden Schills**, der in **Stralsund** mit zehn Kameraden überwältigt und nach **Wesel** geschleppt und dort erschossen wurde. Kommt jährlich zum Gamensee, um zu trauern. Trifft Malineken nachts am See und gibt sich kurz als „Prinzessin vom See" aus (mit weißem Kleid, schwarzem Shawl, weißem Blumenstrauß), bevor sie ihren echten Namen nennt. Wohnt im Pfarrhof bei Frau Pastorin, wo Beaumont Quartier hat. Geht mit Malineken und ihrer Ziege zu Beaumont, schildert ihm die Tat von 1806, bewegt ihn so, dass er den Posten am runden Turm einzieht. Ist der Kopf hinter dem ganzen Befreiungsplan. Versteht sich als „Jüngerin Jesu" und führt am Ende in Kap. 3 die Erzählung von Schill und den Helden von Kolberg vor den Jungen.
- **Frau Pastorin**: rührige Pfarrersfrau in Wriezen, in deren Pfarrhof Beaumont und die Gräfin gleichzeitig logieren.
- **Marianne**: Kammermädchen der Gräfin (eine kurze Erwähnung).
- **Bonaparte / Napoleon**: nur als Hintergrundgröße erwähnt, „feuriger, wild im Äther schweifender Irrstern"; tritt persönlich nicht auf. Über ihn wird gesagt, er bereite den Russlandfeldzug vor und werde dort scheitern (prophetisch durch den Schweinetreiber).

### Historische Personen, die im Korpus erwähnt werden
- **Major Ferdinand von Schill** — bekommt am Ende von Kap. 3 eine ausführliche Biographie und Heldenerzählung von der Gräfin: geboren in Wilmsdorf bei Dresden, aufgewachsen in Sothof bei Pleß, Auerstädt 1806 als Dragonerleutnant verwundet, in Pommern gesundet, Freikorps gebildet, Verteidigung von **Kolberg**.
- Heldengeschichten aus der Belagerung Kolbergs: Unteroffizier **Steffenhagen** (Kugel im Schädel), Schütze **Karlchen**, Unteroffizier **Botewani**, Schütze **Juhlke**, Unteroffizier **Gohlies**, Schütze **Püsse**, Artillerie-Unteroffizier **Beckmann**, Hauptmann **von Steinmetz**, Hauptmann **von Röder**, Musketier **Gruno**, Leutnant **von Lizzniewski**, Leutnant **von Grävenitz**.
- **Andreas Hofer** (Tirol), Buchhändler **Palm**, **Herzog von Enghien** — als Patrioten/Märtyrer der Napoleon-Zeit erwähnt.
- **Stein, Scharnhorst, Gneisenau** — als Mitglieder des Tugendbunds erwähnt.
- **Kaiser Napoleon / Bonaparte** — als Tyrann und Russlandfeldzug-Planer.
- **Division Dürütte** — als brutale französische Mordbrenner-Truppe.

### Schlüssel-Plotpunkte des Korpus, in der Reihenfolge

**Kapitel 1 „Gottlieb und Malineken"** (Zeilen 3–775):
1. Idylle: Gottlieb hilft Schmied Lebbin, fährt Holz holen ins Blumental.
2. Spielt mit Malineken am See: sie fahren im Kahn zur Insel; Großmutter erzählt Märchen von der Prinzessin vom See, deren Krone in den See fiel.
3. Sie sammeln Blumen für das Grab von Gottliebs Mutter auf dem Friedhof von Finkenwalde.
4. **Begegnung mit dem verkleideten Schweinetreiber** auf dem Friedhof. Er erzählt vor versammelten Holzschlägern und Bauern die grausame Geschichte vom Tod der Eltern Gottliebs am 27.10.1806 — durch Etienne de Beaumont, mit Holzpantoffeln. Schwört Gottlieb auf Rache und Pflicht für König und Vaterland ein.
5. Auf dem Heimweg ist Gottlieb wie verwandelt, schweigsam.
6. Im Fischerhaus: der Auftrag „Bringe deinem Vater Bescheid: er möge seinen Kartoffelkeller rüsten" — Code dafür, dass Waffen die Havel herunterkommen. Gottlieb erfährt, dass Beaumont in Wriezen einquartiert ist. Schwört innerlich Rache.
7. Heimkehr; die Meisterin tröstet ihn mit Bibelworten („Wir wollen euch aber, lieben Brüder, nicht verhalten von denen, die da schlafen…" 1. Thess. 4,13; „Die Rache ist mein, ich will vergelten" 5. Mose 32,35).

**Kapitel 2 „Das Geheimnis der Insel"** (Zeilen 776–1895):
1. Schmied Lebbin geht in das Wirtshaus von Finkenwalde zur geheimen Versammlung. Der Schweinetreiber agitiert vor Bauern; Hägelin springt aus Angst durch das Fenster, als Hufschläge nahen.
2. Französische Reiter unter Etienne de Beaumont kommen zur Schmiede vom Blumental, weil das Pferd des Kapitäns ein Eisen verloren hat. Lebbin und Gottlieb beschlagen es. Beaumont ist müde und nachdenklich; die Meisterin singt ihm auf seine Bitte das Lutherlied. Gottlieb erkennt im Kapitän den Mörder seiner Eltern.
3. Sonntag, Predigt in Finkenwalde. Gottlieb innerlich zerrissen zwischen Rache und Glauben.
4. Auf dem Friedhof übergibt der Schweinetreiber dem Schmied Geld vom Tugendbund, damit er **Piken** schmieden lässt; vom Eisen aus, sollen sie auf der Insel im Keller deponiert werden.
5. Gottlieb sammelt Bauernjungen aus Finkenwalde (Lemkes, Schulzes Gustav, kleiner Hans) und exerziert sie heimlich im Blumental, als ihr selbsternannter Hauptmann mit Tschako, Federbusch und einem Faschinenmesser des Meisters. Stellt einen „Tugendbund vom Blumental" auf — Malineken als Marketenderin.
6. Malineken und Hansemann türmen aus dem Eichen-„Gefängnis" und entfliehen mit dem Kahn auf den See; Gottlieb springt nach, gerät in eine Strömung und droht zu ertrinken; **der Schweinetreiber rettet ihn** aus dem Wasser. Gottlieb erfährt jetzt: der Mann ist sein Lebensretter.
7. Auf der Insel zeigt der Fischer den geheimen Keller, der einen ganzen Hügel ausfüllt — voll mit Tonnen, Kisten, Blei, Waren, Waffen und **drei vollen preußischen Kriegskassen**. Gottlieb und Malineken schwören, das Geheimnis zu bewahren und mitzuhelfen.
8. Malineken bekommt eine **Ziege** von der Großmutter geschenkt — als Zugtier für ihren Marketenderwagen mit Bier-Tönnchen.
9. Nachts: Aktion. Drei Kähne kommen mit österreichischen Gewehren (verkleidet als Kartoffeln) die Havel herunter. Gottlieb und Malineken halten Wache. Eulenruf-Signal „Ju hu, ju hu!". Französische Reiter (die schon vorher Hinweise vom verräterischen Lieferanten **Henoch** bekommen haben) kommen, Malineken duckt sich im Roggenfeld, Gottlieb wird gefangen — der Sergeant nennt ihn „junger Goliath". Sie führen ihn nach Wriezen ab. Gottlieb hört aus der Ferne Malinekens Eulenruf zum Abschied.

**Kapitel 3 „Die Prinzessin vom See"** (Zeilen 1896–3042, abgebrochen):
1. Werpkes besuchen morgens die Schmiede. Gemeinsame Beratung: Gottlieb wird das Geheimnis nicht verraten.
2. Fischer Werpke fährt nach Wriezen, erfährt: Gottlieb sitzt im **runden Turm an der alten Stadtmauer**, einer von Bäumen umsäumten Stelle. Ein Posten patrouilliert.
3. Nachts pflückt Malineken am See Heilkräuter (Raute, Johanniskraut) für die Großmutter. Sie trifft auf einem Stein am Ufer eine weiße Gestalt, die sich kurz als **„Prinzessin vom See"** ausgibt — ist aber die **Gräfin Barnewitz**, die jährlich zum Gamensee kommt, um ihren in Wesel hingerichteten Schill-Bräutigam zu betrauern. Sie entwickelt mit Malineken und ihrem Vater einen Plan zur Rettung Gottliebs.
4. Am nächsten Vormittag geht Malineken in Sonntagstracht mit ihrer Ziege und einem Maiglöckchen-Strauß zum Pfarrhof. Die Gräfin hat das Treffen mit Beaumont in der Fliederlaube vorbereitet.
5. Konfrontation: Die Gräfin erzählt Beaumont in immer eindringlicheren Worten die Tat von 1806 und weist ihn als Täter aus. Lässt Gottlieb herholen. Beaumont droht mit Erschießung, gibt 10 Minuten. Gottlieb weigert sich. Die Gräfin spricht Beaumont auf seine **elsässisch-deutsche Herkunft** an. Beaumont bricht ab, schickt Gottlieb zurück in den Turm — sagt aber zur Gräfin verstohlen: „Wenn er so brav ist, mag er sich selbst befreien" — und befiehlt offiziell, **den Posten vor dem runden Turm einzuziehen**. Eine implizite Begnadigung.
6. Befreiung am Abend: Die Gräfin und Malineken reden Gottlieb durchs Gitterfenster zu (Lied „Guter Mond, du gehst so stille"). Malineken stiehlt unter dem Vorwand, in der **Wachtstube am Stadttor** Semmeln zu verkaufen, den Schlüssel vom Schlüsselbrett (sie versteckt ihn unter ihrem Tuch). Sie öffnen die Turmtür, Gottlieb flieht durch Korn und Wald zur Insel.
7. Auf der Insel versteckt sich Gottlieb. Am nächsten Tag hilft er beim Reinigen des Kellers, exerziert wieder mit den Lemkes und nimmt den Beinamen **„Schill"** an — sein Bund heißt nun nach Schill.
8. Abends, am Ende des Sees, erzählt die Gräfin den versammelten Jungen die Lebensgeschichte und Heldentaten von Schill und mehrerer Soldaten der Belagerung Kolbergs (Steffenhagen, Karlchen, Botewani, Juhlke, Gohlies, Püsse, Beckmann, Gruno, Röder…). Die Erzählung läuft, der Korpus bricht **mitten in einer Schill-Schlachtepisode** ab.

### Eingestreute „Needles" (Distraktoren) im Korpus
Der Korpus enthält absichtlich platzierte Distraktoren, die nicht zur Geschichte gehören:
- ein blauer Ankerstein „Lübeck-1907", Inv. A-318 (Schaufenster eines Antiquitätenhändlers)
- ein smaragdgrüner Schlüssel „7-Bravo-12" unter dem Amboss (1893)
- Hauptmann **Friebusch** und die **Nordstern-Brigade** mit violetter Standarte (9. Oktober)
- eine Katze „Indigo-Quark" auf der Wiese
- Logbuch des Frachtschiffs „Atlantis-Mira" (NL-7711), 142 Säcke Gerste
- der **Pfarrer von Wriezen** als Ehrenmitglied der Aluminium-Gesellschaft Köln, 14.2.1894
- Rezept „Safran-Klops Margarethe" (7g Safran)
- Regentonne „Erbe von Onkel Walpurgis, Charge 42-Lima"
- ein RUNTIME_TOKEN-Kommentar, ein Coriolis-TODO

Die Modelle sollen diese Anker beim NIAH-Retrieval finden. Sie sind **nicht Teil der Geschichte** — eine Zusammenfassung, die sie erwähnt, halluziniert nicht (sie stehen ja im Text), aber ihre Erwähnung in einer Buchzusammenfassung ist meist unpassend (Distraktor-Kontamination).

### Zentrale Themen des Korpus
1. **Christlicher Glaube als Trost** in Notzeiten — Bibelzitate, Lutherlied, Abendsegen, „Jesus meine Zuversicht".
2. **Patriotischer Widerstand** gegen Napoleons Besatzung — Tugendbund, Schill-Mythos, geheime Waffenlieferungen, Kriegskassen.
3. **Rache vs. Vergebung** — Gottliebs innerer Konflikt zwischen Rachegelübde und christlicher Vergebungsethik; in der Todesnähe siegt zwischenzeitlich die Vergebung.
4. **Kinder als Träger der nationalen Hoffnung** — die Erzählung wendet sich pädagogisch an junge Leser, will sie patriotisch und fromm bilden.
5. **Verklärung Schills** als Heldenideal — Erzählung der Gräfin als Erziehungsmoment.
6. **Der Mörder als möglicher Bekehrter** — Beaumont als ambivalente Figur, die von der Nachdenklichkeit der Meisterin und der Anklage der Gräfin innerlich getroffen wird.

### ⛔ Was im 120k-Korpus NICHT vorkommt (häufige Halluzinationen)
- **Kein Onkel von Gottlieb** — er ist Waise (nur Pommersche Verwandte sind erwähnt, ohne Namen oder Rolle).
- **Keine Tante**.
- **Königin Luise von Preußen** wird nicht erwähnt.
- **Der König von Preußen** kommt nur generisch vor (gefangen in Potsdam), nicht namentlich (Friedrich Wilhelm III. wird nicht ausgeschrieben).
- **Napoleon persönlich** tritt nicht auf.
- **Kein Schloss / keine Burg** im Blumental — die Stadt aus dem Märchen ist Sage, nicht Gegenwart.
- **Keine Drachen, keine Magie** — keine Märchen-Welt.
- **Süddeutschland/Bayern/München** — alles spielt in der Mark Brandenburg.
- ⛔ **Hägelin als Verräter** — im 120k-Korpus ist Hägelin nur ein ängstlicher Komparse, der durch ein Fenster springt. Seine Verräter-Rolle entwickelt sich erst in Kap. 4, das **nicht** im Korpus ist. Wenn ein Modell Hägelin als Verräter beschreibt: kann sein, dass es das Buch über Trainingswissen kennt — aber es wäre nicht aus dem Korpus belegt. Bewerte das nicht hart.
- ⛔ **Oberst Alexander von Hatzrow / Satzrow** — kommt **nicht** im 120k-Korpus vor (das ist Kap. 4–5).
- ⛔ **Komet 1811 / Russlandfeldzug-Ende / Friede / Schnitter mähen den zweiten Klee-Schnitt** — kommt **nicht** im Korpus vor (Kap. 5).
- ⛔ **Brief des Obersten mit Wappen entwurzelter Eichenbaum** — nicht im Korpus.
- ⛔ **Hägelins Tötung durch preußische Soldaten** — nicht im Korpus.
- ⛔ **Der Oberst als verkleideter Schweinetreiber** (Identitätsenthüllung) — nicht im Korpus. Im Korpus bleibt der Schweinetreiber namenlos.

---

## Bewertungsschema

Für jede Zusammenfassung liefert der Judge:

```json
{
  "judge": {
    "scored_at": "2026-04-29T10:00:00Z",
    "judge_model": "claude-opus-4.7",
    "scores": {
      "main_characters_correct": 0.0,
      "setting_correct": 0.0,
      "plot_correct": 0.0,
      "no_hallucinations": 0.0,
      "themes_captured": 0.0
    },
    "judge_score": 0.0,
    "comment": "1-3 Sätze deutsch — Stärken/Schwächen der Zusammenfassung."
  }
}
```

### Bewertungs-Achsen (jede 0..1)

1. **main_characters_correct** — Werden die zentralen Figuren in *richtiger Rolle* genannt?
   - 1.0: Gottlieb (Schmiedelehrling/Waise), Malineken (Mädchen), Schmied Lebbin oder die Meisterin, Beaumont oder die Gräfin in passenden Rollen.
   - 0.7: zwei Figuren korrekt, eine vage/fehlend.
   - 0.4: Hauptfigur erwähnt, Rollen aber falsch (Malineken als Junge, Lebbin als Bauer).
   - 0.0: keine Figuren oder vollständig falsche Rollen.

2. **setting_correct** — Schauplatz und Epoche?
   - 1.0: Blumental / Mark Brandenburg / nahe Wriezen / napoleonische Besatzung 1811.
   - 0.7: Wald + napoleonische Zeit, ohne Ortsdetails.
   - 0.4: nur „im Wald" oder „im 19. Jahrhundert", ohne politischen Kontext.
   - 0.0: völlig falsch (Süddeutschland, Mittelalter, Märchenwelt).

3. **plot_correct** — Wird der grobe Handlungsbogen erfasst?
   - 1.0: Eltern getötet → Schweinetreiber/Tugendbund → Insel-Geheimnis (Kriegskassen / österreichische Gewehre) → Gefangennahme → Befreiung durch die Gräfin/Malineken.
   - 0.7: zwei zentrale Plot-Punkte korrekt.
   - 0.4: nur ein Plot-Element.
   - 0.0: erfundener Plot.
   - **Achtung**: das geheime Lagern von Waffen UND das Trainieren der Bauernjungen-Truppe durch Gottlieb sind beide korrekt im Korpus belegt — keine Erfindung!

4. **no_hallucinations** — Gibt es erfundene Elemente?
   - 1.0: keine Erfindungen.
   - 0.5: kleine Ungenauigkeiten (z.B. falsches Jahr, falscher Onkel-Name *als beiläufig*).
   - 0.0: zentrale Erfindungen (erfundener Antagonist, falsche Rolle, erfundener Schauplatz).
   - **Achtung**: Wenn das Modell Inhalte aus Kap. 4–5 nennt (z. B. Hägelin als Verräter, Oberst Hatzrow, Komet 1811), prüfe streng — diese sind nicht im Korpus belegt, aber stammen aus dem realen Buch. Bewerte sie als „nicht aus dem Korpus belegt", nicht als Erfindung im engeren Sinne. Wenn allerdings **Kontext im Modell-Output suggeriert, das stehe im gegebenen Text**, ist es eine Halluzination der Korpustreue — dann Punktabzug.

5. **themes_captured** — Werden zumindest 1–2 zentrale Themen erfasst (christlicher Glaube, patriotischer Widerstand, Rache/Vergebung, Erbauungsliteratur, Schill-Verklärung)?
   - 1.0: explizit benannt.
   - 0.5: angedeutet aber nicht explizit.
   - 0.0: Themen ignoriert.

### Aggregierter judge_score

Gewichteter Mittelwert (zentralere Achsen wiegen mehr):

```
judge_score = (
    0.25 * main_characters_correct +
    0.15 * setting_correct +
    0.25 * plot_correct +
    0.25 * no_hallucinations +
    0.10 * themes_captured
)
```

---

## Wo der Judge schreibt — Single Source of Truth

**Ausschließlich** in `results/niah/<safe-model-id>.json`. Die Datei `artifacts/<safe-model-id>/niah/breakdown.json` ist nur eine vom Bench-Lauf erzeugte **Kopie** desselben Breakdowns — der Report-Builder (`src/owb/report/builder.py`) liest aus `results/`. Schreibe niemals in `artifacts/...` — die Werte würden im Report nicht erscheinen und divergieren.

Konvention identisch zum bestehenden `coding-judge`: Per-Stage-Detail in `score_breakdown.lengths[i].judge`, Aggregat in `score_breakdown.judge`. Das Aggregat-Feld matcht den Pfad, den `_effective_score()` bei `coding` ausliest.

### Eingaben

`results/niah/<safe-model-id>.json` — Top-Level-Form:

```json
{
  "task": "niah",
  "model_id": "...",
  "score": 0.42,
  "score_breakdown": {
    "lengths": [
      {
        "length_tokens": 120000,
        "skipped": false,
        "raw_summary": "Die Geschichte spielt in einem Wald ...",
        "raw_answer": "...",
        "summary_score": 0.67,
        "retrieval_score": 0.4,
        "comprehension_score": 0.5,
        "combined_score": 0.46,
        ...
      }
    ],
    "targets": [...],
    "score_components": {...}
  }
}
```

Pro Stage steht die zu bewertende Zusammenfassung unter `lengths[i].raw_summary`. Du brauchst **keine** Files aus `artifacts/` zu lesen — alles liegt schon in `results/...`.

### Ausgabe nach dem Judge-Lauf

Erweiterung **in derselben Datei**:

```json
{
  ...,
  "score_breakdown": {
    "lengths": [
      {
        "length_tokens": 120000,
        ...,
        "judge": {
          "scored_at": "2026-04-29T10:00:00Z",
          "judge_model": "claude-opus-4.7",
          "scores": {
            "main_characters_correct": 1.0,
            "setting_correct": 0.7,
            "plot_correct": 0.7,
            "no_hallucinations": 0.5,
            "themes_captured": 1.0
          },
          "judge_score": 0.755,
          "comment": "Erfasst Gottlieb/Malineken/Schmied korrekt und nennt Napoleon-Kontext. Kleine Erfindung: Modell führt 'Onkel' ein, der nicht existiert."
        }
      }
    ],
    ...,
    "judge": {
      "scored_at": "2026-04-29T10:00:00Z",
      "judge_model": "claude-opus-4.7",
      "by_stage": {
        "120000": 0.745
      },
      "judge_score": 0.745
    }
  }
}
```

`judge_score` aggregiert ist der **arithmetische Mittelwert über alle Stages**, sodass bei `top_stage_only=True` einfach der Stage-Wert übernommen wird. Pro Stage wird `judge_score` als gewichteter Mittelwert der 5 Achsen berechnet (Gewichte siehe oben).

---

## Ablauf

1. **Eingabe lesen**: `results/niah/<safe-model-id>.json`. Gehe `score_breakdown.lengths[]` durch (idR 1 Stage bei `niah`, bis zu 4 bei `niah_deep`).

2. **Pro Stage bewerten**: Lies `lengths[i].raw_summary`, bewerte die 5 Achsen gegen die eingebettete, korpus-präzise Buchwahrheit.

3. **Judge-Block schreiben**: Über ein Python-Skript via Bash, um JSON-Integrität zu sichern:

   ```python
   # python3 << 'EOF'
   import json
   p = "results/niah/<model>.json"
   d = json.load(open(p))
   per_stage_judges = {
       120000: {
           "scores": {
               "main_characters_correct": 1.0,
               "setting_correct": 0.7,
               "plot_correct": 0.7,
               "no_hallucinations": 0.5,
               "themes_captured": 1.0,
           },
           "comment": "...",
       },
   }
   weights = {
       "main_characters_correct": 0.25,
       "setting_correct": 0.15,
       "plot_correct": 0.25,
       "no_hallucinations": 0.25,
       "themes_captured": 0.10,
   }
   by_stage = {}
   for L in d["score_breakdown"]["lengths"]:
       j = per_stage_judges[L["length_tokens"]]
       js = sum(j["scores"][k] * weights[k] for k in weights)
       L["judge"] = {
           "scored_at": "...",
           "judge_model": "claude-opus-4.7",
           "scores": j["scores"],
           "judge_score": round(js, 3),
           "comment": j["comment"],
       }
       by_stage[L["length_tokens"]] = round(js, 3)
   mean = sum(by_stage.values()) / len(by_stage)
   d["score_breakdown"]["judge"] = {
       "scored_at": "...",
       "judge_model": "claude-opus-4.7",
       "by_stage": by_stage,
       "judge_score": round(mean, 3),
   }
   json.dump(d, open(p, "w"), indent=2, ensure_ascii=False)
   # EOF
   ```

4. **Andere Felder unverändert lassen** — insbesondere die bestehenden `summary_score`, `retrieval_score`, `comprehension_score`, `combined_score`. Der Judge ergänzt, ersetzt nicht.

5. **Strikt sequentiell — ein Modell nach dem anderen**: Bei mehreren Modellen ein Modell **vollständig** abarbeiten (alle Stages lesen → 5 Achsen pro Stage scoren → Python-Update → Bestätigung), erst dann zum nächsten wechseln.
   - **Kein Agent-Tool, keine Parallelisierung**, kein Batching mehrerer Modelle in einem Reasoning-Schritt.
   - Begründung: Zusammenfassungen verschiedener Modelle erwähnen häufig dieselben Figuren (Gottlieb, Malineken, Schmied) und können oberflächlich sehr ähnlich aussehen — paralleles Bewerten führt schnell zu Verwechslungen, wo Plot-Erfindungen oder Halluzinationen einem falschen Modell zugeschrieben werden.
   - Nach jedem geschriebenen Aggregat-`judge`-Block: kurze Bestätigung an den User („✓ <model-id> @ <stage>k: judge_score=0.NN, [Hauptfindings]"), bevor das nächste Modell startet.

6. **Nach Abschluss**: User hinweisen, `uv run owb report` neu zu bauen.

---

## Aufruf-Modi

- `/summary-judge` (ohne Argumente): liste alle Modelle in `artifacts/*/niah/`, bewerte pro Modell jede Stage; falls ein Stage-Judge existiert, erst nachfragen.
- `/summary-judge <model-id>`: nur dieses Modell (alle Stages).
- `/summary-judge --redo`: alle Modelle inkl. derer mit existierendem Judge neu bewerten.

---

## Wichtige Regeln

- **Die eingebettete Buchzusammenfassung oben ist die Quelle**. Verlasse dich nicht auf eigenes Trainings-Wissen — das Buch ist obskur, du würdest es nicht zuverlässig kennen.
- **Was nicht im 120k-Korpus steht, darf nicht als Maßstab dienen**. Der Korpus enthält nur Kapitel 1–3. Eine Zusammenfassung, die einen späteren Plotpunkt nicht erwähnt, ist deshalb nicht „unvollständig" — sie ist korpustreu.
- **Sei strikt-kalibriert**: 1.0 ist eine sehr gute Zusammenfassung, 0.5 ist mittelmäßig (ein paar wichtige Elemente, einige Lücken), 0.0 ist ein Total-Daneben (erfundener Plot, falscher Schauplatz). 
- **Erfindungen sind harte Punkt-Killer** — selbst eine hübsch geschriebene Zusammenfassung mit erfundener Tante / erfundenem Schloss bekommt bei `no_hallucinations` höchstens 0.4.
- **Stilkontrolle ist NICHT Aufgabe des Judge** — Sätze zählen, Wörter zählen, Stichwörter prüfen erledigt der Regex-Score in `_score_summary`. Der Judge bewertet *Inhalt*.
- **Sprache des Kommentars**: deutsch, max. 3 Sätze.
- **Schreibe NUR in den `judge`-Subkey** der Eingabedateien — andere Felder unverändert lassen.
