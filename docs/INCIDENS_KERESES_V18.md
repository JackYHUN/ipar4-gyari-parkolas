# V18 – incidensek keresése a teljes mentett futásban

Az Üzemfelügyelet incidenslistája járműazonosító, hibafajta, súlyosság, életciklusállapot és szabad szöveg szerint kereshető. A keresés a szerveren, a mentett adatbázisban történik; nem csak a böngészőben látható 50 sorra vonatkozik. A korábbi találatok a meglévő lapozógombokkal bejárhatók. Az incidens nyitási bizonyítékának megnyitása továbbra is az eredeti fogadott telemetria-, esemény- és forgalmi határhoz kötött.

## Mérnöki indok és rendszerhatár

Egy hosszabb futás több visszatérő érzékelő- és kommunikációhibát rögzíthet. A kezelő számára az adott autó vagy felülvizsgálati megjegyzés gyors visszakeresése önálló felügyeleti értéket jelent. Az OPC Foundation Alarms & Conditions leírása külön auditálja a kezelői riasztásműveleteket [1]; a kereshető, megőrzött incidensnapló e vizsgálhatóságot támogatja a prototípus saját MQTT-rendszerében. Ez saját felügyeleti funkció, nem szabványos OPC UA végpont.

| Követelmény | Megvalósítás | Bizonyíték |
| --- | --- | --- |
| Régebbi találat is elérhető legyen. | Paraméterezett szerveroldali szűrés a teljes `id <= until_id` epizódkörben, csökkenő azonosító szerinti lapozás. | A 100 sort meghaladó mentett epizódok tesztje és a valódi MQTT-kampány régebbi találatának külön lapja. |
| A találatszám ne csak az aktuális oldalt jelentse. | `matching_count` a teljes kijelölt epizódhatárra; a `before` lapozókurzor nem csökkenti. | Független elvárt összesítés, egysoros oldalak és szűkített felső határ. |
| Ékezet és különleges szöveg legyen helyesen kezelve. | Unicode `casefold`, szó szerinti mezőn belüli részszöveg; `%` és `_` nem helyettesítő karakter. | Magyar vegyes betűméret, `Straße` / `STRASSE`, szó szerinti `%_` és SQL-szerű keresőszöveg. |
| Gépelés ne cserélje ki váratlanul a vizsgált oldalt. | Külön alkalmazott lekérdezés és szöveges piszkozat; csak Enter vagy az alkalmazás gomb rögzíti a beírt szöveget. | Késleltetett válaszok, lapozás, piszkozat és lekérdezésazonosság felületi regressziói. |
| A teljes futás figyelmeztetése ne tűnjön el egy szűk kereséstől. | A teljesfutás-állapot és darabszám változatlan; a riport és export minden állapotot tartalmaz a saját rögzített határig. | Üres találat melletti változatlan teljesfutás-összesítő; változatlan riport és export. |

## Szűrési szabályok

Az állapotválasztó az összes, aktív, megszűnt, nyugtázásra váró és nyugtázott epizódot támogatja. A megszűnt és a nyugtázott állapot nem ugyanaz: az első megfigyelt helyreállást, a második kezelői felülvizsgálatot jelent. A nyugtázás nem oldja fel a hibát.

A hibafajta az összes vagy a négy ismert szabály egyike: kommunikációhiány, feldolgozási lemaradás, érzékelőhiba, komponensleállás. A súlyosság az összes, figyelmeztetés vagy hiba. Az autóazonosító pontos és betűméretérzékeny: például `CAR001` nem azonos a `car001` értékkel. Üres azonosítónál rendszerszintű incidensek is szerepelhetnek. Ezek a szűrők egymással ÉS kapcsolatban állnak.

A legfeljebb 128 karakteres keresőszöveg elejéről és végéről a szóközök törlődnek. A keresés a cím, vizsgálati javaslat, érintett, autóazonosító, szabálykulcs és kezelői megjegyzés mezőkben keresi ugyanazt a teljes részszöveget. Bármelyik mező találata elegendő. A keresés nem ékezetmentesít és nem javít elgépelést; az „érzékelő” és az „erzekelo” különböző szöveg. Az SQL-szerű keresőszöveg egyszerű adat, nem végrehajtott kód. A keresés egy determinisztikus SQLite-függvénnyel működik a csak olvasható archívumkapcsolaton is; régi adatbázisba nem ír új táblát vagy indexet.

A választók a beállított választószűrőket alkalmazzák. A szöveges autó- és keresőmező Enter vagy **Alkalmazás** gomb után lép érvénybe; a gépelés és a fókuszváltás önmagában nem indít keresést. **Alaphelyzet** minden szűrőt töröl. A kijelzett találatszám és a lapozás mindig az alkalmazott lekérdezésre vonatkozik, nem az esetleg még szerkesztett piszkozatra. Új alkalmazás visszatér az első oldalra; a régebbi/újabb oldal megtartja a szűrőket és az epizódhatárt.

## API, pillanatkép és korlátok

Példa: `GET /api/operations?run_id=<futás>&status=resolved&rule=sensor_unavailable&severity=error&car_id=CAR001&search=&limit=50`. A V18 felület az új szűrőparamétereket üres vagy `all` érték esetén is kifejezetten elküldi. A válasz `filters` mezője a normalizált kérelem másolata; a `search` mező a levágott eredeti szöveget őrzi, nem a belső, betűmérettől független összehasonlítás alakját.

A `matching_count` a lekéréskor mentett életciklusállapoton számol. Egy élő epizód később lezárható vagy nyugtázható, ezért ugyanazon felső határ mellett is változhat az állapotszűrés találatszáma. Az epizódhatár az újabb epizódok beemelését korlátozza; nem rekonstruál egy régebbi életciklus-pillanatképet. A teljesfutás-összesítő megmarad, a rögzített V17 riport továbbra is minden epizódra vonatkozik a saját felső határáig. Az export szintén teljes epizódkör, nem szűrt találatletöltés.

A hiányos, incidensarchívum nélküli régi futás `matching_count=null` értéket ad; a felületen ez ismeretlen, nem nulla találat. Hibás státusz-, szabály-, súlyosság- vagy azonosítóérték, illetve túl hosszú keresőszöveg 400 választ ad. A régi, új szűrők nélküli kérelmek és az eredeti futás-ZIP incidenskatalógusának formátuma megmaradt.

Az oldal legfeljebb 100 epizódot kérhet, a felület 50-et használ; a szerver legfeljebb eggyel több sort tölt be a következő oldal megállapításához. A találatszám és a szöveges keresés a teljes kijelölt epizódkörön végez munkát. A memóriahasználat korlátozott, de ez nem állít állandó futási időt vagy mért maximális kapacitást nagy archívumokra.

## Reprodukálás

```powershell
.\.venv\Scripts\python.exe verify_units.py
.\.venv\Scripts\python.exe -m tests.perception_mqtt_smoke
.\.venv\Scripts\python.exe -m experiments.verify_frontend
.\.venv\Scripts\python.exe -m experiments.verify_operations_v18
```

Az új egységtesztek a `tests/test_operations_search_v18.py`, a felületi regressziók a `tests/frontend_operations_search_v18.cjs` fájlban vannak. A valódi MQTT-kampány nyilvános HTTP-hibainjektálásból származó epizódokat kérdez le és vet össze a közvetlen SQLite-olvasásból, külön kiválasztási szabályokkal számolt referenciával. Nem hoz létre mesterséges incidenssort a kampány adatbázisában. A nyugtázási megjegyzés tartalmazza az ékezet-, Unicode- és szó szerinti különlegeskarakter-próbák szövegét. A régi 14 működési és riportellenőrzés megmarad; négy új pont a szűrést, lapozást, paraméterellenőrzést és visszaállítás utáni keresést vizsgálja.

Eredmények: `results/unit-summary.json`, `results/ui/perception-renderer-check-v18.json`, `results/operations-check-v18.json`, `results/ui/perception-ui-check-v18.json`, `results/PERCEPTION_MQTT_RENDSZERTESZT.json`. A `results/operations-proof-v18` mappa külön menti a keresett oldalakat és a független referenciát. A korábbi V17 jelentések és exportok eredeti verzióval, dátummal és változatlan tartalommal megmaradnak.

## Irodalom

[1] OPC Foundation, „OPC Unified Architecture – Part 9: Alarms & Conditions, 4.11 Alarm and Condition auditing,” verzió 1.05.06, OPC Foundation. [Online]. Elérhető: https://reference.opcfoundation.org/specs/OPC-10000-9/4.11 . Hozzáférés: 2026. október 11.
