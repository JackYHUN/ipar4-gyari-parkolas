# Forgalmi mintához kapcsolt fogadott bizonyíték · V15

A 0.15.0 változat a mentett üzenetforgalmi megfigyelést összekapcsolja az akkor már feldolgozott járműpozíciókkal és a szemantikus eseményekkel. Egy korábbi sorfelhalmozódás kiválasztásakor így ugyanahhoz a futáshoz és ugyanahhoz a mentett rekordhatárhoz tartozó autóállapot vizsgálható. A kiválasztott autó utolsó, a határon belüli fogadott mintája a meglévő teljesút-visszajátszóban is megnyitható.

## 1. Mérnöki probléma és rendszerhatár

Az élő műszerfal és egy régi forgalmi csúcs külön időpontot mutathat. Ha a felhasználó egy korábbi diagnosztikai mintát az aktuális autóállapottal értelmezne, később érkezett adat is a régi megfigyelés mellé kerülhetne. A V15 ezért a mintához mentett globális telemetria- és eseményazonosítókat használja közös felső rekordhatárként.

A vizsgált rendszerhatár a laptopon futó szimulátor, MQTT-közvetítő, koordinátor, naplózó feliratkozó és HTTP-műszerfal. A kiberfizikai kapcsolatot a járművek, az úthálózat, a gyalogosátkelők és az érzékelők deklarált szimulációs modellje adja; a teszt nem valós autógyári jármű vagy fizikai érzékelő minősítése. A V15 olvasási funkció: nem ad új mozgásparancsot, és az adatköröket az eredeti fogadott archívumból keresi vissza.

## 2. Közös rekordhatár és külön órák

A kiválasztott `transport_samples.id` azonosító egy ténylegesen mentett gazdagépi diagnosztikai pontot jelöl. A pont `telemetry_until_id` és `events_until_id` mezői a mentéskor már feldolgozott járműarchívum és szemantikus napló globális felső rekordhatárai. A lekérdezés az előbbi határon belül minden autó legnagyobb fogadott rekordazonosítójú pozícióját adja vissza, az utóbbi határon belül pedig a mentett eseményeket lapozza.

| Mennyiség | Értelmezés |
| --- | --- |
| Diagnosztikai `utc` | A forgalmi megfigyelés mentési ideje a gazdagép UTC-óráján. |
| `segment`, `elapsed_s` | A forgalmi mérés szakasza és annak monoton gazdagépi ideje. |
| Diagnosztikai `sim_time` | A mintavételkor utoljára feldolgozott állapot modellideje; lemaradáskor korábbi érték lehet. |
| Járműpozíció `utc`, `sim_time` | Az adott fogadott pozíció saját fogadási UTC-ideje és külön modellideje. Nem kerül átírva a diagnosztikai pont idejére. |
| Globális felső rekordhatárok | A már feldolgozott archívum körülhatárolására szolgálnak. Nem eseményenkénti fizikai időbélyegek. |
| `position_source` | A kiválasztott fogadott rekordban mentett pozícióforrás; hiánya ismeretlen marad. |

A közös rekordhatár azt bizonyítja, hogy az adat a kiválasztott diagnosztikai pontkor már rendelkezésre állt a megfigyelő archívumában. Eltérő járművek utolsó rekordjai különböző modellidőhöz tartozhatnak. A rekordok együtt megjelenítése ezért nem bizonyít fizikai egyidejűséget, az események időbeli közelsége pedig nem bizonyítja a sorfelhalmozódás okát. A fogadási callback számlálója lemaradáskor tovább növekedhet, miközben a feldolgozott telemetriahatár változatlan; ez megőrzött mérési állapot.

## 3. API, lapozás és adatkorlátok

Az `/api/transport/context` végpont kötelező `sample_id` és opcionális, explicit `run_id` alapján keresi ki a mentett pontot. A böngésző mindig átadja a kiválasztott futás azonosítóját. Az autóoldal `cars_after` kizáró, növekvő autóazonosító-kurzort, az eseményoldal `events_before` kizáró, csökkenő rekordazonosító-kurzort használ. Az alapértelmezett `cars_limit` és `events_limit` 25; a szerver mindkét adatkörnél legfeljebb 100 sort ad vissza.

A válasz `schema_version=1` és `scope=received_evidence_at_transport_sample` mezőkkel azonosítja a szerződést. Tartalmazza a futás azonosítóját, az eredeti teljes `sample` rekordot, a két felső rekordhatárt, valamint a futás mentett térképét, konfigurációját és metaadatait. A `vehicles` objektum elemei skalár pozíció-, állapot- és szekvenciaadatok; a `total_count` a felső határon belül már megfigyelt autók száma. A `has_more` és `next_after` a következő autóoldalt jelzi. Az `events` objektum `items`, `total` és `next_before` mezői a határon belüli szemantikus naplóhoz tartoznak.

A teljes fogadott úttábla esetén a `vehicles.source` értéke `trip_samples`. A `complete_received_archive` a korábban elveszett mintákra vonatkozó, tárolt archívumkorlátot jelzi; a `prior_missing_samples_global` globális darabszám, nem az adott autó elveszett mintáinak száma. A teljesség a ténylegesen archivált, fogadott adatokra vonatkozik, nem az összes termelői MQTT-üzenet veszteségmentességének igazolása.

Régi, teljesút-tábla nélküli napló csak a megmaradt részletes telemetriát adhatja vissza: `source=legacy_retained_telemetry`, `complete_received_archive=false`, a nem tárolt skalár mezők pedig ismeretlenek maradnak. Tartós forgalmi tábla vagy a kért mintarekord hiányakor a kontextus nem rekonstruálható; a szerver hibával válaszol. A hiány nem nulla autót, nulla forgalmat vagy nulla eseményt jelent.

Az autó- és eseménykurzornak a kiválasztott minta előzményében ténylegesen létező rekordhoz kell tartoznia. Később létrejött autó vagy a felső határnál későbbi esemény lapozási kurzorként nem fogadható el. Egy csak olvasható SQLite-pillanatkép a mentett mintát és a két adatkört ugyanabban az olvasásban fogja össze. A válasz mérete oldalanként korlátozott; az összesítés és a legutóbbi autóminták SQL-lekérdezése hosszabb előzménynél több munkát igényelhet. Állandó válaszidőre nincs új garancia.

## 4. Visszajátszás és érzékelési bizonyíték

A kiválasztott autó az `/api/car/history/page` végponton `direction=end`, a diagnosztikai pont globális `until_id` és `events_until_id` határaival nyitható meg. A végpont a kért plafont az adott autó teljes archívumában elérhető maximumhoz korlátozza; ez a plafon nagyobb lehet a prefixen belüli utolsó autórekord azonosítójánál. A célrekordot külön ellenőrzés köti a kontextusban feltüntetett pontos járműmintához. Újabb pozíció vagy esemény nem kerülhet a vizsgált prefixbe.

A visszajátszó a kiválasztott fogadott mintához mentett érzékelőadatokat és vezérlési diagnosztikát használja. A szintetikus érzékelési grafika továbbra is a mentett megfigyelések sematikus megjelenítése; a rendszer nem tárol fizikai kameraképet vagy videófolyamot. A V15 skalár kontextusoldala önmagában nem bontja ki a teljes tömörített szenzorcsomagokat.

A meglévő teljesút-API a prefixadatok mellett külön `current` élő állapotot és teljesarchívum-számlálókat is átadhat. Ezek megváltozhatnak, miközben a rögzített útvonalprefix változatlan marad. A vizsgálat és a visszajátszó ezért a rögzített `telemetry`, `events`, `target_id`, `window`, `overview` és `mission` adatok alapján értékeli a kiválasztott régi állapotot.

Az archív pont részletei alatt a **Járművek és események megnyitása →** gomb tölti be a **Mi állt rendelkezésre ekkor?** panelt. A **Legutóbb fogadott járműállapotok** és **Utolsó beérkezett események** táblázatok legfeljebb 25 sort mutatnak; saját előző/következő, illetve régebbi/újabb lapozásuk van. Az autó sorában az **Út és érzékelők →** gomb a megfelelő régi célrekordot nyitja meg.

A kontextusból nyitott visszajátszás rögzített prefixet mutat, ezért a korábbi teljesfutás-CSV/JSON exportlinkek ilyenkor nem aktívak: azok jelenlegi API-ja a teljes megőrzött futást exportálná. Az **Új minták betöltése · határ bővítése** külön felhasználói műveletként új rekordhatárt kér, és visszaállítja a teljesfutás-export lehetőségét. Az élő futás resetje nem cseréli le automatikusan a kiválasztott régi kontextust.

## 5. Követelmény–megvalósítás–bizonyíték

| Követelmény | Megvalósítás | Ellenőrzés és korlát |
| --- | --- | --- |
| Egy régi lemaradás mellé csak az akkor már feldolgozott adatok kerüljenek. | A mentett diagnosztikai pont globális fogadott telemetria-/eseményhatárai, autóként legutóbbi skalár rekord. | Valódi MQTT alatt korai kétjárműves pont és később gyártott autó összevetése, független csak olvasható SQL-reference. |
| Lapozás közben az adatkör maradjon rögzített és korlátozott. | Autó- és eseménylapozás saját kizáró kurzorral, oldalanként legfeljebb 100 sor. | Egyautós és háromeseményes HTTP-oldalak teljes prefixének pontos, ismétlődés nélküli összevetése a közvetlen SQL-sorokkal. |
| A járműút a megfelelő régi rekordnál nyíljon meg. | A meglévő visszajátszás globális határokkal, autóra szűkített célrekorddal. | A végső visszajátszási rekord, a skalár pozíció, a szemantikus eseményhatár és a későbbi frissülés összevetése. |
| Reset és újranyitás után az előzmény ne kapcsolódjon az új futáshoz. | Explicit futásazonosító és mentett konfiguráció, független SQLite-olvasás. | Valódi HTTP-reset, friss `Application`, külön csak olvasható `Journal`, közvetlen SQLite-újranyitás. |
| Az igazolás azonosítható forrásból származzon. | Verzió, időbélyeg, konfiguráció, kódlenyomat és a hordozható JSON-bizonyítékok lenyomata. | A kampány előtti/utáni forráslenyomat egyezése; a régi mérési jelentések saját verziójukon maradnak. |

## 6. Reprodukálható vizsgálat

A kampány indítása a projekt könyvtárából:

```text
.venv\Scripts\python.exe -m experiments.verify_transport_context_v15
```

A vizsgálat saját ideiglenes helyi MQTT-porton, külön szimulátorral, koordinátorral és feliratkozóval indul; a futó bemutató vezérlését nem használja. A forgatókönyv két kezdeti autót, legfeljebb hat autót, 16 parkolóhelyet, 8 modellmásodperces gyártási intervallumot, 600 modellmásodperces parkolási időt, 95-ös magot és 2× modellsebességet alkalmaz. A feldolgozó kísérleti szünete legfeljebb nyolc gazdagépmásodpercig tarthat; a vizsgálat három egymás utáni, tényleges gazdagépidőben felvett megfigyelést kér. A szünet a programzáron kívül történik, ezért az MQTT-callback és a HTTP-megfigyelés közben is működik. Ez teszteszköz, nem üzemi funkció.

A tényleges mérés eredménye a `results/transport-context-check-v15.json` jelentésbe kerül. A `results/transport-context-proof-v15` könyvtár a mentett kontextust, az autó- és eseményoldalakat, a kiválasztott visszajátszási oldalt, a lemaradáskor kért kontextust és a független SQL-reference adatokat tartalmazza. A jelentés csak sikeres végrehajtás és változatlan forráslenyomat után kaphat `PASS` státuszt. Az eredmény az adott véges helyi kampány fogadott rekordjaira vonatkozik; maximális kapacitás, valós gyári biztonság vagy fizikai érzékelési pontosság ebből nem állapítható meg.

## 7. Ellenőrzési eredmények

A [valódi MQTT/HTTP-kampány](../results/transport-context-check-v15.json) a végleges **0.15.0** forráson, **2026. október 11-én 13:29:20 UTC** lezárással, **hét sikeres ellenőrzést** rögzített. A `a250ac0edad0` futás véges vizsgálata 11,75 gazdagépmásodpercig tartott és 59 HTTP-kérést tartalmazott. A mért kérések helyi mediánja 32 ms, 95. percentilise 78 ms volt; ebben reset és diagnosztikai lekérés is szerepelt, ezért az érték nem általános válaszidő- vagy kapacitásgarancia.

A kiválasztott **#2** diagnosztikai rekord mentési ideje `2026-10-11T13:29:11.048Z`, gazdagépi eltelt ideje 1,0067806 s és legutóbb feldolgozott modellideje 1,5 s volt. Globális telemetriahatára **#32**, szemantikus eseményhatára **#42**. A független, csak olvasható SQL-lekérdezés a `CAR001` **#31** és a `CAR002` **#32** utolsó fogadott rekordját találta. Az API két egyautós oldala pontosan ugyanezeket a skalár adatokat adta át; a 42 esemény 14 háromeseményes oldalon, duplikáció és kimaradás nélkül egyezett a közvetlen SQL-adatokkal.

A visszajátszás `CAR001` célrekordja **#31** volt. A később ténylegesen gyártott `CAR003` és a későbbi **#114** esemény nem bővítette a régi kontextust; a prefixen kívüli autó-/eseménykurzor és a nem létező mintarekord HTTP 400 választ kapott. A fogadott útvonal-, esemény-, áttekintő és útösszegző adatok az új rekordok érkezése után is egyeztek az eredeti prefixszel. A visszajátszási API külön élő állapotának és teljesarchívum-számlálóinak változatlanságát a kampány nem állítja.

A rövid kísérleti feldolgozói szünet alatt a **#8** mentett diagnosztikai pont **326 üzenetes sort** és **3,3138051 s** feldolgozottállapot-kort rögzített. A pont modellideje 9,15 s, telemetriahatára **#202**, eseményhatára **#126** maradt, miközben a callback-fogadási számláló 825, a sorból kivételi számláló 498 volt. Helyreállításkor 18,05 s modellidő és nulla megfigyelt sor állt rendelkezésre; a régi #8 kontextus továbbra is ugyanazokat a korábban feldolgozott sorokat adta át. A két számláló, a közelítő sorhossz és a mentett rekordhatárok eltérő megfigyelési pontokhoz tartoznak, ezért az adatokból nem számolható minden üzenetre kiterjedő veszteségmérés.

A valódi HTTP-reset új `aeea9fe5861b` futást indított. Az explicit régi futásazonosítóval kért #2 kontextus és a régi visszajátszási prefix megmaradt; az eredeti folyamatok leállítása után friss `Application`, külön csak olvasható `Journal` és közvetlen SQLite-újranyitás alatt is egyezett a bizonyíték. A kampány előtti és utáni vizsgált forráslenyomatok azonosak. A [hordozható bizonyítékkönyvtár](../results/transport-context-proof-v15) hat JSON-állománya a tényleges válaszokat és a SQL-reference adatokat őrzi; mindegyik SHA-256 lenyomata szerepel a jelentésben.

A V14 archiválási és a korábbi terhelési/profilozási jelentések eredeti verziójukhoz és konfigurációjukhoz tartoznak. Ezek nem helyettesítik a V15 új korrelációs viselkedésének fenti, külön végrehajtott igazolását.

A végleges háttérrendszer teljes tesztcsomagja **325/325 Python-tesztet**, a felület ellenőrzése **175/175 JavaScript-esetet** teljesített. A külön érzékelési MQTT/HTTP-kampány 2486 fogadott autókeretet, 2426 eredeti érzékelőmintát és 60 elavult hibakori keretet ellenőrzött; két autó beparkolt, referenciaütközés nélkül. A tesztelt Python-források a teljes futtatás alatt változatlanok maradtak.

A [külön böngészős vizsgálat](../results/ui/perception-ui-check-v15.json) **tíz sikeres ellenőrzést** rögzít az alkalmazás tényleges, alapértelmezett **663 × 815 px** oldalsáv-nézetében. Az ismeretlen kezdő modellidő, a járműrekordok, a 25/17 eseményes lapozás, a pontos #31 út- és szenzorugrás, a külön manuális határbővítés, az új élő futástól független régi kontextus és az újbóli rögzített ugrás is szerepel benne. A keskeny nézet kártyákban mutatja az autókat, teljes szélességű megnyitási gombokkal; dokumentumszintű vízszintes túlcsordulás nem volt. Konzolfigyelmeztetés és hiba nem jelent meg, a tíz vizsgált háttérrendszer-/felületi forráslenyomat nem változott. Ez a nézet saját jelenlegi bizonyítéka; korábbi 1280 px-es vagy mobileredményt nem állít újra. A [végső képernyőkép](../results/ui/diagnosztikai_kontextus_v15.jpg) lenyomatát is őrzi a jelentés.
