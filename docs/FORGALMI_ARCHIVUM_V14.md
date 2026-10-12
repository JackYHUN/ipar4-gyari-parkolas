# Tartós forgalmi archívum és visszakeresés · V14

A 0.14.0 változat a V13 mért MQTT-forgalmi megfigyeléseit futásonként tartósan megőrzi. A fogadási és sorból kivételi ütem, a diagnosztikai sorhossz és a kijelzett állapot kora később is visszakereshető, akkor is, ha az élő 120 pontos ablakból már kikerült vagy új futás indult. A forgalmi archívum a teljes fogadott autóúttal és a szemantikus naplóval együtt segít megvizsgálni egy lemaradást; önmagában nem bizonyítja annak fizikai vagy hálózati okát.

## 1. Rendszerhatár és mérési alap

A rendszer továbbra is egy laptopon futó helyi TCP/MQTT-közvetítőből, külön szimulátor- és koordinátorfolyamatból, naplózó megfigyelőből és HTTP-műszerfalból áll. A megfigyelt forgalom a kijelző feliratkozójába érkező, érvényes, aktuális futású MQTT-üzenetekre vonatkozik. A számlálás minden feliratkozott témát tartalmaz; nem kizárólag a járműtelemetriát.

A mérés a fogadó gép monoton `perf_counter_ns` óráján történik. A ráta a számlálókülönbség és a ténylegesen eltelt pozitív időablak hányadosa. A célablak legfeljebb öt gazdagépmásodperc; a névleges mintavételi kapu egy másodperc. A megfigyelő kötege és a HTTP-állapotlekérés közös monitorhoz fordul, ezért egymástól függetlenül nem készíthetnek kettős mintát. A tényleges mintaköz hosszabb lehet egy másodpercnél, ütemezési vagy adathiány esetén pedig rés marad.

A 2× vagy 8× szimulációs sebesség növelheti az üzenettermelést, de nem szorozza meg a rátaszámítás nevezőjét. Az élő monitor legfeljebb 120 pontot tart memóriában; a V14 archívum az összes ténylegesen felvett diagnosztikai pontot külön őrzi. Egyik adatkör sem teljes MQTT-csomagrögzítés, és a ráta nem a rendszer igazolt maximális kapacitása.

## 2. Mentett adat és az órák értelmezése

A diagnosztikai pont a futás SQLite-naplójának `transport_samples` táblájába kerül. Az archivált rekord `id` azonosítója a mentési sorrendet, a `run_id` futásazonosító pedig a megfelelő adatbázist jelöli. Az adott mérési szakasz relatív `elapsed_s` ideje és a `segment` szakaszazonosító együtt értelmezendő: óra- vagy számláló-visszaállítás új mérési alapot kezdhet ugyanazon futáson belül. Új futásban az első szakasz azonosítója nulla. A grafikon nem köt össze eltérő szakaszokat és nem interpolál hiányzó megfigyelést.

| Adatkör | Jelentés és korlát |
| --- | --- |
| UTC-idő | A forgalmi megfigyelés mentésének emberileg kereshető gépi ideje. Nem egyetlen MQTT-üzenet fogadási callbackjének időbélyege. |
| `elapsed_s` és mérési szakasz | Az aktuális mérési alaphoz képest eltelt gazdagépidő. Más szakasz relatív idejével közvetlenül nem vonható ki. |
| `received_total`, `dequeued_total` | A mintapontkor megfigyelt callback-fogadási és sorból kivételi számláló. Nem a broker teljes üzenetforgalma. |
| `arrival_rate_hz`, `dequeue_rate_hz`, `window_s` | Üzenet/s és a számításhoz ténylegesen használt időablak másodpercben. Kezdőpont vagy elégtelen adat esetén ismeretlen. |
| `queue_messages`, `queue_growth_hz` | A diagnosztikai pontkor mért `qsize()` és annak időegységre jutó előjeles változása. Aktív callback mellett a sorhossz közelítő. |
| `snapshot_age_s` | A legutóbbi már feldolgozott MQTT-állapotcsomag gazdagépidő szerinti kora a megfigyeléskor. Nem a callback-frissesség vagy a szenzorminta modellbeli kora. |
| Mentett `sim_time` | A mintavételkor legutóbb feldolgozott állapot modellideje. Lemaradáskor korábbi állapothoz tartozhat; nem pontos MQTT-fogadási vagy fizikai mozgási idő. |
| `telemetry_until_id`, `events_until_id` | A megfigyeléskor már feldolgozott teljes autóút-archívum globális mintarekordjának, illetve a szemantikus napló utolsó sorának felső határa. Az összevetést segítik, de nem teszik a külön órákat azonossá és nem bizonyítanak oksági kapcsolatot. |

A `null` hiányzó vagy érvénytelen mennyiség. A ténylegesen mért nulla megmarad, és az apadó sor negatív változása is érvényes adat. Két másodpercnél nagyobb szomszédos mintarést a rátaablak nem hidal át. Óra- vagy számláló-visszaállítás nem törli a már archivált régi szakaszt. A nyers gyűrű rotációja és az élő 120 pontos ablak cserélődése sem törli a forgalmi archívumot.

## 3. Visszakeresés és rögzített határok

Az **Üzenetforgalom visszakeresése** nézetben futás választásával külön kezelhető a jelenlegi és egy korábban mentett futás. A böngésző korlátozott, legfeljebb 120 pontos ablakot mutat. A lapozás növekvő mentési rekordazonosítót használ; egy kiválasztáskor rögzített felső határ kizárja az olvasás közben érkező új pontokat. A rögzítés így a vizsgált szakaszt megőrzi, az élő forgalmi panel pedig közben továbbra is az aktuális futást követi. Új élő futás indítása nem váltja át a már kiválasztott archívumot.

1. Válassz mentett futást. A futáslista az élő és a korábbi futásokat megkülönbözteti; a futásazonosító önálló azonosításra is használható.
2. Az első oldal betöltése rögzíti a felső mintahatárt. A következő és előző oldalak ugyanazt a határt használják. Az **Első oldal / friss rögzítés** új felső határt kér, tehát külön, tudatos művelet.
3. A csúszkával válassz diagnosztikai pontot. A részletek a rögzítési UTC időt, a saját mérési szakasz idejét, a számlálókat, rátát, sorhosszt, feldolgozott állapot idejét és korát, valamint a kapcsolódó napló- és úthatárokat mutatják.
4. A CSV/JSON hivatkozások a kiválasztott futás teljes rögzített prefixét adják át. Az oldalszám és a 120 pontos grafikonablak csak a megjelenítést korlátozza.

Az összegző kártyák a teljes rögzített prefix megőrzött pontszámát, mérési szakaszainak számát, megfigyelt legnagyobb érkezési rátáját és sorhosszát mutatják. Ezek a felvett mintapontok szélsőértékei: rövidebb, két pont közötti csúcsot nem zárnak ki, és nem minősített kapacitásértékek. A két grafikon ezzel szemben az adott betöltött oldalt ábrázolja; vízszintes tengelyük a mentési rekordazonosító, nem időtengely. A szakaszon belüli monoton idő a kijelölt pont részleteiben olvasható. Sikertelen frissítéskor a korábbi oldal megmarad, külön sikertelen frissítési jelzéssel; a későn érkező, más kiválasztáshoz tartozó válasz nem írhatja felül az aktuális nézetet.

Az `/api/transport/history` végpont `run_id`, kizáró `after`, `limit` és befoglaló `until_id` paraméterekkel kérdezhető. A backend egy oldalon legfeljebb 500 rekordot ad át. A válasz `schema_version=1`, `scope=recorded_host_clock_transport_observations`, `clock=host_perf_counter` mezőket, `run_id`, `available`, `availability`, `until_id`, `total_count`, `points`, `has_more`, `next_after` és a teljes rögzített prefix `summary` összegzését tartalmazza. Hiányzó `until_id` esetén a kérés az akkori legújabb mentett rekordot rögzíti. A `/api/transport/export` ugyanebből a futásból `format=csv` vagy `format=json` és opcionális `until_id` alapján adja át a rögzített prefixet. A korlátozott böngészőablak nem azonos az exportált teljes mentett prefixszel.

Régi, V13-as vagy korábbi adatbázisban nem volt tartós forgalmi mintatábla. Ilyenkor a csak olvasható történeti API `available=false`, `availability=not_recorded` választ ad. Az újabb, már megfelelő táblát tartalmazó, de még pont nélküli futás `available=true`, `availability=empty`; mentett ponttal a jelzés `recorded`. A korábbi történet nem állítható helyre a jelenlegi memóriabeli monitorból vagy a járműtelemetria alapján. A hiányt külön kell értelmezni; üres történetből nem következik, hogy a korábbi forgalom vagy sorhossz nulla volt.

## 4. Export, megőrzés és reprodukálhatóság

A futás ZIP-exportja a szemantikus napló, a megőrzött részletes telemetria, a teljes fogadott autóút és a konfiguráció mellett `transport.csv` és `transport.json` fájlokban a tartós forgalmi megfigyeléseket is tartalmazza. A `metadata.export_snapshot` forgalmi mezői: `transport_until_id`, `transport_schema_version`, `transport_available`, `transport_members`. Az export független SQLite-olvasási pillanatképet használ: a minták határa és a metaadatok egy rögzített prefixhez tartoznak, miközben az élő megfigyelő folytathatja az írást. A letöltés nem változtatja meg a mozgásvezérlést.

Reset az előző futás adatbázisát lezárja, az új futásnak új azonosítót és új adatbázist ad. A korábbi fájlok megmaradnak, és a futáskatalógusból visszakereshetők. A böngésző- és élőmemória-korlát nem jelent teljes lemezméret-korlátot: a tartós minták, indexek és WAL növelhetik a futáskönyvtár méretét. A gép vagy a program váratlan leállásakor a még véglegesítetlen írások elveszhetnek; a lekérdezés, az export és a rendezett lezárás véglegesíti a függő naplóírásokat.

A reprodukáláshoz megőrzendő a kódverzió, a függőséglista, a teljes futáskonfiguráció, a véletlenmag, a futásazonosító, az exporthatárok és a vizsgált jelentésben rögzített forráslenyomat. Az üzenet/s számok megismételt helyi futásban eltérhetnek a gazdagép terhelése és ütemezése miatt. A teljes prefix összegzése korlátozott memóriával, az összes mentett pontot végigolvasva készül; a lekérdezési idő hosszabb előzménynél növekedhet. A V14 ehhez nem ad új gyorsulási állítást. A V13 véges forgalmi kampány és a V12 profil saját eredeti dátumával, verziójával és hatókörével történeti bizonyíték marad; a V14 új archiválási viselkedését külön kell ellenőrizni.

## 5. Követelmény–megvalósítás–bizonyíték

| Követelmény | Megvalósítás | Ellenőrzés és korlát |
| --- | --- | --- |
| Egy lemaradás később is visszakereshető legyen. | A tényleges diagnosztikai pontok futásonként tartósan tárolódnak, az élő 120 pontos ablaktól függetlenül. | Az ellenőrzésnek az ablakból kieső rekordok, az újranyitás és a reset utáni régi futás adatait is össze kell vetnie. |
| Olvasás közben ne csússzon el a vizsgált adatkészlet. | Kizáró lapozási kurzor és befoglaló, rögzített felső rekordhatár; külön élő és archív nézet. | Új pont beszúrásával ellenőrizendő, hogy a rögzített prefix változatlan marad. |
| Hibás vagy hiányzó időből ne keletkezzen látszólagos mérés. | A `null` és valódi nulla megkülönböztetése, mérési szakaszok és mintarések kezelése. | Óra-/számláló-visszaállítás és hiányzó érték célzott regressziója; a különböző órákból nem számolható egyetlen fizikai reakcióidő. |
| A jelentés eredményei önállóan ellenőrizhetők legyenek. | CSV/JSON prefixexport, SQLite-pillanatkép, futás-ZIP, konfiguráció és verziózott forráslenyomat. | Az API, az adatbázis és a ZIP azonos rekordjainak mezőnkénti összevetése; a siker nem általános MQTT-veszteségmentességi garancia. |

## 6. Ellenőrzési eredmények

A végleges 0.14.0 forráson **307/307 Python-teszt** és **152 JavaScript-ellenőrzés** sikeres. A tesztkészlet 18 új archiválási regressziót tartalmaz: tartós újranyitás, a memóriabeli kereten túli megőrzés, rögzített lapozás, nulla és ismeretlen adat, óraszakasz-váltás, hibás időérték, sérült archívum, csak olvasható régi napló, HTTP-hibakezelés és exportkonzisztencia. Bizonyítékok: [Python-jelentés](../results/unit-summary.json), [frontend-jelentés](../results/ui/perception-renderer-check-v14.json).

A [valódi MQTT-archiválási kampány](../results/transport-archive-check-v14.json) nyolc sikeres ellenőrzést rögzít a `65e56231022e` futáshoz. A végső rögzített prefix 133 pontot tartalmaz; 132 jelentett ráta függetlenül újraszámításra került. A vizsgálat tényleges gazdagépidőben átlépte a 120 pontos memóriaablakot, lapozott, CSV/JSON-ot és érvényes CRC-jű futás-ZIP-et exportált, HTTP-reset után visszaolvasta a régi futást, majd új `Application` példány és csak olvasható SQLite-kapcsolat alatt is összevetette az előzményt. A 150,92 s hosszú kampány a saját konfigurációjára és gazdagépére vonatkozik; nem tartós maximális kapacitásmérés.

A [külön forgalmi és helyreállítási kampány](../results/transport-capacity-check-v14.json) öt sikeres ellenőrzést és 66 HTTP-állapotmegfigyelést tartalmaz. A 12 autós próba feldolgozói szünete alatt 2940 üzenetes sor került rögzítésre; a helyreállítás után a sor kiürült. Az archiválási kampányban külön 756 üzenetes sor volt megfigyelhető. A két eredmény külön futáshoz és konfigurációhoz tartozik, nem összevonható kapacitáshatár.

A [tényleges asztali böngészős ellenőrzés](../results/ui/perception-ui-check-v14.json) hét sikeres esetet rögzít. A 189 pontos előzmény 120 és 69 pontos oldalakra bontva maradt böngészhető; az új élő futás indítása megtartotta a régi futás #189 mintahatárát, kijelölt megfigyelését és exportlinkjét. A kiválasztott pont UTC-je és telemetria-/eseményhatára a helyi HTTP-rekorddal egyezett. A régi V13 futás hiánya nem vált nulla forgalommá. A vizsgált alapértelmezett asztali nézetben nem volt vízszintes dokumentumtúlfutás vagy rögzített konzolfigyelmeztetés/-hiba; új mobil- vagy natív letöltési igazolás nem készült.

Az aktuális [MQTT-érzékelési és teljesút-próba](../results/PERCEPTION_MQTT_RENDSZERTESZT.json) külön igazolja a 2486 CAR001-keretet, ebből 60 elavult és 2426 külön eredeti mintaidőt, két befejezett parkolással és nulla modellbeli ütközéssel. A verzió- és forráslenyomat-kapukat a `record_verification_v14.py`, a csomag bájt-, manifest-, export- és bizonyítékegyezését az `experiments/verify_package_v14.py` ellenőrzi.

A forgalmi archívum mérnöki bizonyítékként használható a beadandóban. A végleges leadáshoz továbbra is szükséges az oktató számára elérhető kódtár, a rendszer működését bemutató videó és a formai követelményeknek megfelelő, számozott oldalakkal, IEEE-hivatkozásokkal és kereszthivatkozott ábrákkal készült műszaki PDF.
